"""
LOOM Whale Engine — Wallet registry, cluster detection, reputation scoring.
Tracks every wallet on Solana, builds dossiers, detects coordinated groups,
and ranks them by predictive value for front-running crowd pumps.

Architecture:
  Wallet Registry → Cluster Engine → Whale Scorer → Intel Brief
       ↓                  ↓                ↓
  Dossier per        Crew detection    Predictive value
  address            + relationship     ranking (not P&L)
"""

import hashlib
import time
import threading
from dataclasses import dataclass, field, asdict
from typing import Optional
from collections import defaultdict, deque


# ═══════════════════════════════════════════════════════════════
# WALLET DOSSIER
# ═══════════════════════════════════════════════════════════════

@dataclass
class TradeRecord:
    token: str
    token_address: str
    entry_time: float
    exit_time: float = 0
    entry_sol: float = 0.0
    exit_sol: float = 0.0
    entry_price: float = 0.0
    exit_price: float = 0.0
    return_pct: float = 0.0
    platform: str = "pump.fun"
    is_closed: bool = False

    @property
    def roi(self) -> float:
        if self.entry_sol > 0 and self.exit_sol > 0:
            return (self.exit_sol - self.entry_sol) / self.entry_sol
        return 0.0


@dataclass
class WalletDossier:
    address: str
    first_seen: float
    last_active: float
    total_trades: int = 0
    winning_trades: int = 0
    total_sol_volume: float = 0.0
    avg_return: float = 0.0
    median_return: float = 0.0
    max_return: float = 0.0
    best_streak: int = 0
    current_streak: int = 0
    active_hours: list = field(default_factory=list)   # hours of day most active
    active_days: list = field(default_factory=list)     # days of week
    favorite_categories: dict = field(default_factory=dict)  # token themes
    typical_entry_sol: float = 0.0
    typical_hold_minutes: float = 0.0
    exit_pattern: str = "unknown"    # staged, dump, gradual, hold
    cluster_id: Optional[str] = None
    tier: int = 0                    # 1=elite, 2=strong, 3=good, 4=tracking
    predictive_value: float = 0.0    # how reliably their moves precede crowd pumps
    twitter_handle: Optional[str] = None
    twitter_confidence: float = 0.0
    labels: list = field(default_factory=list)  # scout, amplifier, leader, exit_signal
    recent_trades: list = field(default_factory=list)  # last 20 TradeRecords
    rhythm_violations: list = field(default_factory=list)  # times they broke pattern
    notes: list = field(default_factory=list)  # agent annotations

    def to_dict(self):
        d = asdict(self)
        d["win_rate"] = round((self.winning_trades / max(1, self.total_trades)) * 100, 1)
        d["address_short"] = self.address[:6] + "..." + self.address[-4:]
        return d

    def to_compact(self):
        return {
            "addr": self.address[:8],
            "tier": self.tier,
            "pv": round(self.predictive_value, 3),
            "wr": round((self.winning_trades / max(1, self.total_trades)) * 100, 1),
            "trades": self.total_trades,
            "avg_r": round(self.avg_return, 1),
            "cluster": self.cluster_id[:8] if self.cluster_id else None,
            "twitter": self.twitter_handle,
            "active": self.last_active,
            "label": self.labels[0] if self.labels else "unknown",
        }


# ═══════════════════════════════════════════════════════════════
# CLUSTER
# ═══════════════════════════════════════════════════════════════

@dataclass
class WhaleCluster:
    cluster_id: str
    members: list = field(default_factory=list)         # wallet addresses
    leader_address: Optional[str] = None
    scout_addresses: list = field(default_factory=list)
    amplifier_addresses: list = field(default_factory=list)
    exit_signal_address: Optional[str] = None            # who sells first
    first_seen: float = 0.0
    total_sol_deployed: float = 0.0
    avg_pump_return: float = 0.0
    coordination_score: float = 0.0       # how synchronized their moves are
    signature_pattern: dict = field(default_factory=dict)  # their typical attack pattern
    recent_activity: list = field(default_factory=list)
    threat_level: str = "unknown"         # high, medium, low

    def to_dict(self):
        return {
            "id": self.cluster_id[:8],
            "members": len(self.members),
            "leader": self.leader_address[:8] if self.leader_address else None,
            "scouts": [s[:8] for s in self.scout_addresses],
            "amplifiers": len(self.amplifier_addresses),
            "coordination": round(self.coordination_score, 3),
            "threat": self.threat_level,
            "sol_deployed": round(self.total_sol_deployed, 2),
            "avg_pump": round(self.avg_pump_return, 1),
        }


# ═══════════════════════════════════════════════════════════════
# WHALE ENGINE
# ═══════════════════════════════════════════════════════════════

