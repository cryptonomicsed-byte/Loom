#!/usr/bin/env python3
"""
LOOM Advanced Engines — Liquidity Flow, Contagion Map, Counterfactual,
Precursor Signatures, Rhythm Alerts.

All five engines feed into the Narrative Brief for autonomous intel synthesis.
"""

import time
import json
import hashlib
import threading
from dataclasses import dataclass, field
from typing import Optional, List, Dict, Tuple
from collections import defaultdict, deque
import numpy as np


# ═══════════════════════════════════════════════════════════════
# 1. LIQUIDITY FLOW RIVER
# ═══════════════════════════════════════════════════════════════

@dataclass
class Flow:
    source: str           # "SOL", "USDC", "ETH"
    destination: str      # "TOKEN_X", "SOL memecoins"
    amount_sol: float
    timestamp: float
    wallet: str = ""
    flow_type: str = "unknown"  # rotation, accumulation, exit, bridge

class LiquidityFlowEngine:
    """Tracks money movement between chains and tokens."""

    def __init__(self):
        self.flows: deque = deque(maxlen=1000)
        self.pools: Dict[str, float] = defaultdict(float)  # token → estimated SOL liquidity
        self._lock = threading.Lock()

    def track_flow(self, source: str, destination: str, amount_sol: float,
                   wallet: str = "", flow_type: str = "unknown"):
        flow = Flow(source=source, destination=destination,
                    amount_sol=amount_sol, timestamp=time.time(),
                    wallet=wallet, flow_type=flow_type)
        with self._lock:
            self.flows.append(flow)
            self.pools[destination] += amount_sol
            self.pools[source] = max(0, self.pools.get(source, 0) - amount_sol)

    def detect_river(self, window_sec: int = 300) -> dict:
        """Detect active liquidity river — where money is flowing RIGHT NOW."""
        now = time.time()
        recent = [f for f in self.flows if now - f.timestamp < window_sec]

        if not recent:
            return {"direction": "stagnant", "flow_sol": 0, "destinations": []}

        # Aggregate by destination
        dest_flows = defaultdict(float)
        for f in recent:
            dest_flows[f.destination] += f.amount_sol

        # Find top destinations
        sorted_dests = sorted(dest_flows.items(), key=lambda x: x[1], reverse=True)
        total_flow = sum(dest_flows.values())

        # Detect direction
        if total_flow > 50:
            direction = "heavy_outflow"  # Lots of money moving
        elif total_flow > 10:
            direction = "rotating"
        else:
            direction = "quiet"

        return {
            "direction": direction,
            "flow_sol": round(total_flow, 2),
            "destinations": [
                {"dest": d, "sol": round(a, 2)}
                for d, a in sorted_dests[:5]
            ],
            "dominant_flow": sorted_dests[0][0] if sorted_dests else None,
        }


# ═══════════════════════════════════════════════════════════════
# 2. CONTAGION MAP
# ═══════════════════════════════════════════════════════════════

@dataclass
class CascadeStep:
    wallet: str
    action: str           # entry, exit, amplify, scout
    amount_sol: float
    timestamp: float
    lag_from_leader_ms: float = 0

