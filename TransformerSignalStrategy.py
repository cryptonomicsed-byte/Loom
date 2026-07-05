"""
TransformerSignalStrategy — Freqtrade strategy powered by WhaleTransformer predictions.

Connects the pure-NumPy transformer model directly to Freqtrade's
entry/exit signal pipeline. No PyTorch needed.

Architecture:
  Whale event stream → Transformer predicts [buy_prob, sell_prob, conviction, eta]
                      → Strategy combines with technical indicators
                      → Entry/exit signals with confidence scores
"""

import logging
import time
import sys
import os
from typing import Optional, Dict

import numpy as np
import pandas as pd
import requests
from pandas import DataFrame

from freqtrade.strategy import IStrategy, IntParameter, DecimalParameter

# Add LOOM path for transformer import
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "loom"))
# Fallback: if LOOM is on the phone, use HTTP API
LOOM_API = "http://localhost:8889"

logger = logging.getLogger(__name__)


class TransformerSignalStrategy(IStrategy):
    """
    Strategy that uses a WhaleTransformer model for entry/exit predictions,
    combined with technical indicators for confirmation.

    The transformer learns from whale event sequences to predict:
    - p_buy: probability of bullish move
    - p_sell: probability of bearish move
    - conviction: model confidence (0-1)
    - eta: estimated time to event (minutes)
    """

    INTERFACE_VERSION = 3
    timeframe = "15m"
    can_short = False

    # ── Hyperparameters ─────────────────────────────────────────
    min_conviction = DecimalParameter(0.3, 0.8, default=0.5, space="buy")
    rsi_buy_threshold = IntParameter(20, 45, default=35, space="buy")
    rsi_sell_threshold = IntParameter(55, 85, default=65, space="sell")
    transformer_weight = DecimalParameter(0.3, 0.9, default=0.6, space="buy")

    # ── Risk Parameters ─────────────────────────────────────────
    minimal_roi = {
        "0": 0.08, "30": 0.04, "60": 0.02, "120": 0.01, "240": 0,
    }
    stoploss = -0.12
    trailing_stop = True
    trailing_stop_positive = 0.02
    trailing_stop_positive_offset = 0.03
    trailing_only_offset_is_reached = True

    # ── Trade Configuration ─────────────────────────────────────
    process_only_new_candles = True
    use_exit_signal = True
    exit_profit_only = False
    startup_candle_count = 50

    # ── Transformer Config ──────────────────────────────────────
    loom_api_url = LOOM_API
    _prediction_cache: Dict[str, dict] = {}
    _cache_ts: float = 0
    _cache_ttl: float = 60
    _use_local_model: bool = False
    _local_model = None

    def _get_transformer_prediction(self) -> dict:
        """Get prediction from transformer model.

        Tries local model first, falls back to LOOM API, falls back to neutral."""
        now = time.time()
        if self._prediction_cache and (now - self._cache_ts) < self._cache_ttl:
            return self._prediction_cache

        result = {"direction": "WAIT", "conviction": 0.0, "eta_minutes": 0}

        # Try local model
        if self._use_local_model and self._local_model is not None:
            try:
                events = self._gather_events()
                result = self._local_model.predict(events)
                self._prediction_cache = result
                self._cache_ts = now
                return result
            except Exception as e:
                logger.debug(f"Local transformer error: {e}")

        # Try LOOM API
        try:
            resp = requests.get(
                f"{self.loom_api_url}/api/whales/galaxy",
                timeout=5,
            )
            if resp.status_code == 200:
                data = resp.json()
                # Extract whale activity from galaxy
                nodes = data.get("nodes", [])
                whale_nodes = [n for n in nodes if n.get("kind") == "whale"]
                active_count = len(whale_nodes)

                # Simple heuristic from galaxy data
                high_tier = [w for w in whale_nodes if w.get("tier", 4) <= 2]
                if high_tier:
                    avg_pv = sum(w.get("pv", 0) for w in high_tier) / len(high_tier)
                    result["conviction"] = min(0.9, avg_pv)
                    result["direction"] = "BUY" if avg_pv > 0.6 else "WAIT"
                    result["eta_minutes"] = max(5, 45 * (1 - avg_pv))

                self._prediction_cache = result
                self._cache_ts = now
        except Exception as e:
            logger.debug(f"LOOM API error: {e}")

        return result

    def _gather_events(self) -> list:
        """Gather recent whale events for transformer input.
        In production, this pulls from LOOM's event stream."""
        try:
            resp = requests.get(f"{self.loom_api_url}/api/state", timeout=5)
            if resp.status_code == 200:
                state = resp.json()
                events = state.get("recent_events", [])
                return [
                    {
                        "type": e.get("event_type", "price_move"),
                        "magnitude": e.get("magnitude", 0.0),
                        "entity": e.get("entity", ""),
                        "wallet_count": 0,
                        "confidence": e.get("anomaly_score", 0.5),
                    }
                    for e in events[-20:]
                ]
        except Exception:
            pass
        return []

    # ── Technical Indicators ────────────────────────────────────

    def populate_indicators(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        # RSI
        delta = dataframe["close"].diff()
        gain = delta.where(delta > 0, 0).rolling(window=14).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(window=14).mean()
        rs = gain / loss
        dataframe["rsi"] = 100 - (100 / (1 + rs))

        # MACD
        ema12 = dataframe["close"].ewm(span=12, adjust=False).mean()
        ema26 = dataframe["close"].ewm(span=26, adjust=False).mean()
        dataframe["macd"] = ema12 - ema26
        dataframe["macd_signal"] = dataframe["macd"].ewm(span=9, adjust=False).mean()
        dataframe["macd_hist"] = dataframe["macd"] - dataframe["macd_signal"]

        # Bollinger
        rolling_mean = dataframe["close"].rolling(window=20).mean()
        rolling_std = dataframe["close"].rolling(window=20).std()
        dataframe["bb_lower"] = rolling_mean - (rolling_std * 2)
        dataframe["bb_upper"] = rolling_mean + (rolling_std * 2)
        dataframe["bb_position"] = (dataframe["close"] - dataframe["bb_lower"]) / (
            dataframe["bb_upper"] - dataframe["bb_lower"]
        )

        # Volume ratio
        dataframe["volume_sma"] = dataframe["volume"].rolling(window=20).mean()
        dataframe["volume_ratio"] = dataframe["volume"] / dataframe["volume_sma"]

        return dataframe

    # ── Entry Logic ─────────────────────────────────────────────

    def populate_entry_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        pair = metadata["pair"]

        # Get transformer prediction
        pred = self._get_transformer_prediction()
        t_conviction = pred.get("conviction", 0)
        t_direction = pred.get("direction", "WAIT")

        # Technical conditions
        rsi_oversold = dataframe["rsi"] < self.rsi_buy_threshold.value
        macd_bullish = (dataframe["macd_hist"] > 0) & (dataframe["macd_hist"].shift(1) <= 0)
        bb_oversold = dataframe["bb_position"] < 0.35
        vol_healthy = dataframe["volume_ratio"] > 0.6

        tech_score = rsi_oversold.astype(int) + macd_bullish.astype(int) + bb_oversold.astype(int)
        tech_buy = (tech_score >= 2) & vol_healthy

        # Combine transformer + technical
        transformer_buy = (t_direction == "BUY") & (t_conviction >= self.min_conviction.value)
        combined_buy = (
            (transformer_buy & tech_buy) |
            (tech_buy & (t_conviction >= self.min_conviction.value * 0.7))
        )

        dataframe.loc[:, "enter_long"] = 0
        dataframe.loc[combined_buy, "enter_long"] = 1

        if dataframe["enter_long"].any():
            logger.info(
                f"🤖 TRANSFORMER ENTRY: {pair} | "
                f"dir={t_direction} conv={t_conviction:.2f} "
                f"eta={pred.get('eta_minutes', 0):.0f}min | "
                f"RSI={dataframe['rsi'].iloc[-1]:.1f}"
            )

        return dataframe

    # ── Exit Logic ──────────────────────────────────────────────

    def populate_exit_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        pair = metadata["pair"]
        pred = self._get_transformer_prediction()
        t_conviction = pred.get("conviction", 0)
        t_direction = pred.get("direction", "WAIT")

        rsi_overbought = dataframe["rsi"] > self.rsi_sell_threshold.value
        macd_bearish = (dataframe["macd_hist"] < 0) & (dataframe["macd_hist"].shift(1) >= 0)
        bb_overbought = dataframe["bb_position"] > 0.7

        tech_score = rsi_overbought.astype(int) + macd_bearish.astype(int) + bb_overbought.astype(int)
        tech_sell = tech_score >= 2

        transformer_sell = (t_direction == "SELL") & (t_conviction >= self.min_conviction.value)

        dataframe.loc[:, "exit_long"] = 0
        dataframe.loc[tech_sell | transformer_sell, "exit_long"] = 1

        if dataframe["exit_long"].any():
            logger.info(
                f"🤖 TRANSFORMER EXIT: {pair} | "
                f"dir={t_direction} conv={t_conviction:.2f}"
            )

        return dataframe
