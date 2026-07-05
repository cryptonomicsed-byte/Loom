#!/usr/bin/env python3
"""
LOOM Multi-Agent Trading System — Zero-cost, local-only.

Three specialized agents debate every trading decision:
  1. Whale Agent    — Transformer model on whale behavior patterns
  2. Technical Agent — Rule-based: RSI, MACD, Bollinger, volume, stochastic
  3. Risk Agent     — Exposure, drawdown, position sizing, correlation risk

No API keys. No LLM costs. All local.

Output: Consensus signal with conviction, dissent flagged, debate log.
"""

import json
import time
import math
import sys
import os
from collections import defaultdict
from typing import List, Dict, Optional, Tuple
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from transformer_predictor import get_model, predict_from_events


# ═══════════════════════════════════════════════════════════════
# AGENT 1: WHALE AGENT
# ═══════════════════════════════════════════════════════════════

class WhaleAgent:
    """Predicts from whale behavior patterns using the trained transformer."""

    def __init__(self):
        self.model = get_model()
        self.name = "WhaleAgent"
        self.weight = 0.40  # base weight in consensus
        self.track_record = {"correct": 0, "total": 0}
        self._last_signal = None

    def analyze(self, events: list, price_data: dict = None) -> dict:
        """Generate trading signal from whale event sequences."""
        if not events:
            return self._neutral("no events")

        pred = self.model.predict(events)
        direction = pred.get("direction", "WAIT")
        conviction = pred.get("conviction", 0)

        # Adjust by whale activity density
        recent_count = len([e for e in events[-10:] if e.get("magnitude", 0) > 0.3])
        activity_bonus = min(0.15, recent_count * 0.03)
        conviction = min(0.95, conviction + activity_bonus)

        return {
            "agent": self.name,
            "direction": direction,
            "conviction": round(conviction, 3),
            "reasoning": f"Transformer on {len(events)} events: "
                        f"p_buy={pred.get('p_buy', 0):.3f} "
                        f"p_sell={pred.get('p_sell', 0):.3f} "
                        f"eta={pred.get('eta_minutes', 0):.0f}m "
                        f"activity_bonus=+{activity_bonus:.2f}",
            "source": "whale_transformer",
        }

    def _neutral(self, reason: str) -> dict:
        return {"agent": self.name, "direction": "WAIT", "conviction": 0.0,
                "reasoning": reason, "source": "whale_transformer"}


# ═══════════════════════════════════════════════════════════════
# AGENT 2: TECHNICAL AGENT
# ═══════════════════════════════════════════════════════════════

