"""
LOOM Fabric — Agent-native semantic event bus.
Transforms raw market data into structured events agents can reason about.

Architecture:
  Sources → Event Processor → Causal Engine → Pattern Memory → WebSocket Stream
              ↓                    ↓                ↓
         Agent Annotations   Temporal Context   Anomaly Detection

All data stays local. VPS accessed via SSH for source ingestion.
"""

import json
import time
import hashlib
import threading
import queue
from dataclasses import dataclass, field, asdict
from typing import Optional
from collections import deque


# ═══════════════════════════════════════════════════════════════
# EVENT SCHEMA
# ═══════════════════════════════════════════════════════════════

@dataclass
class MarketEvent:
    event_id: str
    event_type: str       # price_surge, volume_spike, whale_move, pattern_detected, agent_signal, anomaly
    entity: str           # BTC/USD, SOL, NEIL, etc.
    symbol: str
    magnitude: float      # normalized 0-1 impact
    confidence: float     # 0-1 belief
    timestamp: float
    source: str           # radar, coingecko, kraken, hyperliquid, etc.
    causal_parents: list = field(default_factory=list)  # upstream event IDs
    causal_children: list = field(default_factory=list)  # downstream event IDs
    agent_annotations: dict = field(default_factory=dict)  # {agent_name: {note, conviction, action}}
    correlations: dict = field(default_factory=dict)      # {entity: strength}
    temporal_tags: list = field(default_factory=list)     # ["breakout_retest", "accumulation", "distribution"]
    anomaly_score: float = 0.0
    metadata: dict = field(default_factory=dict)          # raw source data

    def to_dict(self):
        d = asdict(self)
        d["_type"] = "market_event"
        return d

    def to_compact(self):
        """Lightweight version for WebSocket stream"""
        return {
            "id": self.event_id[:8],
            "t": self.event_type,
            "e": self.entity,
            "s": self.symbol,
            "m": round(self.magnitude, 3),
            "c": round(self.confidence, 3),
            "ts": self.timestamp,
            "src": self.source,
            "parents": [p[:8] for p in self.causal_parents],
            "anom": round(self.anomaly_score, 3),
        }


# ═══════════════════════════════════════════════════════════════
# CAUSAL ENGINE
# ═══════════════════════════════════════════════════════════════

class CausalEngine:
    """Builds causal chains between events. When A happens then B happens
    with temporal proximity, link them. Surfaces correlations."""

    def __init__(self, window_seconds: int = 300):
        self.window = window_seconds
        self.recent: deque = deque(maxlen=500)
        self.correlation_matrix: dict = {}  # {(entity_a, entity_b): strength}
        self.causal_graph: dict = {}        # {event_id: [child_event_ids]}

    def link(self, event: MarketEvent, all_recent: list) -> MarketEvent:
        """Find causal parents for this event."""
        now = event.timestamp
        for past in reversed(all_recent):
            if now - past.timestamp > self.window:
                break
            if past.event_id == event.event_id:
                continue

            # Same entity → temporal causality
            if past.entity == event.entity:
                strength = self._causal_strength(past, event)
                if strength > 0.3:
                    event.causal_parents.append(past.event_id)
                    past.causal_children.append(event.event_id)

            # Cross-entity correlation
            if past.entity != event.entity:
                corr_key = tuple(sorted([past.entity, event.entity]))
                self.correlation_matrix[corr_key] = (
                    self.correlation_matrix.get(corr_key, 0) * 0.9 + 0.1
                )
                event.correlations[past.entity] = round(
                    self.correlation_matrix.get(corr_key, 0), 3
                )

        self.recent.append(event)
        return event

    def _causal_strength(self, parent: MarketEvent, child: MarketEvent) -> float:
        """Score causal relationship between two events."""
        time_gap = child.timestamp - parent.timestamp
        time_factor = max(0, 1 - (time_gap / self.window))

        # Same source = stronger causal link
        source_factor = 0.8 if parent.source == child.source else 0.4

        # Magnitude amplification = stronger link
        mag_factor = min(1.0, child.magnitude / max(0.01, parent.magnitude))

        return round((time_factor * 0.4 + source_factor * 0.3 + mag_factor * 0.3), 3)

    def get_causal_chain(self, event_id: str, depth: int = 3) -> dict:
        """Trace causal chain from an event."""
        chain = {"event": event_id[:8], "upstream": [], "downstream": []}
        visited = set()

        # Walk upstream
        current = event_id
        for _ in range(depth):
            found = False
            for e in self.recent:
                if e.event_id == current and e.causal_parents:
                    parent_id = e.causal_parents[0]
                    if parent_id not in visited:
                        chain["upstream"].append(parent_id[:8])
                        visited.add(parent_id)
                        current = parent_id
                        found = True
                        break
            if not found:
                break

        # Walk downstream
        current = event_id
        for _ in range(depth):
            kids = self.causal_graph.get(current, [])
            if kids:
                kid = kids[0]
                if kid not in visited:
                    chain["downstream"].append(kid[:8])
                    visited.add(kid)
                    current = kid
                else:
                    break
            else:
                break

        return chain

    def get_correlations(self, entity: str, top_n: int = 10) -> list:
        """Get top correlated entities."""
        scores = []
        for (a, b), strength in self.correlation_matrix.items():
            if a == entity:
                scores.append((b, strength))
            elif b == entity:
                scores.append((a, strength))
        scores.sort(key=lambda x: x[1], reverse=True)
        return [{"entity": e, "strength": round(s, 3)} for e, s in scores[:top_n]]