class ContagionEngine:
    """Traces exactly who follows the whale leader and how fast."""

    def __init__(self):
        self.cascades: Dict[str, List[CascadeStep]] = {}  # leader → steps
        self._lock = threading.Lock()

    def start_cascade(self, leader: str, token: str, amount_sol: float):
        """Mark the start of a cascade with a leader entry."""
        key = f"{leader}:{token}:{int(time.time())}"
        with self._lock:
            self.cascades[key] = [
                CascadeStep(wallet=leader, action="leader_entry",
                           amount_sol=amount_sol, timestamp=time.time(), lag_from_leader_ms=0)
            ]
        return key

    def add_step(self, cascade_key: str, wallet: str, action: str, amount_sol: float):
        """Add a follower to a cascade."""
        with self._lock:
            if cascade_key not in self.cascades:
                return False
            leader_ts = self.cascades[cascade_key][0].timestamp
            lag = (time.time() - leader_ts) * 1000
            self.cascades[cascade_key].append(
                CascadeStep(wallet=wallet, action=action,
                           amount_sol=amount_sol, timestamp=time.time(),
                           lag_from_leader_ms=lag)
            )
            return True

    def get_cascade_analysis(self, cascade_key: str) -> dict:
        """Analyze cascade velocity and pattern."""
        with self._lock:
            steps = self.cascades.get(cascade_key, [])
            if len(steps) < 2:
                return {"velocity": "solo", "followers": 0}

            lags = [s.lag_from_leader_ms for s in steps[1:]]
            avg_lag = sum(lags) / len(lags)
            total_sol = sum(s.amount_sol for s in steps)

            if avg_lag < 5000:
                velocity = "instant"     # < 5 seconds — coordinated
            elif avg_lag < 30000:
                velocity = "fast"        # < 30 seconds — tight cluster
            elif avg_lag < 120000:
                velocity = "moderate"    # < 2 minutes
            else:
                velocity = "slow"        # organic

            return {
                "leader": steps[0].wallet[:8],
                "followers": len(steps) - 1,
                "total_sol": round(total_sol, 2),
                "velocity": velocity,
                "avg_lag_ms": round(avg_lag, 0),
                "percentile": self._velocity_percentile(avg_lag),
                "steps": [
                    {"wallet": s.wallet[:8], "action": s.action, "sol": s.amount_sol, "lag_ms": round(s.lag_from_leader_ms, 0)}
                    for s in steps[:10]
                ],
            }

    def _velocity_percentile(self, avg_lag_ms: float) -> int:
        """What percentile is this cascade velocity?"""
        if avg_lag_ms < 1000: return 99
        if avg_lag_ms < 5000: return 95
        if avg_lag_ms < 15000: return 85
        if avg_lag_ms < 60000: return 70
        if avg_lag_ms < 180000: return 50
        return 30

    def get_active_cascades(self) -> list:
        """Get all active cascades from the last 10 minutes."""
        now = time.time()
        active = []
        for key, steps in self.cascades.items():
            if steps and now - steps[0].timestamp < 600:
                active.append(self.get_cascade_analysis(key))
        return active


# ═══════════════════════════════════════════════════════════════
# 3. COUNTERFACTUAL ENGINE
# ═══════════════════════════════════════════════════════════════

class CounterfactualEngine:
    """Detects gaps between expected outcome and actual outcome."""

    def __init__(self):
        self.history: List[dict] = []       # (wallet, expected_return, actual_return, context)
        self.gaps: deque = deque(maxlen=100)

    def record_outcome(self, wallet: str, expected_return_pct: float,
                       actual_return_pct: float, token: str, context: dict = None):
        """Record an actual outcome vs expected."""
        gap = expected_return_pct - actual_return_pct
        entry = {
            "wallet": wallet[:8],
            "token": token,
            "expected": round(expected_return_pct, 1),
            "actual": round(actual_return_pct, 1),
            "gap": round(gap, 1),
            "gap_direction": "underperformed" if gap > 0 else "overperformed" if gap < 0 else "as_expected",
            "timestamp": time.time(),
            "context": context or {},
        }
        self.history.append(entry)
        if abs(gap) > 50:
            self.gaps.append(entry)

    def get_counterfactual_signals(self) -> list:
        """Find active gaps that suggest catch-up trades."""
        now = time.time()
        signals = []
        for gap in self.gaps:
            if now - gap["timestamp"] < 7200:
                if gap["gap_direction"] == "underperformed" and gap["gap"] > 100:
                    signals.append({
                        "wallet": gap["wallet"],
                        "token": gap["token"],
                        "gap_pp": gap["gap"],
                        "signal": "CATCH_UP",
                        "reason": f"Expected +{gap['expected']}%, actual +{gap['actual']}% — gap of {gap['gap']}pp likely to close",
                        "confidence": min(0.9, gap["gap"] / 300),
                    })
        return sorted(signals, key=lambda s: s["gap_pp"], reverse=True)

    def get_wallet_expected_return(self, wallet: str) -> float:
        """Get historical average return for a wallet."""
        wallet_entries = [e for e in self.history if e["wallet"] == wallet[:8]]
        if not wallet_entries:
            return 0
        return sum(e["expected"] for e in wallet_entries) / len(wallet_entries)