class TechnicalAgent:
    """Rule-based technical analysis: RSI, MACD, Bollinger, volume, stochastic."""

    def __init__(self):
        self.name = "TechnicalAgent"
        self.weight = 0.35
        self.track_record = {"correct": 0, "total": 0}

    def analyze(self, events: list, price_data: dict = None) -> dict:
        """Score technical conditions from recent events."""
        if not events:
            return self._neutral("no data")

        # Extract numeric signals from events
        rsi_vals = []
        macd_vals = []
        bb_pos = []
        vol_ratio = []
        stoch_vals = []

        for e in events[-30:]:
            meta = e.get("metadata", {}) if isinstance(e, dict) else {}
            rsi = meta.get("rsi")
            macd_h = meta.get("macd_hist")
            bb = meta.get("bb_position")
            vol = meta.get("volume_ratio")
            stoch = meta.get("stoch")
            if rsi is not None: rsi_vals.append(rsi)
            if macd_h is not None: macd_vals.append(macd_h)
            if bb is not None: bb_pos.append(bb)
            if vol is not None: vol_ratio.append(vol)
            if stoch is not None: stoch_vals.append(stoch)

        # If no structured data, derive from event types
        buy_signals = 0
        sell_signals = 0
        total_signals = 0

        for e in events[-20:]:
            etype = e.get("type", "")
            mag = e.get("magnitude", 0)

            # Map event types to technical conditions
            if etype in ("price_surge", "crowd_arrive"):
                buy_signals += mag
            elif etype in ("whale_exit", "distribution", "anomaly"):
                sell_signals += mag
            elif etype == "volume_spike":
                buy_signals += mag * 0.5  # volume can go either way
            total_signals += abs(mag)

        # Compute score
        if total_signals > 0:
            buy_score = buy_signals / total_signals
            sell_score = sell_signals / total_signals
        else:
            buy_score = sell_score = 0

        # Use actual indicator values if available
        rsi_now = rsi_vals[-1] if rsi_vals else 50

        if rsi_now < 30:
            buy_score += 0.2
        elif rsi_now > 70:
            sell_score += 0.2

        if macd_vals and len(macd_vals) >= 2:
            if macd_vals[-1] > 0 and macd_vals[-2] <= 0:
                buy_score += 0.15  # MACD bullish crossover
            elif macd_vals[-1] < 0 and macd_vals[-2] >= 0:
                sell_score += 0.15  # MACD bearish crossover

        if stoch_vals:
            if stoch_vals[-1] < 20:
                buy_score += 0.1
            elif stoch_vals[-1] > 80:
                sell_score += 0.1

        # Determine direction
        if buy_score > sell_score + 0.1:
            direction = "BUY"
            conviction = min(0.85, buy_score)
        elif sell_score > buy_score + 0.1:
            direction = "SELL"
            conviction = min(0.85, sell_score)
        else:
            direction = "WAIT"
            conviction = max(buy_score, sell_score) * 0.5

        return {
            "agent": self.name,
            "direction": direction,
            "conviction": round(conviction, 3),
            "reasoning": f"Buy={buy_score:.2f} Sell={sell_score:.2f} "
                        f"RSI={rsi_now:.0f} Events={total_signals:.2f}",
            "source": "technical_rules",
        }

    def _neutral(self, reason: str) -> dict:
        return {"agent": self.name, "direction": "WAIT", "conviction": 0.0,
                "reasoning": reason, "source": "technical_rules"}


# ═══════════════════════════════════════════════════════════════
# AGENT 3: RISK AGENT
# ═══════════════════════════════════════════════════════════════

class RiskAgent:
    """Monitors exposure, drawdown, and correlation risk."""

    def __init__(self):
        self.name = "RiskAgent"
        self.weight = 0.25
        self.track_record = {"correct": 0, "total": 0}
        self.max_exposure = 0.50  # 50% of capital
        self.max_drawdown = 0.15  # 15% max
        self.max_positions = 5
        self.open_positions: list = []
        self.current_exposure = 0.0
        self.current_drawdown = 0.0

    def update_state(self, exposure: float = 0, drawdown: float = 0,
                     open_count: int = 0):
        self.current_exposure = exposure
        self.current_drawdown = drawdown
        self.open_positions = [{}] * open_count

    def analyze(self, events: list, price_data: dict = None) -> dict:
        """Evaluate risk conditions and decide whether to allow/prohibit new positions."""

        reasons = []
        risk_score = 0.0  # 0 = all clear, 1 = maximum risk
        direction = "WAIT"
        allow_entry = True

        # Exposure check
        if self.current_exposure >= self.max_exposure:
            reasons.append(f"exposure {self.current_exposure:.0%} >= max {self.max_exposure:.0%}")
            risk_score = 0.9
            allow_entry = False

        # Drawdown check
        if self.current_drawdown >= self.max_drawdown:
            reasons.append(f"drawdown {self.current_drawdown:.0%} >= max {self.max_drawdown:.0%}")
            risk_score = max(risk_score, 0.85)
            allow_entry = False

        # Position count check
        if len(self.open_positions) >= self.max_positions:
            reasons.append(f"positions {len(self.open_positions)} >= max {self.max_positions}")
            risk_score = max(risk_score, 0.6)

        # Event-based risk signals
        anomaly_count = sum(1 for e in events[-20:] if e.get("type") == "anomaly")
        if anomaly_count >= 3:
            reasons.append(f"{anomaly_count} anomalies in recent events")
            risk_score = max(risk_score, 0.5)
            allow_entry = False

        # Volatility check from events
        magnitudes = [e.get("magnitude", 0) for e in events[-10:] if e.get("magnitude", 0) > 0]
        if magnitudes and np.std(magnitudes) > 0.3:
            reasons.append("high event volatility")
            risk_score = max(risk_score, 0.4)

        if not reasons:
            reasons.append("all risk parameters within bounds")

        direction = "WAIT" if not allow_entry else "ALLOW"
        conviction = 1.0 - risk_score

        return {
            "agent": self.name,
            "direction": direction,
            "conviction": round(conviction, 3),
            "reasoning": "; ".join(reasons),
            "risk_score": round(risk_score, 3),
            "allow_entry": allow_entry,
            "source": "risk_management",
        }


