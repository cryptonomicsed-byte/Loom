#!/usr/bin/env python3
"""
LOOM Signal Composites — market-quality gate + smart-money momentum.

Closes the hard-risk gaps found in the TransformerSignalStrategy audit:

  MarketQualityEngine  — liquidity-depth gate, age gate, volume/turnover
                         sanity, honeypot flags (illiquid pump, sell-side
                         vacuum, wash volume) and bundler-risk proxies
                         (single-pool dominance, launch-window pump,
                         FDV/liquidity mismatch). Pure heuristics on
                         DexScreener pair metadata — no on-chain tax oracle
                         needed, so it runs on the public API.

  SmartMoneyComposite  — per-token conviction fusing:
                         (a) whale-engine predictive value for the token,
                         (b) momentum (ROC + volume surge),
                         (c) market quality,
                         (d) technical alignment (RSI/MACD/Bollinger).
                         Quality-failed tokens are truncated to 0.3
                         conviction, so the gate is a hard filter on the
                         composite score.

No pandas. numpy + stdlib only. Lazy-imports the LOOM whale engine so this
module stays import-safe in fast_server's background loader.
"""

import math
import time
from typing import Optional

import numpy as np

# ── Gate thresholds (USD / hours / ratios) ─────────────────────
MIN_LIQUIDITY_USD = 30_000.0      # soft floor — below this is micro-pool
HARD_LIQUIDITY_USD = 10_000.0     # hard floor — instant reject
MIN_AGE_HOURS = 1.0               # younger tokens are bundler territory
WASH_TURNOVER = 20.0              # vol24h / liq > this → wash-traded
FDV_LIQ_RATIO = 500.0             # fdv / liq > this → mispriced microcap
SINGLE_POOL_VOL_SHARE = 0.85      # top pair volume share → dominance
LAUNCH_WINDOW_HOURS = 24.0

# Composite weights (sum = 1.0)
W_QUALITY = 0.42
W_MOMENTUM = 0.28
W_WHALE = 0.20
W_TECH = 0.10

BUY_CONVICTION = 0.55
REJECT_CAP = 0.30                 # quality-failed conviction ceiling


def _sig(x: float) -> float:
    """Logistic squash to (0,1), centered at 0."""
    try:
        return 1.0 / (1.0 + math.exp(-float(x)))
    except OverflowError:
        return 1.0 if x > 0 else 0.0


def _num(v, default=0.0) -> float:
    try:
        f = float(v)
        return f if math.isfinite(f) else default
    except (TypeError, ValueError):
        return default


# ═══════════════════════════════════════════════════════════════
# MARKET QUALITY ENGINE
# ═══════════════════════════════════════════════════════════════