# ═══════════════════════════════════════════════════════════════
# PATTERN MEMORY
# ═══════════════════════════════════════════════════════════════

class PatternMemory:
    """Stores event patterns and detects recurrences. Agents can query:
    'Has this pattern happened before? What was the outcome?'"""

    def __init__(self, max_patterns: int = 1000):
        self.patterns: deque = deque(maxlen=max_patterns)
        self.event_history: deque = deque(maxlen=5000)
        self.pattern_index: dict = {}  # {pattern_hash: [event_ids]}

    def ingest(self, event: MarketEvent):
        """Store event in history."""
        self.event_history.append(event)

    def detect_pattern(self, window_events: int = 5) -> Optional[dict]:
        """Check if current event sequence matches a known pattern."""
        if len(self.event_history) < window_events:
            return None

        recent = list(self.event_history)[-window_events:]
        pattern_key = "|".join(f"{e.event_type}:{e.entity}" for e in recent)
        pattern_hash = hashlib.md5(pattern_key.encode()).hexdigest()[:12]

        if pattern_hash in self.pattern_index:
            return {
                "pattern_id": pattern_hash,
                "occurrences": len(self.pattern_index[pattern_hash]),
                "first_seen": self.pattern_index[pattern_hash][0],
                "signature": pattern_key,
            }

        # New pattern
        self.pattern_index[pattern_hash] = [recent[0].event_id]
        self.patterns.append({
            "hash": pattern_hash,
            "signature": pattern_key,
            "first_event": recent[0].event_id,
            "timestamp": recent[0].timestamp,
        })
        return None

    def find_similar(self, event: MarketEvent, limit: int = 5) -> list:
        """Find historically similar events."""
        similar = []
        for past in reversed(self.event_history):
            if past.event_id == event.event_id:
                continue
            if past.event_type == event.event_type and past.entity == event.entity:
                similar.append({
                    "event_id": past.event_id[:8],
                    "timestamp": past.timestamp,
                    "magnitude": past.magnitude,
                    "anomaly_score": past.anomaly_score,
                })
            if len(similar) >= limit:
                break
        return similar


# ═══════════════════════════════════════════════════════════════
# ANOMALY DETECTOR
# ═══════════════════════════════════════════════════════════════