# ═══════════════════════════════════════════════════════════════
# ORCHESTRATOR — Consensus + Debate
# ═══════════════════════════════════════════════════════════════

class AgentOrchestrator:
    """
    Runs all agents, computes weighted consensus, and surfaces dissent.

    Decision logic:
      1. Each agent produces {direction, conviction, reasoning}
      2. If RiskAgent flags => hard veto (no entry regardless of others)
      3. Remaining agents vote weighted by conviction * agent_weight * track_record
      4. If Whale and Technical disagree by >0.3 conviction => DEBATE flag
      5. Consensus output includes the debate log for transparency
    """

    def __init__(self, events_callback=None):
        self.whale = WhaleAgent()
        self.technical = TechnicalAgent()
        self.risk = RiskAgent()
        self.agents = [self.whale, self.technical, self.risk]
        self._events_cb = events_callback
        self.decision_log: list = []

    def get_events(self) -> list:
        if self._events_cb:
            return self._events_cb()
        return []

    def decide(self, symbol: str = "UNKNOWN",
               events: list = None,
               price_data: dict = None) -> dict:
        """
        Run full agent debate cycle for a trading decision.
        Returns consensus signal ready for Vantage or Freqtrade.
        """
        if events is None:
            events = self.get_events()

        # Run all agents
        whale_sig = self.whale.analyze(events, price_data)
        tech_sig = self.technical.analyze(events, price_data)
        risk_sig = self.risk.analyze(events, price_data)

        # Check for hard veto
        if not risk_sig.get("allow_entry", True):
            return self._build_consensus(
                symbol, events,
                whale_sig, tech_sig, risk_sig,
                final_direction="WAIT",
                final_conviction=0.0,
                veto=True,
                veto_reason=risk_sig["reasoning"],
            )

        # Weighted vote between Whale and Technical
        agents_voting = [whale_sig, tech_sig]
        weights = [self.whale.weight, self.technical.weight]

        # Adjust weights by historical accuracy
        for i, agent in enumerate([self.whale, self.technical]):
            tr = agent.track_record
            if tr["total"] > 5:
                acc_bonus = (tr["correct"] / tr["total"] - 0.5) * 0.2
                weights[i] += acc_bonus

        # Compute weighted scores
        buy_score = 0.0
        sell_score = 0.0
        total_weight = 0.0

        for sig, w in zip(agents_voting, weights):
            if sig["direction"] == "BUY":
                buy_score += sig["conviction"] * w
            elif sig["direction"] == "SELL":
                sell_score += sig["conviction"] * w
            total_weight += w

        # Normalize
        if total_weight > 0:
            buy_score /= total_weight
            sell_score /= total_weight

        # Determine consensus direction
        if buy_score > sell_score + 0.1:
            final_direction = "BUY"
            final_conviction = buy_score
        elif sell_score > buy_score + 0.1:
            final_direction = "SELL"
            final_conviction = sell_score
        else:
            final_direction = "WAIT"
            final_conviction = max(buy_score, sell_score) * 0.4

        # Debate check: do agents disagree significantly?
        debate = False
        debate_reason = ""
        if (whale_sig["direction"] != tech_sig["direction"] and
            whale_sig["conviction"] > 0.3 and tech_sig["conviction"] > 0.3):
            debate = True
            gap = abs(whale_sig["conviction"] - tech_sig["conviction"])
            debate_reason = (
                f"DISAGREEMENT: WhaleAgent says {whale_sig['direction']} "
                f"(conv={whale_sig['conviction']:.2f}), "
                f"TechnicalAgent says {tech_sig['direction']} "
                f"(conv={tech_sig['conviction']:.2f}). Gap={gap:.2f}"
            )

        return self._build_consensus(
            symbol, events,
            whale_sig, tech_sig, risk_sig,
            final_direction, final_conviction,
            veto=False,
            debate=debate,
            debate_reason=debate_reason,
        )

    def _build_consensus(self, symbol, events,
                         whale_sig, tech_sig, risk_sig,
                         final_direction, final_conviction,
                         veto=False, veto_reason="",
                         debate=False, debate_reason="") -> dict:
        """Build structured consensus output."""

        # Track record update placeholder (in production, update after trade closes)
        result = {
            "symbol": symbol,
            "timestamp": time.time(),
            "events_analyzed": len(events),
            "agents": {
                "whale": whale_sig,
                "technical": tech_sig,
                "risk": risk_sig,
            },
            "consensus": {
                "direction": final_direction,
                "conviction": round(final_conviction, 3),
                "vetoed": veto,
                "veto_reason": veto_reason,
                "debate": debate,
                "debate_reason": debate_reason,
                "source": "loom-multi-agent-v1",
            },
        }

        self.decision_log.append({
            "ts": time.time(),
            "symbol": symbol,
            "direction": final_direction,
            "conviction": final_conviction,
            "debate": debate,
        })

        # Keep log bounded
        if len(self.decision_log) > 1000:
            self.decision_log = self.decision_log[-500:]

        return result

    def generate_report(self) -> str:
        """Generate a human-readable summary of the latest decision."""
        if not self.decision_log:
            return "No decisions yet."

        last = self.decision_log[-1]
        lines = [
            "=" * 55,
            "  LOOM MULTI-AGENT CONSENSUS",
            "=" * 55,
            f"  Symbol:     {last['symbol']}",
            f"  Direction:  {last['direction']}",
            f"  Conviction: {last['conviction']:.2f}",
            f"  Debate:     {'⚠️ YES — agents disagree' if last['debate'] else '✓ Consensus'}",
            "=" * 55,
        ]
        return "\n".join(lines)