class MarketQualityEngine:
    """Scores token market quality and emits honeypot/bundler risk flags.

    Input is DexScreener pair metadata (see ohlcv_feed.dex_solana_pairs).
    """

    def evaluate(self, pairs: list, top_pair: dict = None) -> dict:
        if not pairs:
            return self._result(0.0, ["no_pair_data"], ["no_pair_data"],
                                passed=False)

        top = top_pair or pairs[0]
        liq = _num((top.get("liquidity") or {}).get("usd"))
        vol = (top.get("volume") or {})
        vol_h24 = _num(vol.get("h24"))
        vol_h1 = _num(vol.get("h1"))
        vol_m5 = _num(vol.get("m5"))
        pc = (top.get("priceChange") or {})
        pc_m5 = _num(pc.get("m5"))
        pc_1h = _num(pc.get("m1h"))
        pc_6h = _num(pc.get("m6h"))
        txns = (top.get("txns") or {})
        t_h24 = txns.get("h24") or {}
        t_m5 = txns.get("m5") or {}
        buys_h24 = _num(t_h24.get("buys"))
        sells_h24 = _num(t_h24.get("sells"))
        buys_m5 = _num(t_m5.get("buys"))
        sells_m5 = _num(t_m5.get("sells"))
        fdv = _num(top.get("fdv"))
        created_ms = _num(top.get("pairCreatedAt"))
        age_h = max(0.0, (time.time() * 1000 - created_ms) / 3.6e6) if created_ms else 0.0

        honeypot = []
        bundler = []

        # ── Honeypot flags (proxy — no on-chain tax oracle) ──
        if liq < HARD_LIQUIDITY_USD:
            honeypot.append("no_liquidity")
        elif liq < MIN_LIQUIDITY_USD:
            honeypot.append("micro_liquidity")
        if pc_m5 > 20.0 and vol_m5 < 5_000:
            honeypot.append("illiquid_pump")        # price moves w/o volume
        if vol_h24 > 0 and sells_h24 <= 0 and buys_h24 > 0:
            honeypot.append("sell_side_vacuum")     # no sellers = can't exit
        if liq > 0 and vol_h24 / max(liq, 1) > WASH_TURNOVER:
            honeypot.append("wash_volume")
        if 0 < age_h < MIN_AGE_HOURS:
            honeypot.append("too_young")

        # ── Bundler-risk proxies ──
        total_vol = sum(_num((p.get("volume") or {}).get("h24")) for p in pairs)
        if total_vol > 0 and len(pairs) >= 2:
            top_share = vol_h24 / total_vol
            if top_share > SINGLE_POOL_VOL_SHARE and liq < 100_000:
                bundler.append("single_pool_dominance")
        elif len(pairs) == 1 and 0 < liq < 50_000:
            bundler.append("thin_single_pool")
        if age_h < LAUNCH_WINDOW_HOURS and pc_m5 > 10.0:
            bundler.append("launch_window_pump")
        if fdv > 0 and liq > 0 and fdv / max(liq, 1) > FDV_LIQ_RATIO:
            bundler.append("fdv_liq_mismatch")

        hard_flags = {"no_liquidity", "illiquid_pump", "sell_side_vacuum"}
        passed = (
            liq >= MIN_LIQUIDITY_USD
            and age_h >= MIN_AGE_HOURS
            and not (hard_flags & set(honeypot))
        )

        # ── Quality sub-score (0..1) ──
        depth = min(1.0, liq / 250_000.0)                    # 250k = full marks
        turnover_sane = 1.0 if vol_h24 <= WASH_TURNOVER * max(liq, 1) \
            else max(0.0, 1.0 - vol_h24 / max(liq * 4, 1))
        age_score = min(1.0, age_h / 48.0)
        buy_pressure = _sig((buys_h24 - sells_h24) / max(buys_h24 + sells_h24, 1) * 4)
        quality = 0.4 * depth + 0.2 * turnover_sane + 0.2 * age_score + 0.2 * buy_pressure
        quality = min(1.0, max(0.0, quality))

        return self._result(quality, honeypot, bundler, passed=passed,
                            extra={
                                "liquidity_usd": round(liq, 2),
                                "volume_24h": round(vol_h24, 2),
                                "age_hours": round(age_h, 3),
                                "price_change_5m": round(pc_m5, 3),
                                "price_change_6h": round(pc_6h, 3),
                                "buys_24h": int(buys_h24),
                                "sells_24h": int(sells_h24),
                                "fdv": round(fdv, 2) if fdv else None,
                                "pairs": len(pairs),
                            })

    def _result(self, score, honeypot, bundler, passed, extra=None):
        r = {
            "score": round(max(0.0, min(1.0, score)), 4),
            "passed": passed,
            "honeypot_flags": honeypot,
            "bundler_risk": bundler,
            "reasons": honeypot + bundler,
        }
        if extra:
            r.update(extra)
        return r


# ═══════════════════════════════════════════════════════════════
# SMART MONEY COMPOSITE
# ═══════════════════════════════════════════════════════════════

