#!/usr/bin/env python3
"""
LOOM Narrative Engine — Compresses raw events into temporal stories.

Hierarchy:
  Event (tick)    → single whale action (entry, exit, signal)
  Episode (15min) → clustered events forming a pattern
  Arc (4h)        → series of related episodes
  Cycle (7d)      → market regime shift

The engine continuously ingests events, groups them by entity
and temporal proximity, and outputs structured narratives that
agents can query instead of raw event streams.
"""

import time
import json
import hashlib
import threading
from dataclasses import dataclass, field
from typing import Optional, List, Dict
from collections import defaultdict, deque
import numpy as np


# ═══════════════════════════════════════════════════════════════
# NARRATIVE DATA TYPES
# ═══════════════════════════════════════════════════════════════

@dataclass
class Episode:
    """A cluster of related events within a short time window."""
    episode_id: str
    entity: str                      # primary entity (whale, token, cluster)
    entity_type: str                 # whale, token, cluster
    events: list = field(default_factory=list)
    start_time: float = 0.0
    end_time: float = 0.0
    event_count: int = 0
    dominant_pattern: str = "unknown"  # accumulation, distribution, pump, dump
    conviction_score: float = 0.0
    anomaly_score: float = 0.0
    narrative: str = ""               # auto-generated one-liner
    parent_arc_id: Optional[str] = None

    def to_dict(self):
        return {
            "id": self.episode_id[:8],
            "entity": self.entity,
            "type": self.entity_type,
            "pattern": self.dominant_pattern,
            "events": self.event_count,
            "duration_sec": int(self.end_time - self.start_time),
            "conviction": round(self.conviction_score, 3),
            "anomaly": round(self.anomaly_score, 3),
            "narrative": self.narrative,
            "start": self.start_time,
        }


@dataclass
class Arc:
    """A series of related episodes forming a coherent narrative thread."""
    arc_id: str
    entity: str
    entity_type: str
    episodes: list = field(default_factory=list)
    start_time: float = 0.0
    end_time: float = 0.0
    episode_count: int = 0
    arc_type: str = "unknown"          # accumulation_arc, distribution_arc, rotation_arc
    collective_conviction: float = 0.0
    narrative: str = ""
    parent_cycle_id: Optional[str] = None

    def to_dict(self):
        return {
            "id": self.arc_id[:8],
            "entity": self.entity,
            "type": self.arc_type,
            "episodes": self.episode_count,
            "duration_hours": round((self.end_time - self.start_time) / 3600, 1),
            "conviction": round(self.collective_conviction, 3),
            "narrative": self.narrative,
        }


@dataclass
class Cycle:
    """A market regime shift spanning multiple arcs."""
    cycle_id: str
    cycle_type: str                   # risk_on, risk_off, rotation, accumulation
    arcs: list = field(default_factory=list)
    start_time: float = 0.0
    end_time: float = 0.0
    arc_count: int = 0
    dominant_chain: str = "solana"
    narrative: str = ""
    confidence: float = 0.0

    def to_dict(self):
        return {
            "id": self.cycle_id[:8],
            "type": self.cycle_type,
            "arcs": self.arc_count,
            "duration_days": round((self.end_time - self.start_time) / 86400, 1),
            "chain": self.dominant_chain,
            "confidence": round(self.confidence, 3),
            "narrative": self.narrative,
        }


# ═══════════════════════════════════════════════════════════════
# NARRATIVE ENGINE
# ═══════════════════════════════════════════════════════════════

