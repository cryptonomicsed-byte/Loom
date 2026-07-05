#!/usr/bin/env python3
"""
LOOM Backtester — Validates whale transformer predictions against historical data.

Runs the transformer over historical event sequences and checks:
  1. Direction accuracy (% of correct BUY/SELL calls)
  2. Profit factor (gross profit / gross loss)
  3. Sharpe ratio (risk-adjusted return)
  4. Maximum drawdown
  5. Win rate by conviction tier
  6. Signal decay curve (accuracy over time after prediction)

All in pure Python/NumPy — no trading framework needed.
"""

import json
import time
import os
import sys
import math
from collections import defaultdict
from typing import List, Dict, Tuple
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


class WhaleBacktester:
    """Backtest whale transformer predictions on historical event sequences."""

    def __init__(self, model=None):
        if model is None:
            from transformer_predictor import get_model
            model = get_model()
        self.model = model
        self.results: List[dict] = []
        self.trades: List[dict] = []

    def simulate_price_path(self, direction: str, magnitude: float,
                            eta: int, steps: int = 10) -> List[float]:
        """
        Simulate a price path after a prediction.
        Uses geometric Brownian motion with drift based on predicted direction.
        """
        price = 1.0
        dt = eta / steps / 60  # time step in hours

        # Drift based on prediction
        drift = 0.01 * magnitude if direction == "BUY" else -0.01 * magnitude
        volatility = 0.02 * (1 + magnitude * 0.5)

        path = [price]
        for _ in range(steps):
            shock = np.random.normal(0, 1) * volatility * math.sqrt(dt)
            price *= math.exp((drift - 0.5 * volatility**2) * dt + shock)
            path.append(price)

        return path

    def run_single_prediction(self, events: list, predicted: dict,
                              actual_direction: str = None,
                              actual_move_pct: float = None) -> dict:
        """
        Evaluate a single prediction against actual outcome.
        Returns trade result with P&L.
        """
        direction = predicted.get("direction", "WAIT")
        conviction = predicted.get("conviction", 0)
        eta = predicted.get("eta_minutes", 60)

        # Simulate outcome if no actual data
        if actual_direction is None:
            magnitude = conviction * 2  # scale conviction to price impact
            path = self.simulate_price_path(direction, magnitude, int(eta))
            final_move = (path[-1] - path[0]) / path[0] * 100

            # Determine if prediction was right
            if direction == "BUY":
                correct = final_move > 0
            elif direction == "SELL":
                correct = final_move < 0
            else:
                correct = False
        else:
            correct = (direction == actual_direction)
            final_move = actual_move_pct or 0

        # P&L calculation
        entry_cost = 0.001  # 0.1% fee
        if direction in ("BUY", "SELL"):
            pnl_pct = abs(final_move) - (entry_cost * 2) if correct else -abs(final_move) - (entry_cost * 2)
        else:
            pnl_pct = 0

        return {
            "direction": direction,
            "conviction": conviction,
            "eta_minutes": int(eta),
            "correct": correct,
            "final_move_pct": round(final_move, 4),
            "pnl_pct": round(pnl_pct, 4),
            "events_used": len(events),
        }

    def backtest_sequence(self, event_sequence: List[dict],
                          window: int = 15, step: int = 5) -> dict:
        """
        Walk-forward backtest over an event sequence.
        Slides a window, predicts next, checks result.

        Args:
            event_sequence: Chronological list of whale events
            window: Number of events to feed to transformer
            step: How many events to step forward each iteration
        """
        trades = []
        for i in range(window, len(event_sequence) - 1, step):
            train_events = event_sequence[max(0, i - window):i]
            next_event = event_sequence[i]

            # Predict
            pred = self.model.predict(train_events)

            # Determine actual outcome from next event
            next_type = next_event.get("type", "")
            if next_type in ("price_surge", "crowd_arrive", "whale_entry", "amplifier_entry"):
                actual = "BUY"
                actual_move = next_event.get("magnitude", 0.5) * 10
            elif next_type in ("whale_exit", "distribution", "price_move"):
                mag = next_event.get("magnitude", 0.3)
                actual = "SELL" if "exit" in next_type or "distribution" in next_type else "BUY" if mag > 0 else "SELL"
                actual_move = abs(mag) * 5 * (1 if actual == "BUY" else -1)
            else:
                actual = None
                actual_move = 0

            result = self.run_single_prediction(
                train_events, pred, actual, actual_move
            )
            result["step"] = i
            result["next_event_type"] = next_type
            trades.append(result)

        self.trades.extend(trades)
        return self._compute_metrics(trades)

    def _compute_metrics(self, trades: List[dict]) -> dict:
        """Compute backtest metrics from trade list."""
        if not trades:
            return {"error": "no trades"}

        actionable = [t for t in trades if t["direction"] in ("BUY", "SELL")]
        if not actionable:
            return {"error": "no actionable trades"}

        total = len(actionable)
        wins = sum(1 for t in actionable if t["correct"])
        losses = total - wins
        win_rate = wins / total * 100

        # P&L
        gross_profit = sum(t["pnl_pct"] for t in actionable if t["pnl_pct"] > 0)
        gross_loss = abs(sum(t["pnl_pct"] for t in actionable if t["pnl_pct"] < 0))
        profit_factor = gross_profit / max(gross_loss, 0.001)
        total_pnl = sum(t["pnl_pct"] for t in actionable)

        # Sharpe
        returns = [t["pnl_pct"] for t in actionable]
        mean_ret = np.mean(returns)
        std_ret = np.std(returns) if len(returns) > 1 else 0.01
        sharpe = (mean_ret / std_ret) * math.sqrt(len(returns)) if std_ret > 0 else 0

        # Max drawdown
        cumulative = np.cumsum(returns)
        running_max = np.maximum.accumulate(cumulative)
        drawdowns = cumulative - running_max
        max_dd = abs(min(drawdowns)) if len(drawdowns) > 0 else 0

        # Conviction tiers
        tiers = {"high": [], "medium": [], "low": []}
        for t in actionable:
            if t["conviction"] >= 0.7:
                tiers["high"].append(t)
            elif t["conviction"] >= 0.4:
                tiers["medium"].append(t)
            else:
                tiers["low"].append(t)

        tier_stats = {}
        for tier_name, tier_trades in tiers.items():
            if tier_trades:
                t_wr = sum(1 for t in tier_trades if t["correct"]) / len(tier_trades) * 100
                t_pnl = sum(t["pnl_pct"] for t in tier_trades)
                tier_stats[tier_name] = {
                    "trades": len(tier_trades),
                    "win_rate": round(t_wr, 1),
                    "total_pnl": round(t_pnl, 4),
                }

        return {
            "total_trades": total,
            "wins": wins,
            "losses": losses,
            "win_rate": round(win_rate, 1),
            "profit_factor": round(profit_factor, 2),
            "total_pnl_pct": round(total_pnl, 4),
            "sharpe_ratio": round(sharpe, 2),
            "max_drawdown_pct": round(max_dd, 4),
            "avg_conviction": round(np.mean([t["conviction"] for t in actionable]), 3),
            "by_conviction_tier": tier_stats,
            "signal_decay": self._compute_signal_decay(actionable),
        }

    def _compute_signal_decay(self, trades: List[dict]) -> dict:
        """Compute how prediction accuracy decays with eta."""
        buckets = defaultdict(list)
        for t in trades:
            bucket = max(0, min(120, t["eta_minutes"]))
            bucket = (bucket // 15) * 15  # 15-min buckets
            buckets[bucket].append(t)

        decay = {}
        for eta_bucket in sorted(buckets.keys()):
            bucket_trades = buckets[eta_bucket]
            wr = sum(1 for t in bucket_trades if t["correct"]) / len(bucket_trades) * 100
            decay[str(eta_bucket)] = {
                "trades": len(bucket_trades),
                "win_rate": round(wr, 1),
            }

        return decay

    def generate_report(self) -> str:
        """Generate a human-readable backtest report."""
        metrics = self._compute_metrics(self.trades) if self.trades else {"error": "no data"}

        if "error" in metrics:
            return f"Backtest Report\n{'='*60}\nError: {metrics['error']}\n"

        lines = [
            "=" * 60,
            "  LOOM WHALE TRANSFORMER — BACKTEST REPORT",
            "=" * 60,
            "",
            f"  Total Trades:     {metrics['total_trades']}",
            f"  Wins:             {metrics['wins']}",
            f"  Losses:           {metrics['losses']}",
            f"  Win Rate:         {metrics['win_rate']}%",
            f"  Profit Factor:    {metrics['profit_factor']}",
            f"  Total P&L:        {metrics['total_pnl_pct']:.2f}%",
            f"  Sharpe Ratio:     {metrics['sharpe_ratio']}",
            f"  Max Drawdown:     {metrics['max_drawdown_pct']:.2f}%",
            f"  Avg Conviction:   {metrics['avg_conviction']}",
            "",
            "  ── By Conviction Tier ──",
        ]

        for tier, stats in metrics.get("by_conviction_tier", {}).items():
            lines.append(
                f"  {tier:8s}: {stats['trades']:3d} trades | "
                f"WR {stats['win_rate']:5.1f}% | "
                f"P&L {stats['total_pnl']:+.4f}%"
            )

        lines.extend([
            "",
            "  ── Signal Decay (accuracy by prediction horizon) ──",
        ])

        for eta, stats in sorted(metrics.get("signal_decay", {}).items(),
                                 key=lambda x: int(x[0])):
            bar = "█" * int(stats["win_rate"] / 5)
            lines.append(
                f"  {eta:>4}min: {bar:<20s} {stats['win_rate']:5.1f}% "
                f"({stats['trades']} trades)"
            )

        lines.append("=" * 60)
        return "\n".join(lines)


# ── Quick Test ────────────────────────────────────────────────

if __name__ == "__main__":
    from transformer_predictor import WhaleTransformer, generate_training_data, train_step

    print("Training transformer for backtest...")
    model = WhaleTransformer()

    # Generate training data
    events = [
        {"type": "scout_entry", "magnitude": 0.3, "entity": "w1", "wallet_count": 1, "confidence": 0.6},
        {"type": "amplifier_entry", "magnitude": 0.6, "entity": "w2", "wallet_count": 2, "confidence": 0.7},
        {"type": "volume_spike", "magnitude": 0.8, "entity": "TOKEN_X", "wallet_count": 3, "confidence": 0.8},
        {"type": "crowd_arrive", "magnitude": 0.9, "entity": "TOKEN_X", "wallet_count": 5, "confidence": 0.9},
        {"type": "whale_exit", "magnitude": 0.5, "entity": "w1", "wallet_count": 1, "confidence": 0.4},
    ] * 30

    X, Y = generate_training_data(events, num_samples=100)

    # Train
    for step in range(50):
        loss = 0
        for i in range(0, len(X), 8):
            loss += train_step(model, X[i:i+8], Y[i:i+8])
        if step % 10 == 0:
            print(f"  step {step}: loss={loss:.4f}")

    # Backtest
    tester = WhaleBacktester(model)
    results = tester.backtest_sequence(events, window=15, step=5)
    print(tester.generate_report())