class AnomalyDetector:
    """Detects events that deviate from expected patterns."""

    def __init__(self):
        self.baselines: dict = {}  # {entity: {event_type: avg_magnitude}}
        self.event_counts: dict = {}

    def score(self, event: MarketEvent) -> float:
        """Score how anomalous this event is. 0 = normal, 1 = highly unusual."""
        key = (event.entity, event.event_type)
        count = self.event_counts.get(key, 0) + 1
        self.event_counts[key] = count

        # First few events of this type → not enough data, low anomaly
        if count < 5:
            return 0.05

        # Compare magnitude to baseline
        baseline = self.baselines.get(event.entity, {}).get(event.event_type)
        if baseline is None:
            self.baselines.setdefault(event.entity, {})[event.event_type] = event.magnitude
            return 0.1

        # Update running average
        alpha = 0.1
        new_baseline = baseline * (1 - alpha) + event.magnitude * alpha
        self.baselines[event.entity][event.event_type] = new_baseline

        # Deviation from baseline
        if baseline > 0:
            deviation = abs(event.magnitude - baseline) / baseline
            return min(1.0, round(deviation * 0.5, 3))

        return 0.0


# ═══════════════════════════════════════════════════════════════
# EVENT PROCESSOR
# ═══════════════════════════════════════════════════════════════

class EventProcessor:
    """Converts raw data from sources into structured MarketEvents."""

    def __init__(self):
        self.event_counter = 0

    def _make_id(self) -> str:
        self.event_counter += 1
        return hashlib.md5(f"{time.time()}:{self.event_counter}".encode()).hexdigest()

    def from_signal(self, signal: dict) -> MarketEvent:
        """Convert Ares alpha signal to MarketEvent."""
        conv = signal.get("conviction", 0)
        score = signal.get("score", conv)
        change = signal.get("change_6h", 0)
        event_type = "price_surge" if change > 20 else ("agent_signal" if conv >= 4 else "volume_spike")

        return MarketEvent(
            event_id=self._make_id(),
            event_type=event_type,
            entity=f"{signal.get('symbol','?')}/USD",
            symbol=signal.get("symbol", "?"),
            magnitude=min(1.0, max(0.0, (abs(change) / 100 if change else conv / 10))),
            confidence=min(1.0, max(0.1, score / 10)),
            timestamp=signal.get("ts", time.time()),
            source=signal.get("source", "radar"),
            temporal_tags=["trending"] if signal.get("type") == "trending" else [],
            metadata={
                "price": signal.get("price"),
                "volume_24h": signal.get("volume_24h"),
                "liquidity": signal.get("liquidity"),
                "change_6h": change,
                "age_hours": signal.get("age_hours"),
                "dex_url": signal.get("url", ""),
            },
        )

    def from_price_move(self, coin: dict) -> Optional[MarketEvent]:
        """Convert CoinGecko price data to MarketEvent if significant."""
        change = abs(coin.get("price_change_percentage_24h", 0))
        if change < 2:  # Filter noise
            return None

        return MarketEvent(
            event_id=self._make_id(),
            event_type="price_surge" if change > 5 else "price_move",
            entity=f"{coin.get('symbol','?').upper()}/USD",
            symbol=coin.get("symbol", "?").upper(),
            magnitude=min(1.0, change / 20),
            confidence=0.7,
            timestamp=time.time(),
            source="coingecko",
            temporal_tags=["volatile"] if change > 10 else [],
            metadata={
                "price": coin.get("current_price"),
                "market_cap": coin.get("market_cap"),
                "volume_24h": coin.get("total_volume"),
                "change_24h": change,
                "change_7d": coin.get("price_change_percentage_7d_in_currency"),
            },
        )

    def from_freqtrade(self, trade: dict) -> MarketEvent:
        """Convert Freqtrade trade to MarketEvent."""
        pnl = trade.get("close_profit", 0) or 0
        return MarketEvent(
            event_id=self._make_id(),
            event_type="trade_executed",
            entity=trade.get("pair", "?"),
            symbol=trade.get("pair", "?").split("/")[0] if "/" in str(trade.get("pair", "")) else "?",
            magnitude=min(1.0, abs(pnl) * 100),
            confidence=1.0,
            timestamp=time.time(),
            source="freqtrade",
            temporal_tags=["win"] if pnl > 0 else (["loss"] if pnl < 0 else []),
            metadata={
                "profit": pnl,
                "open_date": trade.get("open_date"),
                "close_date": trade.get("close_date"),
                "is_short": trade.get("is_short"),
            },
        )


# ═══════════════════════════════════════════════════════════════
# AGENT ANNOTATION LAYER
# ═══════════════════════════════════════════════════════════════