class NarrativeEngine:
    """
    Ingests events → groups into episodes → chains into arcs → detects cycles.

    Event ingestion happens in real-time. The engine continuously
    checks if new events belong to existing episodes or start new ones.
    """

    def __init__(self, max_episodes: int = 500, max_arcs: int = 100, max_cycles: int = 20):
        self.active_episodes: Dict[str, Episode] = {}
        self.completed_episodes: deque = deque(maxlen=max_episodes)
        self.active_arcs: Dict[str, Arc] = {}
        self.completed_arcs: deque = deque(maxlen=max_arcs)
        self.cycles: deque = deque(maxlen=max_cycles)

        # Episode params
        self.episode_window = 900       # 15 minutes max for one episode
        self.episode_max_gap = 120      # 2 minutes between events to merge
        self.episode_min_events = 3     # Minimum events to form an episode

        # Arc params
        self.arc_window = 14400         # 4 hours max for one arc
        self.arc_min_episodes = 2       # Minimum episodes to form an arc

        # Cycle params
        self.cycle_window = 604800      # 7 days
        self.cycle_min_arcs = 2

        self._lock = threading.Lock()
        self._last_cycle_check = time.time()

    def ingest(self, event_type: str, entity: str, entity_type: str = "token",
               magnitude: float = 0.0, source: str = "unknown",
               metadata: dict = None) -> Optional[Episode]:
        """
        Ingest a single event. Returns an Episode if one was completed.
        """
        now = time.time()
        metadata = metadata or {}

        with self._lock:
            # Find or create episode for this entity
            ep_key = f"{entity_type}:{entity}"

            if ep_key in self.active_episodes:
                ep = self.active_episodes[ep_key]
                gap = now - ep.end_time

                if gap <= self.episode_max_gap:
                    # Extend existing episode
                    ep.events.append({
                        "type": event_type, "ts": now,
                        "magnitude": magnitude, "source": source,
                        "meta": metadata,
                    })
                    ep.end_time = now
                    ep.event_count = len(ep.events)
                    self._update_episode_pattern(ep)
                    self._generate_episode_narrative(ep)
                    return None  # Episode still open

                else:
                    # Gap too large — close this episode, start new one
                    completed = self._close_episode(ep_key)

                    # Check if completed episode should start/join an arc
                    if completed:
                        self._process_arc(completed)

                    # Start new episode
                    new_ep = self._start_episode(entity, entity_type, now, event_type,
                                                 magnitude, source, metadata)
                    self.active_episodes[ep_key] = new_ep
                    return completed

            else:
                # No active episode — start one
                ep = self._start_episode(entity, entity_type, now, event_type,
                                        magnitude, source, metadata)
                self.active_episodes[ep_key] = ep
                return None

    def _start_episode(self, entity, entity_type, now, event_type, mag, source, meta):
        ep = Episode(
            episode_id=hashlib.md5(f"{entity}{now}".encode()).hexdigest()[:12],
            entity=entity,
            entity_type=entity_type,
            events=[{"type": event_type, "ts": now, "magnitude": mag, "source": source, "meta": meta}],
            start_time=now,
            end_time=now,
            event_count=1,
        )
        return ep

    def _close_episode(self, ep_key: str) -> Optional[Episode]:
        """Finalize an episode, compute its pattern, and archive it."""
        ep = self.active_episodes.pop(ep_key, None)
        if not ep or ep.event_count < self.episode_min_events:
            return None  # Not enough events to matter

        self._update_episode_pattern(ep)
        self._generate_episode_narrative(ep)
        self.completed_episodes.append(ep)
        return ep

    def _update_episode_pattern(self, ep: Episode):
        """Classify the episode's dominant pattern from its events."""
        types = [e["type"] for e in ep.events]
        magnitudes = [e.get("magnitude", 0) for e in ep.events]

        buy_like = sum(1 for t in types if t in (
            "whale_entry", "scout_entry", "amplifier_entry", "leader_entry",
            "crowd_arrive", "price_surge", "agent_signal",
        ))
        sell_like = sum(1 for t in types if t in (
            "whale_exit", "distribution", "anomaly",
        ))

        if buy_like > sell_like * 2:
            ep.dominant_pattern = "accumulation"
        elif sell_like > buy_like * 2:
            ep.dominant_pattern = "distribution"
        elif buy_like > 0 and sell_like > 0:
            ep.dominant_pattern = "volatile"
        else:
            ep.dominant_pattern = "neutral"

        ep.conviction_score = min(1.0, ep.event_count / 15)
        ep.anomaly_score = sum(1 for t in types if t == "anomaly") / max(1, len(types))

    def _generate_episode_narrative(self, ep: Episode):
        """Generate a one-line narrative for the episode."""
        duration = int(ep.end_time - ep.start_time)
        minutes = duration // 60
        seconds = duration % 60

        if ep.entity_type == "whale":
            ep.narrative = (
                f"{ep.entity[:8]} {ep.dominant_pattern} — "
                f"{ep.event_count} events in {minutes}m{seconds}s"
            )
        elif ep.entity_type == "token":
            ep.narrative = (
                f"{ep.entity} {ep.dominant_pattern} episode — "
                f"{ep.event_count} events, conviction {ep.conviction_score:.2f}"
            )
        elif ep.entity_type == "cluster":
            ep.narrative = (
                f"Cluster {ep.entity[:8]} {ep.dominant_pattern} — "
                f"{ep.event_count} coordinated events"
            )
        else:
            ep.narrative = f"{ep.dominant_pattern} — {ep.event_count} events"

    def _process_arc(self, episode: Episode):
        """Check if this episode starts or extends an arc."""
        now = episode.end_time
        arc_key = f"{episode.entity_type}:{episode.entity}"

        if arc_key in self.active_arcs:
            arc = self.active_arcs[arc_key]
            gap = now - arc.end_time

            if gap <= self.arc_window:
                arc.episodes.append(episode)
                arc.end_time = now
                arc.episode_count = len(arc.episodes)
                episode.parent_arc_id = arc.arc_id
                self._update_arc_type(arc)
                return

            # Gap too large — close old arc
            completed_arc = self.active_arcs.pop(arc_key, None)
            if completed_arc and completed_arc.episode_count >= self.arc_min_episodes:
                self.completed_arcs.append(completed_arc)
                self._check_cycle(completed_arc)

        # Start new arc
        new_arc = Arc(
            arc_id=hashlib.md5(f"{arc_key}{now}".encode()).hexdigest()[:12],
            entity=episode.entity,
            entity_type=episode.entity_type,
            episodes=[episode],
            start_time=episode.start_time,
            end_time=now,
            episode_count=1,
        )
        episode.parent_arc_id = new_arc.arc_id
        self.active_arcs[arc_key] = new_arc
        self._update_arc_type(new_arc)

    def _update_arc_type(self, arc: Arc):
        """Classify arc type from episode patterns."""
        patterns = [ep.dominant_pattern for ep in arc.episodes]
        accum = patterns.count("accumulation")
        distrib = patterns.count("distribution")

        if accum > distrib * 2:
            arc.arc_type = "accumulation_arc"
        elif distrib > accum * 2:
            arc.arc_type = "distribution_arc"
        elif accum > 0 and distrib > 0:
            arc.arc_type = "rotation_arc"
        else:
            arc.arc_type = "consolidation"

        convs = [ep.conviction_score for ep in arc.episodes]
        arc.collective_conviction = sum(convs) / max(1, len(convs))

        arc.narrative = (
            f"{arc.entity} {arc.arc_type.replace('_', ' ')} — "
            f"{arc.episode_count} episodes, "
            f"conviction {arc.collective_conviction:.2f}"
        )

    def _check_cycle(self, arc: Arc):
        """Detect if we've entered a new market cycle."""
        now = arc.end_time

        # Check existing active cycle
        if self.cycles:
            last_cycle = self.cycles[-1]
            if now - last_cycle.start_time <= self.cycle_window:
                last_cycle.arcs.append(arc)
                last_cycle.end_time = now
                last_cycle.arc_count = len(last_cycle.arcs)
                arc.parent_cycle_id = last_cycle.cycle_id
                self._update_cycle_type(last_cycle)
                return

        # New cycle
        cycle = Cycle(
            cycle_id=hashlib.md5(f"cycle{now}".encode()).hexdigest()[:12],
            cycle_type=self._detect_cycle_type(arc),
            arcs=[arc],
            start_time=now,
            end_time=now,
            arc_count=1,
            dominant_chain="solana",
        )
        arc.parent_cycle_id = cycle.cycle_id
        self.cycles.append(cycle)

    def _detect_cycle_type(self, arc: Arc) -> str:
        """Detect market regime from arc type."""
        if arc.arc_type in ("accumulation_arc",):
            return "risk_on"
        elif arc.arc_type in ("distribution_arc",):
            return "risk_off"
        elif arc.arc_type == "rotation_arc":
            return "rotation"
        return "neutral"

    def _update_cycle_type(self, cycle: Cycle):
        """Re-evaluate cycle type from all arcs."""
        types = [a.arc_type for a in cycle.arcs]
        if types.count("accumulation_arc") > types.count("distribution_arc"):
            cycle.cycle_type = "risk_on"
        else:
            cycle.cycle_type = "risk_off"

        cycle.narrative = (
            f"{cycle.cycle_type.replace('_', ' ')} cycle — "
            f"{cycle.arc_count} arcs over "
            f"{round((cycle.end_time - cycle.start_time) / 86400, 1)} days"
        )
        cycle.confidence = min(1.0, cycle.arc_count / 5)

    # ── Query Interface ──────────────────────────────────────

    def get_active_episodes(self) -> list:
        with self._lock:
            return [ep.to_dict() for ep in list(self.active_episodes.values())]

    def get_recent_episodes(self, limit: int = 20) -> list:
        with self._lock:
            return [ep.to_dict() for ep in list(self.completed_episodes)[-limit:]]

    def get_active_arcs(self) -> list:
        with self._lock:
            return [a.to_dict() for a in list(self.active_arcs.values())]

    def get_recent_arcs(self, limit: int = 10) -> list:
        with self._lock:
            return [a.to_dict() for a in list(self.completed_arcs)[-limit:]]

    def get_cycles(self) -> list:
        with self._lock:
            return [c.to_dict() for c in list(self.cycles)]

    def get_narrative_summary(self) -> str:
        """Generate a human-readable narrative summary."""
        with self._lock:
            lines = ["═══ LOOM NARRATIVE SUMMARY ═══", ""]

            active_eps = list(self.active_episodes.values())
            if active_eps:
                lines.append(f"Active Episodes ({len(active_eps)}):")
                for ep in active_eps[-5:]:
                    dur = int(time.time() - ep.start_time)
                    lines.append(f"  ◉ {ep.narrative} (active {dur}s)")

            active_arcs_list = list(self.active_arcs.values())
            if active_arcs_list:
                lines.append(f"\nActive Arcs ({len(active_arcs_list)}):")
                for arc in active_arcs_list[-3:]:
                    lines.append(f"  ◎ {arc.narrative}")

            cycles_list = list(self.cycles)
            if cycles_list:
                lines.append(f"\nMarket Cycles ({len(cycles_list)}):")
                for cycle in cycles_list[-3:]:
                    lines.append(f"  ◈ {cycle.narrative} (confidence {cycle.confidence:.2f})")

            lines.append("\n" + "═" * 32)
            return "\n".join(lines)

    def get_state(self) -> dict:
        return {
            "active_episodes": len(self.active_episodes),
            "completed_episodes": len(self.completed_episodes),
            "active_arcs": len(self.active_arcs),
            "completed_arcs": len(self.completed_arcs),
            "cycles": len(self.cycles),
            "episodes": self.get_recent_episodes(10),
            "arcs": self.get_recent_arcs(5),
            "cycles": self.get_cycles(),
        }


# ═══════════════════════════════════════════════════════════════
# GLOBAL INSTANCE
# ═══════════════════════════════════════════════════════════════

narrative = NarrativeEngine()