# ═══════════════════════════════════════════════════════════════
# 4. PRECURSOR SIGNATURE MATCHER
# ═══════════════════════════════════════════════════════════════

@dataclass
class Signature:
    sig_id: str
    name: str
    event_sequence: list     # Ordered list of event types that lead to outcome
    outcome: str             # What happens after: pump, dump, breakout, nothing
    occurrences: int = 0
    successes: int = 0
    avg_return_pct: float = 0.0
    avg_time_to_outcome_sec: float = 0.0
    first_seen: float = 0.0
    last_seen: float = 0.0

    @property
    def accuracy(self) -> float:
        return self.successes / max(1, self.occurrences)

class PrecursorEngine:
    """Learns and matches precursor signatures that predict market moves."""

    def __init__(self):
        self.signatures: Dict[str, Signature] = {}

    def learn_from_episode(self, episode_events: list, outcome: str,
                          return_pct: float = 0, time_to_outcome: float = 0):
        """Learn a new signature from an observed outcome."""
        # Create canonical sequence (event types only, no magnitudes)
        seq = tuple(e.get("type", "") for e in episode_events)
        if len(seq) < 3:
            return

        sig_hash = hashlib.md5(str(seq).encode()).hexdigest()[:12]

        if sig_hash in self.signatures:
            sig = self.signatures[sig_hash]
            sig.occurrences += 1
            sig.last_seen = time.time()
            sig.avg_return_pct = (sig.avg_return_pct * (sig.occurrences - 1) + return_pct) / sig.occurrences
            sig.avg_time_to_outcome_sec = (sig.avg_time_to_outcome_sec * (sig.occurrences - 1) + time_to_outcome) / sig.occurrences
            if (outcome == "pump" and return_pct > 50) or (outcome == "dump" and return_pct < -30):
                sig.successes += 1
        else:
            sig = Signature(
                sig_id=sig_hash,
                name=f"sig_{sig_hash[:6]}",
                event_sequence=list(seq),
                outcome=outcome,
                occurrences=1,
                successes=1 if return_pct > 50 else 0,
                avg_return_pct=return_pct,
                avg_time_to_outcome_sec=time_to_outcome,
                first_seen=time.time(),
                last_seen=time.time(),
            )
            self.signatures[sig_hash] = sig

    def match(self, current_events: list, min_similarity: float = 0.6) -> dict:
        """Match current event sequence against known signatures."""
        if len(current_events) < 3:
            return {"matched": False, "reason": "too few events"}

        current_seq = tuple(e.get("type", "") for e in current_events[-10:])

        best_match = None
        best_score = 0

        for sig_id, sig in self.signatures.items():
            if sig.occurrences < 2:
                continue

            # Compute sequence similarity (common prefix + overall overlap)
            sig_seq = sig.event_sequence

            # Count matching positions
            matches = 0
            total = min(len(current_seq), len(sig_seq))
            for i in range(total):
                if current_seq[-(i+1)] == sig_seq[-(i+1)]:
                    matches += 1

            score = matches / max(total, 1)

            if score > best_score and score >= min_similarity:
                best_score = score
                best_match = {
                    "sig_id": sig_id,
                    "name": sig.name,
                    "similarity": round(score, 3),
                    "expected_outcome": sig.outcome,
                    "accuracy": round(sig.accuracy, 3),
                    "occurrences": sig.occurrences,
                    "avg_return_pct": round(sig.avg_return_pct, 1),
                    "avg_time_sec": round(sig.avg_time_to_outcome_sec, 0),
                }

        if best_match:
            return {"matched": True, **best_match}
        return {"matched": False, "reason": "no matching signature"}


# ═══════════════════════════════════════════════════════════════
# 5. RHYTHM VIOLATION ALERTS
# ═══════════════════════════════════════════════════════════════

