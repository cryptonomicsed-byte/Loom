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


# ═══════════════════════════════════════════════════════════════
# OHLCV BACKTESTER — market-grounded walk-forward + calibration
# ═══════════════════════════════════════════════════════════════

def _ema_np(arr: np.ndarray, span: int) -> np.ndarray:
    """Exponential moving average (pure numpy)."""
    out = np.empty_like(arr, dtype=np.float64)
    out[0] = arr[0]
    alpha = 2.0 / (span + 1.0)
    for k in range(1, len(arr)):
        out[k] = alpha * arr[k] + (1 - alpha) * out[k - 1]
    return out


class OHLCVBacktester:
    """Walk-forward backtest on real OHLCV bars.

    A signal function is evaluated at each bar close (i); execution happens
    at bar (i+1) open with slippage. Positions exit on exit signal, stop
    loss, take profit or max-hold. Costs are charged round-trip (fee +
    slippage on both legs).

    Produces:
      - trade-level metrics (win rate, profit factor, Sharpe, max DD)
      - signal-level calibration: hit-rate vs conviction buckets, Brier
        score, horizon decay — i.e. *does conviction actually mean anything?*
    """

    def __init__(self, ohlcv: dict, fee_bps: float = 10.0,
                 slippage_bps: float = 50.0):
        self.ts = np.asarray(ohlcv["ts"], dtype=np.float64)
        self.o = np.asarray(ohlcv["open"], dtype=np.float64)
        self.h = np.asarray(ohlcv["high"], dtype=np.float64)
        self.l = np.asarray(ohlcv["low"], dtype=np.float64)
        self.c = np.asarray(ohlcv["close"], dtype=np.float64)
        self.v = np.asarray(ohlcv["volume"], dtype=np.float64)
        self.n = len(self.c)
        # Round-trip cost as a fraction of notional
        self.cost = 2.0 * (fee_bps + slippage_bps) / 10000.0
        self.slippage = slippage_bps / 10000.0
        self.ind = self._indicators()

    def _indicators(self) -> dict:
        c, v = self.c, self.v
        n = self.n

        # RSI(14) — epsilon-guarded (audit F5)
        delta = np.diff(c, prepend=c[0])
        gain = np.where(delta > 0, delta, 0.0)
        loss = np.where(delta < 0, -delta, 0.0)
        ag = _ema_np(gain, 14)
        al = _ema_np(loss, 14)
        rs = ag / np.maximum(al, 1e-9)
        rsi = 100.0 - 100.0 / (1.0 + rs)

        # MACD(12,26,9)
        ema12 = _ema_np(c, 12)
        ema26 = _ema_np(c, 26)
        macd = ema12 - ema26
        macd_signal = _ema_np(macd, 9)
        macd_hist = macd - macd_signal

        # Bollinger(20,2)
        sma20 = np.convolve(c, np.ones(20) / 20.0, mode="full")[:n]
        sma20[:19] = c[:19]
        sd20 = np.array([
            float(np.std(c[max(0, i - 19):i + 1])) for i in range(n)
        ])
        bb_low = sma20 - 2 * sd20
        bb_high = sma20 + 2 * sd20
        bb_pos = (c - bb_low) / np.maximum(bb_high - bb_low, 1e-9)

        # Volume ratio (vs 20-bar SMA)
        vsma = np.convolve(v, np.ones(20) / 20.0, mode="full")[:n]
        vsma[:19] = v[:19]
        vol_ratio = v / np.maximum(vsma, 1e-9)

        # ATR(14)
        tr = np.maximum(self.h - self.l, np.maximum(
            np.abs(self.h - np.roll(c, 1)), np.abs(self.l - np.roll(c, 1))))
        tr[0] = self.h[0] - self.l[0]
        atr = _ema_np(tr, 14)

        return {
            "ts": self.ts, "open": self.o, "high": self.h, "low": self.l,
            "close": c, "volume": v,
            "rsi": rsi, "macd_hist": macd_hist, "macd_signal": macd_signal,
            "bb_pos": bb_pos, "vol_ratio": vol_ratio, "atr": atr,
        }

    # ── Simulation ─────────────────────────────────────────────

    def run(self, signal_fn, stop_loss: float = -0.12,
            take_profit: float = 0.10, max_hold_bars: int = 16,
            cooldown_bars: int = 2, only_long: bool = True) -> dict:
        """Walk forward; signal_fn(i, ind, meta) -> {direction, conviction}.

        meta carries {pair, symbol, context} where context is an optional
        dict set by the caller (e.g. per-token whale conviction).
        """
        ind = self.ind
        signals = []
        trades = []
        meta = {"n": self.n}

        in_position = False
        entry_i = entry_px = None
        entry_conv = entry_dir = None
        cooldown_until = -1

        for i in range(26, self.n - 1):
            if in_position:
                j = i  # current bar while holding
                entry = entry_px
                sl = entry * (1.0 + stop_loss)
                tp = entry * (1.0 + take_profit)
                exit_px = None
                reason = None

                # Intrabar stop/take-profit (conservative: stop wins ties)
                if self.l[j] <= sl:
                    exit_px, reason = sl, "stop_loss"
                elif self.h[j] >= tp:
                    exit_px, reason = tp, "take_profit"
                elif (j - entry_i) >= max_hold_bars:
                    exit_px, reason = self.c[j], "max_hold"

                if exit_px is None:
                    # Signal-based exit check (e.g. technical overbought)
                    sig = signal_fn(i, ind, meta)
                    if sig.get("direction") == "SELL":
                        if j + 1 < self.n:
                            exit_px = self.o[j + 1] * (1.0 - self.slippage)
                        else:
                            exit_px = self.c[j]
                        reason = "signal_exit"

                if exit_px is not None:
                    ret = (exit_px / entry - 1.0) - self.cost
                    trades.append({
                        "symbol": meta.get("symbol", "?"),
                        "entry_i": entry_i, "exit_i": j,
                        "entry_ts": float(self.ts[entry_i]),
                        "exit_ts": float(self.ts[j]),
                        "entry_px": round(entry, 8),
                        "exit_px": round(exit_px, 8),
                        "ret_pct": round(ret * 100.0, 4),
                        "conviction": entry_conv,
                        "direction": entry_dir,
                        "bars_held": j - entry_i,
                        "reason": reason,
                    })
                    in_position = False
                    cooldown_until = j + cooldown_bars
                    continue

            # Evaluate signal at close of bar i
            sig = signal_fn(i, ind, meta)
            direction = sig.get("direction", "WAIT")
            conviction = float(sig.get("conviction", 0.0))

            if direction != "WAIT":
                signals.append({
                    "i": i, "ts": float(self.ts[i]),
                    "direction": direction,
                    "conviction": conviction,
                    "symbol": meta.get("symbol", "?"),
                })

            if direction == "BUY" and not in_position and i >= cooldown_until:
                entry_i = i
                entry_px = self.o[i + 1] * (1.0 + self.slippage)
                entry_conv = conviction
                entry_dir = direction
                in_position = True

        # Flatten anything still open at the end
        if in_position:
            ret = (self.c[-1] / entry_px - 1.0) - self.cost
            trades.append({
                "symbol": meta.get("symbol", "?"),
                "entry_i": entry_i, "exit_i": self.n - 1,
                "entry_ts": float(self.ts[entry_i]),
                "exit_ts": float(self.ts[-1]),
                "entry_px": round(entry_px, 8),
                "exit_px": round(self.c[-1], 8),
                "ret_pct": round(ret * 100.0, 4),
                "conviction": entry_conv,
                "direction": entry_dir,
                "bars_held": self.n - 1 - entry_i,
                "reason": "end_of_data",
            })

        metrics = self._trade_metrics(trades)
        calibration = self._calibrate(signals)
        return {
            "metrics": metrics,
            "calibration": calibration,
            "signals": len(signals),
            "trades": trades,
        }

    # ── Metrics ────────────────────────────────────────────────

    def _trade_metrics(self, trades: list) -> dict:
        if not trades:
            return {"trades": 0, "error": "no trades"}

        rets = np.array([t["ret_pct"] for t in trades])
        wins = int(np.sum(rets > 0))
        total = len(rets)
        gross_profit = float(np.sum(rets[rets > 0]))
        gross_loss = abs(float(np.sum(rets[rets < 0])))
        total_pnl = float(np.sum(rets))
        mean = float(np.mean(rets))
        std = float(np.std(rets)) or 0.01
        sharpe = (mean / std) * math.sqrt(total) if std > 0 else 0.0

        cum = np.cumsum(rets)
        running_max = np.maximum.accumulate(cum)
        max_dd = float(abs(np.min(cum - running_max)))

        # Conviction-tier trade stats
        tiers = {}
        for t in trades:
            cv = t["conviction"]
            tier = "high" if cv >= 0.7 else ("medium" if cv >= 0.4 else "low")
            tiers.setdefault(tier, []).append(t)
        tier_stats = {}
        for tier, tt in tiers.items():
            r = np.array([x["ret_pct"] for x in tt])
            tier_stats[tier] = {
                "trades": len(tt),
                "win_rate": round(float(np.sum(r > 0)) / len(r) * 100, 1),
                "total_pnl_pct": round(float(np.sum(r)), 2),
            }

        return {
            "trades": total,
            "wins": wins,
            "losses": total - wins,
            "win_rate": round(wins / total * 100, 1),
            "profit_factor": round(gross_profit / max(gross_loss, 0.001), 2),
            "total_pnl_pct": round(total_pnl, 2),
            "sharpe_ratio": round(sharpe, 2),
            "max_drawdown_pct": round(max_dd, 2),
            "avg_ret_pct": round(mean, 3),
            "avg_hold_bars": round(float(np.mean([t["bars_held"] for t in trades])), 1),
            "avg_conviction": round(float(np.mean([t["conviction"] for t in trades])), 3),
            "by_conviction_tier": tier_stats,
        }

    # ── Signal calibration ─────────────────────────────────────

    def _calibrate(self, signals: list,
                   horizons: tuple = (1, 2, 4, 8, 12, 24)) -> dict:
        """Signal-level calibration: hit-rate vs conviction, Brier, decay.

        A BUY signal at bar i is a 'hit' at horizon h if the close h bars
        later is above the slippage-adjusted execution price (open[i+1]).
        """
        if not signals:
            return {"error": "no signals"}

        buckets = [0.0, 0.2, 0.4, 0.6, 0.8, 1.01]
        bucket_stats = {b: {"n": 0, "conv_sum": 0.0, "hits": 0,
                            "hits_12": 0, "brier_4": 0.0, "brier_12": 0.0}
                        for b in buckets[:-1]}
        decay = {h: {"n": 0, "hits": 0} for h in horizons}
        brier4_total = brier12_total = n_eval4 = n_eval12 = 0

        for s in signals:
            if s.get("direction") != "BUY":
                continue  # long-only strategy: SELL is an exit trigger, not a tradeable signal
            i = s["i"]
            conv = s["conviction"]
            bucket = max(b for b in buckets[:-1] if conv >= b)
            bs = bucket_stats[bucket]
            bs["n"] += 1
            bs["conv_sum"] += conv

            entry = self.o[i + 1] * (1.0 + self.slippage)
            for h in horizons:
                if i + 1 + h >= self.n:
                    continue
                ret = self.c[i + 1 + h] / entry - 1.0
                hit = 1.0 if ret > 0 else 0.0
                decay[h]["n"] += 1
                decay[h]["hits"] += hit
                if h == 4:
                    bs["hits"] += hit
                    bs["brier_4"] += (hit - conv) ** 2
                    brier4_total += (hit - conv) ** 2
                    n_eval4 += 1
                if h == 12:
                    bs["hits_12"] += hit
                    bs["brier_12"] += (hit - conv) ** 2
                    brier12_total += (hit - conv) ** 2
                    n_eval12 += 1

        cal = {"buckets": {}, "decay": {}, "brier_4h": None, "brier_12h": None}
        for b, s in bucket_stats.items():
            if s["n"] == 0:
                continue
            mean_conv = s["conv_sum"] / s["n"]
            cal["buckets"][f"{b:.1f}"] = {
                "n": s["n"],
                "mean_conviction": round(mean_conv, 3),
                "hit_rate_4h": round(s["hits"] / max(s["n"], 1) * 100, 1),
                "hit_rate_12h": round(s["hits_12"] / max(s["n"], 1) * 100, 1),
                "brier_4h": round(s["brier_4"] / max(s["n"], 1), 4),
            }
        for h, s in decay.items():
            cal["decay"][str(h)] = {
                "n": s["n"],
                "hit_rate": round(s["hits"] / max(s["n"], 1) * 100, 1),
            }
        if n_eval4:
            cal["brier_4h"] = round(brier4_total / n_eval4, 4)
        if n_eval12:
            cal["brier_12h"] = round(brier12_total / n_eval12, 4)
        return cal

    # ── Reporting ──────────────────────────────────────────────

    def calibration_report_md(self, label: str, result: dict) -> str:
        lines = [
            f"### {label}",
            "",
            f"- Signals: **{result['signals']}**",
        ]
        m = result.get("metrics") or {}
        if m.get("error"):
            lines.append(f"- Trades: {m['error']}")
        else:
            lines.append(
                f"- Trades: **{m['trades']}** | Win rate **{m['win_rate']}%** | "
                f"Profit factor **{m['profit_factor']}** | P&L **{m['total_pnl_pct']}%** | "
                f"Sharpe **{m['sharpe_ratio']}** | MaxDD **{m['max_drawdown_pct']}%**")
            lines.append("")
            lines.append("| conviction bucket | n | mean conv | hit@4h | hit@12h | brier@4h |")
            lines.append("|---|---|---|---|---|---|")
            cal = result.get("calibration") or {}
            for b in sorted(cal.get("buckets", {}), key=float):
                s = cal["buckets"][b]
                lines.append(
                    f"| {b} | {s['n']} | {s['mean_conviction']:.2f} | "
                    f"{s['hit_rate_4h']}% | {s['hit_rate_12h']}% | {s['brier_4h']} |")
            brier = cal.get("brier_4h")
            if brier is not None:
                lines.append("")
                lines.append(f"- **Brier@4h: {brier}** (lower = better calibrated)")
        lines.append("")
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