class AnnotationLayer:
    """Agents can annotate events. Annotations fuse into collective intelligence."""

    def __init__(self):
        self.annotations: dict = {}  # {event_id: {agent: annotation}}

    def annotate(self, event_id: str, agent_name: str, note: str,
                 conviction: float = 0.5, action: str = "observe"):
        """Agent adds an observation to an event."""
        if event_id not in self.annotations:
            self.annotations[event_id] = {}
        self.annotations[event_id][agent_name] = {
            "note": note,
            "conviction": conviction,
            "action": action,
            "timestamp": time.time(),
        }

    def get_collective_conviction(self, event_id: str) -> float:
        """Fuse multiple agent annotations into collective conviction score."""
        anns = self.annotations.get(event_id, {})
        if not anns:
            return 0.0
        return round(sum(a["conviction"] for a in anns.values()) / len(anns), 3)

    def get_annotations(self, event_id: str) -> dict:
        return self.annotations.get(event_id, {})


# ═══════════════════════════════════════════════════════════════
# LOOM FABRIC (Main Orchestrator)
# ═══════════════════════════════════════════════════════════════

class LoomFabric:
    """Central fabric that threads everything together."""

    def __init__(self):
        self.processor = EventProcessor()
        self.causal = CausalEngine(window_seconds=600)
        self.memory = PatternMemory()
        self.anomaly = AnomalyDetector()
        self.annotations = AnnotationLayer()
        self.event_queue: queue.Queue = queue.Queue()
        self.subscribers: list = []  # WebSocket clients
        self._lock = threading.Lock()

    def ingest_signal(self, signal: dict):
        """Ingest a raw Ares signal."""
        event = self.processor.from_signal(signal)
        self._process(event)

    def ingest_price_move(self, coin: dict):
        """Ingest a CoinGecko price change."""
        event = self.processor.from_price_move(coin)
        if event:
            self._process(event)

    def ingest_trade(self, trade: dict):
        """Ingest a Freqtrade trade."""
        event = self.processor.from_freqtrade(trade)
        self._process(event)

    def _process(self, event: MarketEvent):
        """Full processing pipeline."""
        # Anomaly detection
        event.anomaly_score = self.anomaly.score(event)

        # Causal linking
        recent_list = list(self.causal.recent)
        event = self.causal.link(event, recent_list)

        # Pattern memory
        self.memory.ingest(event)
        pattern_match = self.memory.detect_pattern()

        # Pattern annotation
        if pattern_match:
            event.temporal_tags.append("pattern_repeat")

        # Similar history
        similar = self.memory.find_similar(event)

        # Auto-annotate with collective intelligence
        event.agent_annotations = self.annotations.get_annotations(event.event_id)

        # Build enriched output
        enriched = {
            "event": event.to_compact(),
            "causal_chain": self.causal.get_causal_chain(event.event_id),
            "correlations": self.causal.get_correlations(event.entity),
            "pattern": pattern_match,
            "similar_past": similar,
            "collective_conviction": self.annotations.get_collective_conviction(event.event_id),
        }

        # Push to subscribers
        with self._lock:
            self.event_queue.put(enriched)
            for sub in self.subscribers[:]:  # copy to avoid mutation during iteration
                try:
                    sub(enriched)
                except Exception:
                    self.subscribers.remove(sub)

    def subscribe(self, callback):
        """Register a WebSocket callback."""
        with self._lock:
            self.subscribers.append(callback)

    def unsubscribe(self, callback):
        """Remove a WebSocket callback."""
        with self._lock:
            if callback in self.subscribers:
                self.subscribers.remove(callback)

    def get_state(self) -> dict:
        """Return current fabric state for REST API."""
        recent = list(self.causal.recent)[-50:]
        return {
            "event_count": self.processor.event_counter,
            "recent_events": [e.to_dict() for e in recent],
            "anomalies": [e.to_dict() for e in recent if e.anomaly_score > 0.5],
            "top_correlations": self.causal.get_correlations("BTC/USD")[:10],
            "pattern_count": len(self.memory.patterns),
            "queue_size": self.event_queue.qsize(),
        }


# Global fabric instance
fabric = LoomFabric()