class RhythmAlertEngine:
    """Generates alerts when whales break their own behavioral patterns."""

    def __init__(self):
        self.alerts: deque = deque(maxlen=100)

    def check_whale(self, dossier: dict) -> Optional[dict]:
        """Check if a whale is violating its known rhythm."""
        violations = []
        now = time.time()

        # Active hours check
        active_hours = dossier.get("active_hours", [])
        if active_hours:
            from datetime import datetime
            current_hour = datetime.fromtimestamp(now).hour
            if current_hour not in active_hours and len(active_hours) >= 3:
                violations.append({
                    "type": "hour_violation",
                    "detail": f"trading at {current_hour}:00 UTC (normal: {sorted(active_hours)[:3]})",
                    "severity": "high",
                })

        # Size check
        typical_size = dossier.get("typical_entry_sol", 0)
        last_entry = dossier.get("last_entry_sol", 0)
        if typical_size > 0 and last_entry > typical_size * 2.5:
            violations.append({
                "type": "size_violation",
                "detail": f"entry {last_entry:.1f} SOL, typical {typical_size:.1f} SOL",
                "severity": "high" if last_entry > typical_size * 4 else "medium",
            })

        # Activity spike
        recent_trades = dossier.get("recent_trades", [])
        if len(recent_trades) >= 5:
            recent_24h = [t for t in recent_trades if now - t.get("entry_time", 0) < 86400]
            if len(recent_24h) > dossier.get("total_trades", 1) * 0.3:
                violations.append({
                    "type": "activity_spike",
                    "detail": f"{len(recent_24h)} trades in 24h (30%+ of lifetime)",
                    "severity": "medium",
                })

        if violations:
            alert = {
                "wallet": dossier.get("address", "?")[:8],
                "tier": dossier.get("tier", 4),
                "violations": violations,
                "overall_severity": "high" if any(v["severity"] == "high" for v in violations) else "medium",
                "timestamp": now,
                "message": f"Whale {dossier.get('address', '?')[:8]} breaking patterns: "
                          + "; ".join(v["detail"] for v in violations),
            }
            self.alerts.append(alert)
            return alert
        return None

    def get_active_alerts(self, max_age: int = 3600) -> list:
        now = time.time()
        return [a for a in self.alerts if now - a["timestamp"] < max_age]


# ═══════════════════════════════════════════════════════════════
# 6. NARRATIVE BRIEF
# ═══════════════════════════════════════════════════════════════