# ═══════════════════════════════════════════════════════════════
# Quick Test
# ═══════════════════════════════════════════════════════════════

if __name__ == "__main__":
    orch = AgentOrchestrator()

    # Sample events from the fabric pattern
    test_events = [
        {"type": "scout_entry", "magnitude": 0.3, "entity": "w1", "wallet_count": 1,
         "confidence": 0.6, "metadata": {"rsi": 28, "macd_hist": 0.01, "bb_position": 0.1}},
        {"type": "amplifier_entry", "magnitude": 0.6, "entity": "w2", "wallet_count": 2,
         "confidence": 0.7, "metadata": {"rsi": 25, "macd_hist": 0.02, "bb_position": 0.15}},
        {"type": "volume_spike", "magnitude": 0.8, "entity": "SOL", "wallet_count": 3,
         "confidence": 0.8, "metadata": {"rsi": 30, "macd_hist": 0.03, "bb_position": 0.2}},
        {"type": "crowd_arrive", "magnitude": 0.9, "entity": "SOL", "wallet_count": 5,
         "confidence": 0.9, "metadata": {"rsi": 35, "macd_hist": 0.05, "bb_position": 0.3}},
    ] * 5

    # Normal scenario
    result = orch.decide("SOL/USDC", test_events)
    print(orch.generate_report())
    print(f"\nWhale:   {result['agents']['whale']['direction']:4s} "
          f"conv={result['agents']['whale']['conviction']:.2f}")
    print(f"Tech:    {result['agents']['technical']['direction']:4s} "
          f"conv={result['agents']['technical']['conviction']:.2f}")
    print(f"Risk:    {result['agents']['risk']['direction']:4s} "
          f"conv={result['agents']['risk']['conviction']:.2f}")
    print(f"CONSENSUS: {result['consensus']['direction']:4s} "
          f"conv={result['consensus']['conviction']:.2f}")

    # High risk scenario
    orch.risk.update_state(exposure=0.55, drawdown=0.18, open_count=6)
    result2 = orch.decide("SOL/USDC", test_events)
    print(f"\n⚠️  HIGH RISK — VETO: {result2['consensus']['vetoed']}")
    print(f"   Reason: {result2['consensus']['veto_reason']}")
