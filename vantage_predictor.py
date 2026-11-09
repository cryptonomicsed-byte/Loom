#!/usr/bin/env python3
"""
VantagePredictor — Bridges LOOM's whale transformer to Vantage's signal API.

Architecture:
  WhaleTransformer → Predict (direction, conviction, eta)
  CoinGecko → Current price data
  ↓
  Format as Vantage signal → POST /api/trading/signals/ingest
  ↓
  Vantage agents consume signal → react, debate, trade
"""

import json
import time
import threading
import urllib.request
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from transformer_predictor import get_model, predict_from_events

# ── Config ────────────────────────────────────────────────────

# P1-05: was hardcoded to 2.25.70.156 (a retired host).
VANTAGE_URL = os.environ.get("VANTAGE_URL", "http://127.0.0.1:8001")
VANTAGE_KEY = ""

def _load_key():
    try:
        key_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".vantage_key")
        with open(key_file) as f:
            return f.read().strip()
    except Exception:
        try:
            with open(os.path.expanduser("~/.vantage_key")) as f:
                return f.read().strip()
        except Exception:
            return os.environ.get("VANTAGE_API_KEY", "")
COINGECKO_URL = "https://api.coingecko.com/api/v3/simple/price"

# Tracked symbols — maps our entity names to Vantage symbols
TRACKED_SYMBOLS = {
    "SOL": "SOL/USDC",
    "BTC": "BTC/USD",
    "ETH": "ETH/USD",
    "BONK": "BONK/USDC",
    "WIF": "WIF/USDC",
    "POPCAT": "POPCAT/USDC",
    "MYRO": "MYRO/USDC",
    "TATE": "TATE/USDC",
    "NEIL": "NEIL/USDC",
    "SAMO": "SAMO/USDC",
    "MEW": "MEW/USDC",
}