class SmartMoneyComposite:
    """Per-token conviction: whale signal × momentum × quality × technical."""

    def __init__(self, whale_engine=None):
        self.quality = MarketQualityEngine()
        self._whale_engine = whale_engine

    # ── Whale conviction legs ──────────────────────────────────

    def _engine(self):
        """Lazy LOOM whale engine access (import-safe in servers)."""
        if self._whale_engine is not None:
            return self._whale_engine
        try:
            from whales import whales
            self._whale_engine = whales
        except Exception:
            self._whale_engine = False  # permanently off, no repeated imports
        return self._whale_engine or None

    def token_whale_conviction(self, token_mint: str) -> float:
        """Avg predictive value of tier<=2 wallets that traded this token."""
        eng = self._engine()
        if not eng:
            return 0.0
        try:
            addrs = eng.token_index.get(token_mint, [])
            vals = []
            for a in addrs:
                w = eng.wallets.get(a)
                if w and w.tier <= 2 and w.total_trades >= 3:
                    vals.append(w.predictive_value)
            return sum(vals) / len(vals) if vals else 0.0
        except Exception:
            return 0.0

    def global_whale_conviction(self) -> float:
        """Legacy heuristic (matches TransformerSignalStrategy F1 behavior):
        avg predictive value of tier<=2 whales → 0..0.9 conviction."""
        eng = self._engine()
        if not eng:
            return 0.0
        try:
            vals = [w.predictive_value for w in eng.wallets.values()
                    if w.tier <= 2 and w.total_trades >= 3]
            if not vals:
                return 0.0
            avg_pv = sum(vals) / len(vals)
            return max(0.0, min(0.9, avg_pv))
        except Exception:
            return 0.0

    # ── Technical / momentum legs (numpy on OHLCV arrays) ─────

    @staticmethod
    def _momentum(c: np.ndarray, v: np.ndarray, i: int, lookback: int = 6):
        """Momentum sub-score at bar i from ROC + volume surge."""
        if i < lookback + 2:
            return 0.0
        c_prev = c[i - lookback]
        roc = (c[i] / c_prev - 1.0) if c_prev > 0 else 0.0
        v_sma = float(np.mean(v[max(0, i - 20):i + 1]))
        v_ratio = float(v[i]) / max(v_sma, 1e-9)
        roc_1h = (c[i] / c[i - 1] - 1.0) if c[i - 1] > 0 else 0.0
        mom = 0.55 * _sig(roc * 10.0) + 0.25 * _sig(roc_1h * 8.0) + 0.20 * _sig((v_ratio - 1.0) * 2.0)
        return max(0.0, min(1.0, mom))

    @staticmethod
    def _technical(c: np.ndarray, i: int):
        """Technical alignment sub-score (0..1) at bar i."""
        if i < 26:
            return 0.0
        closes = c[max(0, i - 26):i + 1]
        # RSI(14) with epsilon (audit F5)
        delta = np.diff(closes)
        if len(delta) < 14:
            return 0.0
        d14 = delta[-14:]
        gain = np.mean(np.where(d14 > 0, d14, 0.0))
        loss = np.mean(np.where(d14 < 0, -d14, 0.0))
        rs = gain / max(loss, 1e-9)
        rsi = 100.0 - 100.0 / (1.0 + rs)
        rsi_oversold = 1.0 if rsi < 40 else (0.5 if rsi < 50 else 0.0)

        # MACD
        ema12 = _ema(closes, 12)[-1]
        ema26 = _ema(closes, 26)[-1]
        macd = ema12 - ema26
        macd_bull = 1.0 if macd > 0 else 0.0

        # Bollinger position
        sma20 = np.mean(closes[-20:])
        sd20 = float(np.std(closes[-20:]))
        bb_low, bb_high = sma20 - 2 * sd20, sma20 + 2 * sd20
        bb_pos = (closes[-1] - bb_low) / max(bb_high - bb_low, 1e-9)
        bb_ok = 1.0 if bb_pos < 0.6 else (0.5 if bb_pos < 0.8 else 0.0)

        return 0.4 * rsi_oversold + 0.3 * macd_bull + 0.3 * bb_ok

    # ── Full per-token scoring ─────────────────────────────────

    def score_token(self, md: dict, i: int = -1,
                    whale_conv: float = None, pair: dict = None) -> dict:
        """Composite score for one token at bar index i of its OHLCV.

        Args:
            md: market-data dict from ohlcv_feed.get_token_market_data
            i: bar index into md['ohlcv'] (default -1 = latest)
            whale_conv: precomputed token whale conviction (optional)
            pair: DexScreener pair override (default md['pairs'][0])
        """
        ohlcv = md.get("ohlcv")
        pairs = md.get("pairs") or []
        top_pair = pair or (pairs[0] if pairs else None)

        q = self.quality.evaluate(pairs, top_pair)

        if ohlcv is None:
            # Partial score: quality + whale only (no OHLCV bars available)
            if whale_conv is None:
                whale_conv = self.token_whale_conviction(md.get("mint", ""))
            partial_raw = W_QUALITY * q["score"] + W_WHALE * whale_conv
            partial_raw = max(0.0, min(1.0, partial_raw))
            if not q["passed"]:
                partial_raw = min(partial_raw, REJECT_CAP)
            partial_dir = "BUY" if partial_raw >= BUY_CONVICTION else "WAIT"
            return {
                "symbol": md.get("symbol", "?"),
                "mint": md.get("mint"),
                "direction": partial_dir,
                "decision": "PARTIAL" if partial_raw > 0 else "NO_DATA",
                "conviction": round(partial_raw, 4),
                "quality": q,
                "reasons": list(q["reasons"]) + ["no_ohlcv_partial"],
                "components": {"quality": round(q["score"], 4), "momentum": 0.0,
                               "whale": round(whale_conv, 4), "technical": 0.0},
            }

        idx = i if i >= 0 else len(ohlcv["close"]) - 1
        c, v = ohlcv["close"], ohlcv["volume"]

        mom = self._momentum(c, v, idx)
        tech = self._technical(c, idx)
        if whale_conv is None:
            whale_conv = self.token_whale_conviction(md.get("mint", ""))

        raw = (W_QUALITY * q["score"] + W_MOMENTUM * mom
               + W_WHALE * whale_conv + W_TECH * tech)
        raw = max(0.0, min(1.0, raw))

        # Quality gate: failed tokens get truncated (hard filter on score)
        if not q["passed"]:
            conviction = min(raw, REJECT_CAP)
            decision = "REJECT"
            direction = "WAIT"
        else:
            conviction = raw
            direction = "BUY" if conviction >= BUY_CONVICTION else "WAIT"
            decision = "BUY" if direction == "BUY" else "HOLD"

        reasons = list(q["reasons"])
        if direction == "BUY":
            reasons.append("composite_buy")

        return {
            "symbol": md.get("symbol", "?"),
            "mint": md.get("mint"),
            "direction": direction,
            "decision": decision,
            "conviction": round(conviction, 4),
            "quality": q,
            "reasons": reasons,
            "components": {
                "quality": round(q["score"], 4),
                "momentum": round(mom, 4),
                "whale": round(whale_conv, 4),
                "technical": round(tech, 4),
            },
        }

    def rank(self, mds: list, limit: int = 10) -> list:
        """Score a list of market-data dicts and rank by conviction."""
        scored = []
        for md in mds:
            try:
                scored.append(self.score_token(md))
            except Exception:
                continue
        scored.sort(key=lambda s: s["conviction"], reverse=True)
        return scored[:limit]


def _ema(arr: np.ndarray, span: int) -> np.ndarray:
    """Exponential moving average (pure numpy, adjust=False semantics)."""
    out = np.empty_like(arr, dtype=np.float64)
    out[0] = arr[0]
    alpha = 2.0 / (span + 1.0)
    for k in range(1, len(arr)):
        out[k] = alpha * arr[k] + (1 - alpha) * out[k - 1]
    return out


if __name__ == "__main__":
    import sys
    sys.path.insert(0, __file__.rsplit("/", 1)[0])
    from ohlcv_feed import get_token_market_data

    composite = SmartMoneyComposite()
    for mint in sys.argv[1:] or ["DezXAZ8z7PnrnRJjz3wXBoRgixCa6xjnB7YaB1pPB263"]:
        md = get_token_market_data(mint, days=3)
        s = composite.score_token(md)
        print(f"{s['symbol']:8s} {s['decision']:6s} conv={s['conviction']:.3f} "
              f"q={s['components']['quality']:.3f} mom={s['components']['momentum']:.3f} "
              f"whale={s['components']['whale']:.3f} tech={s['components']['technical']:.3f} "
              f"| {s['reasons'][:3]}")