class WhaleEngine:
    """Central whale intelligence system."""

    def __init__(self):
        self.wallets: dict = {}           # {address: WalletDossier}
        self.clusters: dict = {}          # {cluster_id: WhaleCluster}
        self.token_index: dict = {}       # {token_address: [wallet_addresses]}
        self._lock = threading.Lock()
        self._cluster_counter = 0
        self.subscribers: list = []

    # ── Wallet Operations ───────────────────────────────────────

    def register_trade(self, address: str, token: str, token_address: str,
                       entry_sol: float, entry_time: float = None,
                       platform: str = "pump.fun") -> WalletDossier:
        """Register a new trade for a wallet. Auto-creates dossier if new."""
        ts = entry_time or time.time()

        with self._lock:
            # Get or create dossier
            if address not in self.wallets:
                self.wallets[address] = WalletDossier(
                    address=address,
                    first_seen=ts,
                    last_active=ts,
                )

            wallet = self.wallets[address]
            wallet.last_active = ts
            wallet.total_trades += 1
            wallet.total_sol_volume += entry_sol

            # Update entry size pattern
            if wallet.typical_entry_sol > 0:
                wallet.typical_entry_sol = wallet.typical_entry_sol * 0.9 + entry_sol * 0.1
            else:
                wallet.typical_entry_sol = entry_sol

            # Track active hours
            from datetime import datetime
            hour = datetime.fromtimestamp(ts).hour
            day = datetime.fromtimestamp(ts).weekday()
            if hour not in wallet.active_hours:
                wallet.active_hours.append(hour)
            if day not in wallet.active_days:
                wallet.active_days.append(day)

            # Create trade record
            trade = TradeRecord(
                token=token,
                token_address=token_address,
                entry_time=ts,
                entry_sol=entry_sol,
                platform=platform,
            )
            wallet.recent_trades.append(trade)
            if len(wallet.recent_trades) > 50:
                wallet.recent_trades.pop(0)

            # Index token
            if token_address not in self.token_index:
                self.token_index[token_address] = []
            if address not in self.token_index[token_address]:
                self.token_index[token_address].append(address)

            # Rhythm check
            if len(wallet.active_hours) > 3:
                common_hours = self._get_common_hours(wallet)
                if hour not in common_hours:
                    wallet.rhythm_violations.append({
                        "time": ts,
                        "reason": f"trading at {hour}:00 UTC (normal: {common_hours})",
                        "size": entry_sol,
                    })

            # Size check (3x median = violation)
            if wallet.typical_entry_sol > 0 and entry_sol > wallet.typical_entry_sol * 2.5:
                wallet.rhythm_violations.append({
                    "time": ts,
                    "reason": f"entry {entry_sol:.1f} SOL, typical {wallet.typical_entry_sol:.1f}",
                    "size": entry_sol,
                })

            self._update_tier(wallet)
            self._notify("trade", wallet=wallet.to_compact(), token=token)
            return wallet

    def register_exit(self, address: str, token_address: str,
                      exit_sol: float, exit_time: float = None):
        """Record trade exit, compute return."""
        ts = exit_time or time.time()

        with self._lock:
            wallet = self.wallets.get(address)
            if not wallet:
                return

            # Find matching trade
            for trade in reversed(wallet.recent_trades):
                if trade.token_address == token_address and not trade.is_closed:
                    trade.exit_time = ts
                    trade.exit_sol = exit_sol
                    trade.return_pct = ((exit_sol - trade.entry_sol) / trade.entry_sol) * 100
                    trade.is_closed = True

                    # Update win/loss
                    if trade.return_pct > 0:
                        wallet.winning_trades += 1
                        wallet.current_streak += 1
                        wallet.best_streak = max(wallet.best_streak, wallet.current_streak)
                    else:
                        wallet.current_streak = 0

                    # Update returns
                    returns = [t.return_pct for t in wallet.recent_trades if t.is_closed]
                    if returns:
                        wallet.avg_return = sum(returns) / len(returns)
                        wallet.max_return = max(wallet.max_return, trade.return_pct)
                        sorted_returns = sorted(returns)
                        mid = len(sorted_returns) // 2
                        wallet.median_return = sorted_returns[mid]

                    # Update hold time
                    hold_min = (trade.exit_time - trade.entry_time) / 60
                    if wallet.typical_hold_minutes > 0:
                        wallet.typical_hold_minutes = wallet.typical_hold_minutes * 0.9 + hold_min * 0.1
                    else:
                        wallet.typical_hold_minutes = hold_min

                    # Update exit pattern
                    wallet.exit_pattern = self._classify_exit_pattern(wallet)

                    self._update_tier(wallet)
                    self._update_predictive_value(wallet)
                    self._notify("exit", wallet=wallet.to_compact(), token=token_address)
                    return wallet

    def _get_common_hours(self, wallet: WalletDossier) -> list:
        """Get hours where wallet is most active."""
        if len(wallet.active_hours) <= 3:
            return wallet.active_hours
        from collections import Counter
        return [h for h, _ in Counter(wallet.active_hours).most_common(3)]

    def _classify_exit_pattern(self, wallet: WalletDossier) -> str:
        """Classify wallet's exit strategy."""
        closed = [t for t in wallet.recent_trades if t.is_closed and t.exit_sol > 0]
        if len(closed) < 3:
            return "unknown"

        # Check for staged exits (multiple sells at different prices)
        # Simplified: look at return consistency
        returns = [t.return_pct for t in closed]
        avg = sum(returns) / len(returns)
        if avg > 100 and all(r > 10 for r in returns):
            return "staged"  # consistent high returns = planned exits
        if any(r > 200 for r in returns):
            return "dump"    # occasional massive wins = dump strategy
        if all(-10 < r < 50 for r in returns):
            return "gradual"
        return "hold"

    # ── Tier Assignment ─────────────────────────────────────────

    def _update_tier(self, wallet: WalletDossier):
        """Assign tier based on performance and reliability."""
        score = 0

        # Win rate
        wr = (wallet.winning_trades / max(1, wallet.total_trades)) * 100
        if wr >= 90: score += 40
        elif wr >= 75: score += 30
        elif wr >= 60: score += 20
        elif wr >= 40: score += 10

        # Average return
        if wallet.avg_return >= 300: score += 30
        elif wallet.avg_return >= 150: score += 20
        elif wallet.avg_return >= 50: score += 10

        # Trade count (consistency)
        if wallet.total_trades >= 50: score += 20
        elif wallet.total_trades >= 20: score += 15
        elif wallet.total_trades >= 10: score += 10
        elif wallet.total_trades >= 3: score += 5

        # Streak
        if wallet.best_streak >= 10: score += 10
        elif wallet.best_streak >= 5: score += 5

        if score >= 80: wallet.tier = 1        # Elite
        elif score >= 55: wallet.tier = 2       # Strong
        elif score >= 30: wallet.tier = 3       # Good
        else: wallet.tier = 4                   # Tracking

    def _update_predictive_value(self, wallet: WalletDossier):
        """Compute predictive value: how reliably this wallet's moves
        precede crowd pumps. PV is NOT P&L — it's 'does the crowd follow?'"""
        closed = [t for t in wallet.recent_trades if t.is_closed]
        if len(closed) < 3:
            wallet.predictive_value = max(0.1, wallet.avg_return / 500)
            return

        # Base: win rate and return consistency
        wr = wallet.winning_trades / max(1, wallet.total_trades)

        # Speed of returns (faster = more predictive of pumps)
        hold_times = [
            (t.exit_time - t.entry_time) / 60
            for t in closed if t.exit_time > t.entry_time
        ]
        avg_hold = sum(hold_times) / max(1, len(hold_times))
        speed_score = max(0, 1 - (avg_hold / 180))  # faster than 3h = better

        # Return magnitude (higher = more likely leader, not follower)
        mag_score = min(1.0, wallet.avg_return / 500)

        # Consistency (low variance = reliable pattern)
        returns = [t.return_pct for t in closed]
        if len(returns) > 1:
            mean_r = sum(returns) / len(returns)
            variance = sum((r - mean_r) ** 2 for r in returns) / len(returns)
            consistency = max(0, 1 - (variance / 50000))
        else:
            consistency = 0.5

        wallet.predictive_value = round(
            wr * 0.35 + speed_score * 0.25 + mag_score * 0.25 + consistency * 0.15, 3
        )

    # ── Cluster Operations ──────────────────────────────────────

    def detect_clusters(self, token_address: str, time_window_sec: int = 300):
        """Detect coordinated wallets buying same token within window."""
        with self._lock:
            buyers = self.token_index.get(token_address, [])
            if len(buyers) < 2:
                return None

            # Find wallets that bought within time window of each other
            clusters = []
            for i, addr_a in enumerate(buyers):
                wa = self.wallets.get(addr_a)
                if not wa:
                    continue
                for addr_b in buyers[i+1:]:
                    wb = self.wallets.get(addr_b)
                    if not wb:
                        continue

                    # Check if both have trades in this token
                    trades_a = [t for t in wa.recent_trades if t.token_address == token_address]
                    trades_b = [t for t in wb.recent_trades if t.token_address == token_address]
                    if not trades_a or not trades_b:
                        continue

                    time_diff = abs(trades_a[0].entry_time - trades_b[0].entry_time)
                    if time_diff < time_window_sec:
                        clusters.append((addr_a, addr_b, time_diff))

            if not clusters:
                return None

            # Find connected components in the graph
            cluster_members = self._find_connected(clusters)
            if len(cluster_members) < 2:
                return None

            # Create or update cluster
            member_ids = sorted(cluster_members)
            cluster_hash = hashlib.md5("|".join(member_ids).encode()).hexdigest()[:12]

            if cluster_hash not in self.clusters:
                self._cluster_counter += 1
                self.clusters[cluster_hash] = WhaleCluster(
                    cluster_id=cluster_hash,
                    first_seen=time.time(),
                )

            cluster = self.clusters[cluster_hash]
            cluster.members = list(set(cluster.members + member_ids))
            cluster.coordination_score = min(1.0, cluster.coordination_score + 0.1)

            # Identify roles
            self._assign_cluster_roles(cluster)

            # Assign cluster to members
            for addr in member_ids:
                wallet = self.wallets.get(addr)
                if wallet:
                    wallet.cluster_id = cluster_hash

            return cluster

    def _find_connected(self, pairs: list) -> set:
        """Find connected components from pair list."""
        graph = defaultdict(set)
        for a, b, _ in pairs:
            graph[a].add(b)
            graph[b].add(a)

        visited = set()
        components = []

        def dfs(node, comp):
            visited.add(node)
            comp.add(node)
            for neighbor in graph[node]:
                if neighbor not in visited:
                    dfs(neighbor, comp)

        for node in graph:
            if node not in visited:
                comp = set()
                dfs(node, comp)
                components.append(comp)

        # Return largest component
        return max(components, key=len) if components else set()

    def _assign_cluster_roles(self, cluster: WhaleCluster):
        """Identify leader, scouts, amplifiers based on behavior."""
        members = [self.wallets.get(a) for a in cluster.members]
        members = [m for m in members if m is not None]
        if not members:
            return

        # Leader: highest predictive value
        sorted_by_pv = sorted(members, key=lambda m: m.predictive_value, reverse=True)
        cluster.leader_address = sorted_by_pv[0].address
        sorted_by_pv[0].labels.append("leader")

        # Scouts: enter first with small positions
        for m in members:
            if m.typical_entry_sol < 5 and m.total_trades > 10:
                cluster.scout_addresses.append(m.address)
                if "scout" not in m.labels:
                    m.labels.append("scout")

        # Exit signal: who sells first consistently
        # Amplifiers: large position entries
        for m in members:
            if m.typical_entry_sol > 10:
                cluster.amplifier_addresses.append(m.address)
                if "amplifier" not in m.labels:
                    m.labels.append("amplifier")

        cluster.threat_level = "high" if cluster.coordination_score > 0.7 else "medium"

    # ── Query Interface ─────────────────────────────────────────

    def get_leaderboard(self, limit: int = 20) -> list:
        """Top wallets by predictive value."""
        with self._lock:
            ranked = sorted(
                self.wallets.values(),
                key=lambda w: w.predictive_value,
                reverse=True,
            )
            return [w.to_compact() for w in ranked[:limit] if w.total_trades >= 3]

    def get_whale(self, address: str) -> Optional[dict]:
        """Get full dossier for a wallet."""
        wallet = self.wallets.get(address)
        return wallet.to_dict() if wallet else None

    def get_cluster(self, cluster_id: str) -> Optional[dict]:
        """Get cluster by ID."""
        cluster = self.clusters.get(cluster_id)
        return cluster.to_dict() if cluster else None

    def get_active_whales(self, max_age_sec: int = 3600) -> list:
        """Whales active within the last N seconds."""
        now = time.time()
        with self._lock:
            active = []
            for w in self.wallets.values():
                if now - w.last_active < max_age_sec and w.tier <= 2:
                    active.append(w.to_compact())
            return sorted(active, key=lambda w: w["pv"], reverse=True)

    def get_token_whales(self, token_address: str) -> list:
        """All wallets that have traded a specific token."""
        addresses = self.token_index.get(token_address, [])
        return [self.wallets[a].to_compact() for a in addresses if a in self.wallets]

    def get_rhythm_violators(self) -> list:
        """Whales operating outside their normal patterns."""
        with self._lock:
            violators = []
            for w in self.wallets.values():
                if w.rhythm_violations and w.tier <= 2:
                    latest = w.rhythm_violations[-1]
                    if time.time() - latest["time"] < 3600:  # Within last hour
                        violators.append({
                            "address": w.address[:8],
                            "tier": w.tier,
                            "violation": latest["reason"],
                            "time": latest["time"],
                            "size": latest["size"],
                        })
            return violators

    # ── Pub/Sub ─────────────────────────────────────────────────

    def subscribe(self, callback):
        self.subscribers.append(callback)

    def _notify(self, event_type: str, **data):
        for sub in self.subscribers:
            try:
                sub(event_type, data)
            except Exception:
                pass


# Global instance
whales = WhaleEngine()