class VantagePredictor:
    """Transformer-powered predictor that posts signals to Vantage."""

    def __init__(self, vault_events_callback=None):
        self.model = get_model()
        self.vantage_url = VANTAGE_URL.rstrip("/")
        self.vantage_key = _load_key()
        self.session = self._build_session()
        self._events_cb = vault_events_callback
        self._last_signals: dict = {}  # {symbol: last_signal}
        self._signal_cooldown: int = 300  # seconds between signals per symbol

    def _build_session(self):
        """Build opener with auth headers if key is set."""
        opener = urllib.request.build_opener()
        if self.vantage_key:
            opener.addheaders = [
                ("X-Agent-Key", self.vantage_key),
                ("Content-Type", "application/json"),
            ]
        return opener

    # ── Data Fetching ──────────────────────────────────────────

    def get_price(self, symbol: str) -> dict:
        """Get current price for a symbol from CoinGecko."""
        try:
            # Map to coingecko id
            cg_id = symbol.split("/")[0].lower()
            url = f"{COINGECKO_URL}?ids={cg_id}&vs_currencies=usd&include_24hr_change=true"
            req = urllib.request.Request(url, headers={"User-Agent": "VantagePredictor/1.0"})
            with urllib.request.urlopen(req, timeout=5) as resp:
                data = json.loads(resp.read())
                token_data = data.get(cg_id, {})
                return {
                    "price": token_data.get("usd", 0),
                    "change_24h": token_data.get("usd_24h_change", 0),
                    "symbol": symbol,
                }
        except Exception:
            return {"price": 0, "change_24h": 0, "symbol": symbol}

    def get_events(self) -> list:
        """Gather recent events for transformer input."""
        if self._events_cb:
            return self._events_cb()

        # Fallback: query LOOM API on localhost
        try:
            req = urllib.request.Request("http://localhost:8889/api/state", headers={"User-Agent": "VP/1.0"})
            with urllib.request.urlopen(req, timeout=3) as resp:
                state = json.loads(resp.read())
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

    # ── Prediction ─────────────────────────────────────────────

    def predict(self, symbol: str) -> dict:
        """
        Predict next move for a symbol.
        Returns Vantage-compatible signal dict.
        """
        events = self.get_events()
        price_data = self.get_price(symbol)

        # Run transformer
        pred = self.model.predict(events) if events else {
            "direction": "WAIT", "conviction": 0.0, "p_buy": 0.0, "p_sell": 0.0,
        }

        # Format as Vantage signal
        direction = pred.get("direction", "WAIT")
        if direction not in ("BUY", "SELL"):
            direction = "BUY" if pred.get("p_buy", 0) > pred.get("p_sell", 0) else "SELL"

        conviction = float(pred.get("conviction", 0))

        # Adjust conviction by price trend alignment
        price_change = price_data.get("change_24h", 0)
        if direction == "BUY" and price_change < -5:
            conviction *= 0.7  # Reduce conviction if fighting trend
        elif direction == "SELL" and price_change > 5:
            conviction *= 0.7
        conviction = min(0.99, max(0.1, conviction))

        signal = {
            "symbol": symbol,
            "direction": direction,
            "conviction": conviction,
            "chain": "solana" if "SOL" in symbol.upper() or any(
                t in symbol.upper() for t in ["BONK", "WIF", "POPCAT", "MYRO", "TATE", "NEIL", "SAMO", "MEW"]
            ) else "ethereum" if "ETH" in symbol.upper() else "bitcoin",
            "source": "loom-transformer-v1",
            "horizon": f"{int(pred.get('eta_minutes', 60))}m",
            "timestamp": time.time(),
            "metadata": {
                "p_buy": pred.get("p_buy", 0),
                "p_sell": pred.get("p_sell", 0),
                "eta_minutes": pred.get("eta_minutes", 0),
                "price": price_data.get("price", 0),
                "price_change_24h": price_data.get("change_24h", 0),
                "model": "WhaleTransformer-v1",
                "events_used": len(events),
            },
        }

        return signal

    # ── Signal Publishing ──────────────────────────────────────

    def publish_signal(self, signal: dict) -> dict:
        """Push a signal to Vantage's trading signal ingestion endpoint."""
        symbol = signal.get("symbol", "?")

        # Cooldown check
        now = time.time()
        if symbol in self._last_signals:
            if now - self._last_signals[symbol] < self._signal_cooldown:
                return {"published": False, "reason": "cooldown"}

        # P1-05: intel door, system-tool auth, signal_record/1, conviction 0..1
        # capped 0.69. X-Agent-Key is for pulls only.
        import vantage_record as vr
        rec = vr.build(symbol, signal.get("direction", ""), signal.get("conviction", 0),
                       "loom-whale-transformer", mint=str(signal.get("mint") or ""),
                       detail=f"horizon {signal.get('horizon', '')}",
                       evidence={k: v for k, v in (signal.get("metadata") or {}).items()
                                 if isinstance(v, (int, float))},
                       version="loom-predictor/1.0")
        out = vr.post(rec, timeout=5)
        if out.get("published"):
            self._last_signals[symbol] = now
            print(f"  [vantage] published {symbol} {rec['direction']} conv={rec['conviction']:.2f}")
            return out
        print(f"  [vantage] publish failed for {symbol}: {out.get('reason') or out.get('status')}")
        return out

    def run_cycle(self, symbols: list = None):
        """Run one prediction + publish cycle for all tracked symbols."""
        if symbols is None:
            symbols = list(TRACKED_SYMBOLS.values())[:5]  # Top 5 only

        for symbol in symbols:
            try:
                signal = self.predict(symbol)
                if signal["conviction"] >= 0.4:  # Only publish meaningful signals
                    self.publish_signal(signal)
            except Exception as e:
                print(f"  [predictor] error for {symbol}: {e}")

    def run_loop(self, interval: int = 120):
        """Run continuous prediction loop."""
        print(f"[VantagePredictor] starting — {len(TRACKED_SYMBOLS)} symbols, every {interval}s")
        while True:
            self.run_cycle()
            time.sleep(interval)


# ── Standalone Runner ─────────────────────────────────────────

def start_predictor(interval: int = 120):
    """Start the predictor as a background thread."""
    predictor = VantagePredictor()
    t = threading.Thread(target=predictor.run_loop, args=(interval,), daemon=True)
    t.start()
    return predictor


# ── One-shot test ─────────────────────────────────────────────

if __name__ == "__main__":
    predictor = VantagePredictor()
    print("Testing prediction for top 3 symbols...")
    for sym in list(TRACKED_SYMBOLS.values())[:3]:
        signal = predictor.predict(sym)
        print(f"  {sym:12s} → {signal['direction']:4s} conv={signal['conviction']:.2f} "
              f"eta={signal['metadata']['eta_minutes']:.0f}m "
              f"price=${signal['metadata']['price']:.4f}")