class NarrativeBrief:
    """Synthesizes all engines into an autonomous intel report."""

    def __init__(self, narrative_engine=None, flow_engine=None,
                 contagion_engine=None, counterfactual_engine=None,
                 precursor_engine=None, rhythm_engine=None,
                 whale_engine=None, trenches_monitor=None,
                 omniroute=None):
        self.narrative = narrative_engine
        self.flow = flow_engine
        self.contagion = contagion_engine
        self.counterfactual = counterfactual_engine
        self.precursor = precursor_engine
        self.rhythm = rhythm_engine
        self.whales = whale_engine
        self.trenches = trenches_monitor
        self.omniroute = omniroute  # OmniRouteBridge for LLM synthesis

    def generate(self) -> str:
        """Generate the full intel brief. Uses LLM if OmniRoute is available."""
        if self.omniroute and self.omniroute.available:
            return self._generate_llm()
        return self._generate_template()

    def _generate_template(self) -> str:
        """Template-based brief (fallback when no LLM)."""
        lines = [
            "╔══════════════════════════════════════════════════════╗",
            "║         LOOM WHALE INTEL BRIEF                       ║",
            f"║         {time.strftime('%Y-%m-%d %H:%M UTC', time.gmtime())}                          ║",
            "╚══════════════════════════════════════════════════════╝",
            "",
        ]

        # ── Critical Alerts ──
        lines.append("🔴 CRITICAL")
        if self.trenches:
            alerts = self.trenches.get_active_alerts(max_age=120)
            for a in alerts[:3]:
                lines.append(f"  [{a['level']}] {a['symbol']} — score {a['alpha_score']:.2f} "
                           f"— {a['buyer_count']} buyers, {a['known_whales']} whales "
                           f"— {a['action']}")
        if not alerts:
            lines.append("  No active pump.fun alerts.")

        # ── Whales ──
        lines.append("\n🐋 WHALE ACTIVITY")
        if self.whales:
            lb = self.whales.get_leaderboard(5)
            for w in lb:
                lines.append(f"  [{w['label']}] {w['addr']} — T{w['tier']} PV={w['pv']:.3f} WR={w['wr']:.0f}%")
        if self.rhythm:
            rhythm_alerts = self.rhythm.get_active_alerts()
            for a in rhythm_alerts[:3]:
                lines.append(f"  ⚠️  RHYTHM BREAK: {a['message']}")

        # ── Liquidity ──
        lines.append("\n💧 LIQUIDITY FLOW")
        if self.flow:
            river = self.flow.detect_river()
            lines.append(f"  Direction: {river['direction']} ({river['flow_sol']:.1f} SOL)")
            for d in river.get("destinations", [])[:3]:
                lines.append(f"  → {d['dest']} : {d['sol']:.1f} SOL")

        # ── Contagion ──
        lines.append("\n🦠 CONTAGION")
        if self.contagion:
            cascades = self.contagion.get_active_cascades()
            for c in cascades[:3]:
                lines.append(f"  {c['leader']} → {c['followers']} followers "
                           f"({c['velocity']}, {c['avg_lag_ms']:.0f}ms avg lag, "
                           f"{c['percentile']}th percentile)")

        # ── Counterfactual ──
        lines.append("\n📊 COUNTERFACTUAL GAPS")
        if self.counterfactual:
            signals = self.counterfactual.get_counterfactual_signals()
            for s in signals[:3]:
                lines.append(f"  {s['wallet']} on {s['token']}: gap {s['gap_pp']}pp — {s['signal']} "
                           f"(confidence {s['confidence']:.2f})")
        if not signals:
            lines.append("  No significant gaps detected.")

        # ── Narrative ──
        lines.append("\n📖 NARRATIVE")
        if self.narrative:
            lines.append(self.narrative.get_narrative_summary())

        lines.append("\n" + "═" * 56)
        return "\n".join(lines)

    def _generate_llm(self) -> str:
        """Generate brief using OmniRoute LLM for natural language synthesis."""
        # Collect structured data
        data = {
            "timestamp": time.strftime('%Y-%m-%d %H:%M UTC', time.gmtime()),
            "trenches": self.trenches.get_active_alerts() if self.trenches else [],
            "whales": self.whales.get_leaderboard(5) if self.whales else [],
            "liquidity": self.flow.detect_river() if self.flow else {},
            "contagion": self.contagion.get_active_cascades() if self.contagion else [],
            "counterfactual": self.counterfactual.get_counterfactual_signals() if self.counterfactual else [],
            "rhythm_alerts": self.rhythm.get_active_alerts() if self.rhythm else [],
        }

        llm_brief = self.omniroute.synthesize_narrative(data)
        return llm_brief

    def generate_json(self) -> dict:
        """Generate structured brief for API consumption."""
        brief = {
            "timestamp": time.time(),
            "trenches": self.trenches.get_active_alerts() if self.trenches else [],
            "whales": self.whales.get_leaderboard(10) if self.whales else [],
            "liquidity": self.flow.detect_river() if self.flow else {},
            "contagion": self.contagion.get_active_cascades() if self.contagion else [],
            "counterfactual": self.counterfactual.get_counterfactual_signals() if self.counterfactual else [],
            "rhythm_alerts": self.rhythm.get_active_alerts() if self.rhythm else [],
        }
        if self.narrative:
            brief["narrative"] = self.narrative.get_state()
        return brief


# ═══════════════════════════════════════════════════════════════
# GLOBAL INSTANCES
# ═══════════════════════════════════════════════════════════════

flow_engine = LiquidityFlowEngine()
contagion_engine = ContagionEngine()
counterfactual_engine = CounterfactualEngine()
precursor_engine = PrecursorEngine()
rhythm_engine = RhythmAlertEngine()
