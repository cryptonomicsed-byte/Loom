#!/usr/bin/env python3
"""
TRENCHES — Pump.fun Alpha Layer for LOOM.

Detects new pump.fun launches within seconds, tracks first buyers,
scores alpha from wallet quality + velocity + buyer concentration,
and fires front-running alerts before the crowd arrives.

Architecture:
  Solana RPC → New token detection → First buyer analysis
     ↓                    ↓                    ↓
  Wallet scoring    Bonding curve    Social velocity
     ↓                    ↓                    ↓
         ALPHA SCORE ENGINE → Front-run alert

Sources:
  - Solana RPC (getSignaturesForAddress on pump.fun program)
  - Birdeye (trending tokens, holder data)
  - Jupiter (price quotes)
  - Kolscan-style wallet leaderboard (manual seed + Dune queries)
"""

import json
import time
import threading
import os
import sys
import hashlib
from dataclasses import dataclass, field
from typing import Optional, List, Dict, Set
from collections import defaultdict, deque

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# ═══════════════════════════════════════════════════════════════
# PUMP.FUN CONSTANTS
# ═══════════════════════════════════════════════════════════════

PUMP_PROGRAM = "6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P"
PUMP_AMM = "pAMMBay6oceH9fJKBRHGP5D4bD4sWpmSwMn52FMfXEA"
RAYDIUM_MIGRATION = "39azUYFWP8FRDgZJxK9BfBMW6ErDRf4sWx4iWqKEYT9N"
BONDING_CURVE_TOTAL_SOL = 85.0  # Pump.fun bonding curve fills at ~85 SOL

SOLANA_RPC = "https://api.mainnet-beta.solana.com"

# Profitability thresholds
MIN_ALPHA_SCORE = 0.5   # Minimum to alert
HIGH_ALPHA_SCORE = 0.75  # Front-run alert
ELITE_ALPHA_SCORE = 0.9  # Instant alert + auto-snipe candidate


# ═══════════════════════════════════════════════════════════════
# FIRST BUYER TRACKER
# ═══════════════════════════════════════════════════════════════

@dataclass
class LaunchTracker:
    """Tracks a single pump.fun token from creation through bonding."""
    mint: str
    symbol: str
    created_at: float
    creator_wallet: str = ""
    first_buyers: List[dict] = field(default_factory=list)  # [(wallet, amount, timestamp), ...]
    total_volume_sol: float = 0.0
    buyer_count: int = 0
    unique_buyers: Set[str] = field(default_factory=set)
    known_whale_buyers: Set[str] = field(default_factory=set)
    bonding_progress_pct: float = 0.0
    raydium_migrated: bool = False
    social_mentions: int = 0
    alpha_score: float = 0.0
    score_components: dict = field(default_factory=dict)
    status: str = "tracking"  # tracking, alerted, bonding, migrated, dead
    last_updated: float = field(default_factory=time.time)


class PumpFunMonitor:
    """
    Real-time pump.fun launch monitor.
    Polls Solana for new program interactions, tracks first buyers,
    computes alpha scores.
    """

    def __init__(self, whale_engine=None):
        self.active_launches: Dict[str, LaunchTracker] = {}
        self.completed_launches: List[LaunchTracker] = []
        self.known_whales: Set[str] = set()
        self.alert_queue: deque = deque(maxlen=100)
        self.whale_engine = whale_engine
        self._lock = threading.Lock()
        self._last_scan = 0
        self._scan_interval = 3  # Scan every 3s for new launches

    def load_known_whales(self, addresses: list):
        """Load known profitable wallet addresses (from Kolscan, Dune, manual)."""
        self.known_whales.update(addresses)
        print(f"  [trenches] loaded {len(addresses)} known whale wallets")

    def scan_new_launches(self) -> list:
        """Scan pump.fun program for new token creations."""
        try:
            import urllib.request
            body = json.dumps({
                "jsonrpc": "2.0", "id": 1,
                "method": "getSignaturesForAddress",
                "params": [PUMP_PROGRAM, {"limit": 15}],
            }).encode()
            req = urllib.request.Request(SOLANA_RPC, data=body, headers={
                "Content-Type": "application/json", "User-Agent": "LOOM-Trenches/1.0",
            })
            with urllib.request.urlopen(req, timeout=8) as resp:
                result = json.loads(resp.read())
                sigs = result.get("result", [])
        except Exception:
            return []

        new_tokens = []
        for s in sigs:
            sig = s.get("signature", "")
            bt = s.get("blockTime", time.time())
            if sig not in self.active_launches and not any(
                l.mint == sig for l in self.completed_launches[-500:]
            ):
                # New potential launch — parse transaction for token mint
                mint = self._extract_mint_from_tx(sig)
                if mint and mint not in self.active_launches:
                    tracker = LaunchTracker(
                        mint=mint,
                        symbol=mint[:8],
                        created_at=bt,
                    )
                    self.active_launches[mint] = tracker
                    new_tokens.append(tracker)

        return new_tokens

    def _extract_mint_from_tx(self, signature: str) -> Optional[str]:
        """Extract token mint from a transaction signature."""
        try:
            import urllib.request
            body = json.dumps({
                "jsonrpc": "2.0", "id": 1,
                "method": "getTransaction",
                "params": [signature, {
                    "encoding": "jsonParsed",
                    "maxSupportedTransactionVersion": 0,
                }],
            }).encode()
            req = urllib.request.Request(SOLANA_RPC, data=body, headers={
                "Content-Type": "application/json", "User-Agent": "LOOM-Trenches/1.0",
            })
            with urllib.request.urlopen(req, timeout=8) as resp:
                tx = json.loads(resp.read()).get("result", {})
                if not tx:
                    return None

                # Look for token mint in post-token-balances
                meta = tx.get("meta", {})
                post_balances = meta.get("postTokenBalances", [])
                if post_balances:
                    return post_balances[0].get("mint")

                # Check instructions for initializeMint
                msg = tx.get("transaction", {}).get("message", {})
                instructions = msg.get("instructions", [])
                for ix in instructions:
                    if ix.get("programId") == PUMP_PROGRAM:
                        accounts = ix.get("accounts", [])
                        if len(accounts) > 0:
                            return accounts[0]
        except Exception:
            pass
        return None

    def track_first_buyers(self, tracker: LaunchTracker, max_age_sec: int = 60):
        """
        Track who bought within the first N seconds of launch.
        Checks known whale list and updates alpha score.
        """
        if time.time() - tracker.created_at > max_age_sec * 2:
            return  # Don't track old launches

        try:
            import urllib.request
            body = json.dumps({
                "jsonrpc": "2.0", "id": 1,
                "method": "getSignaturesForAddress",
                "params": [tracker.mint, {"limit": 20}],
            }).encode()
            req = urllib.request.Request(SOLANA_RPC, data=body, headers={
                "Content-Type": "application/json", "User-Agent": "LOOM-Trenches/1.0",
            })
            with urllib.request.urlopen(req, timeout=8) as resp:
                sigs = json.loads(resp.read()).get("result", [])
        except Exception:
            return

        for s in sigs:
            bt = s.get("blockTime", 0)
            if bt - tracker.created_at > max_age_sec:
                continue

            try:
                # Get transaction details for wallet extraction
                body2 = json.dumps({
                    "jsonrpc": "2.0", "id": 1,
                    "method": "getTransaction",
                    "params": [s["signature"], {
                        "encoding": "jsonParsed",
                        "maxSupportedTransactionVersion": 0,
                    }],
                }).encode()
                req2 = urllib.request.Request(SOLANA_RPC, data=body2, headers={
                    "Content-Type": "application/json", "User-Agent": "LOOM-Trenches/1.0",
                })
                with urllib.request.urlopen(req2, timeout=8) as resp2:
                    tx = json.loads(resp2.read()).get("result", {})
            except Exception:
                continue

            if not tx:
                continue

            # Extract wallet from account keys
            account_keys = tx.get("transaction", {}).get("message", {}).get("accountKeys", [])
            for ak in account_keys:
                if isinstance(ak, dict):
                    wallet = ak.get("pubkey", "")
                else:
                    wallet = str(ak)

                if wallet and wallet not in tracker.unique_buyers:
                    tracker.unique_buyers.add(wallet)
                    tracker.buyer_count += 1

                    if wallet in self.known_whales:
                        tracker.known_whale_buyers.add(wallet)

                    if self.whale_engine:
                        # Check whale engine tier
                        w = self.whale_engine.wallets.get(wallet)
                        if w and w.tier <= 2:
                            tracker.known_whale_buyers.add(wallet)

        tracker.last_updated = time.time()
        self._score_launch(tracker)

    def _score_launch(self, tracker: LaunchTracker):
        """Compute alpha score for a launch based on multiple signals."""

        now = time.time()
        age_seconds = now - tracker.created_at

        score = 0.0
        components = {}

        # 1. Whale quality (40% weight)
        if tracker.buyer_count > 0:
            whale_ratio = len(tracker.known_whale_buyers) / tracker.buyer_count
            if whale_ratio > 0.3:
                whale_score = 0.4
            elif whale_ratio > 0.1:
                whale_score = 0.25
            elif len(tracker.known_whale_buyers) > 0:
                whale_score = 0.15
            else:
                whale_score = 0.0
        else:
            whale_score = 0.0
        components["whale_quality"] = round(whale_score, 3)
        score += whale_score

        # 2. Velocity (25% weight) — how fast are buyers entering?
        if age_seconds > 0 and tracker.buyer_count > 0:
            buyers_per_sec = tracker.buyer_count / age_seconds
            if buyers_per_sec > 0.3:
                velocity_score = 0.25  # 1 buyer every 3s = very hot
            elif buyers_per_sec > 0.1:
                velocity_score = 0.18
            elif buyers_per_sec > 0.03:
                velocity_score = 0.10
            else:
                velocity_score = 0.03
        else:
            velocity_score = 0.0
        components["velocity"] = round(velocity_score, 3)
        score += velocity_score

        # 3. Buyer concentration (20% weight) — concentrated = pumps better
        if tracker.buyer_count >= 3:
            # More unique buyers = broader interest
            if tracker.buyer_count >= 10:
                concentration_score = 0.20
            elif tracker.buyer_count >= 5:
                concentration_score = 0.12
            else:
                concentration_score = 0.06
        else:
            concentration_score = 0.0
        components["concentration"] = round(concentration_score, 3)
        score += concentration_score

        # 4. Freshness bonus (10% weight) — newer launches get higher scores
        if age_seconds < 30:
            freshness_score = 0.10
        elif age_seconds < 120:
            freshness_score = 0.06
        elif age_seconds < 300:
            freshness_score = 0.03
        else:
            freshness_score = 0.0
        components["freshness"] = round(freshness_score, 3)
        score += freshness_score

        # 5. Social mentions (5% weight)
        if tracker.social_mentions > 0:
            social_score = min(0.05, tracker.social_mentions * 0.01)
        else:
            social_score = 0.0
        components["social"] = round(social_score, 3)
        score += social_score

        tracker.alpha_score = round(min(1.0, score), 3)
        tracker.score_components = components

        # Fire alerts at thresholds
        if tracker.alpha_score >= ELITE_ALPHA_SCORE and tracker.status == "tracking":
            tracker.status = "alerted"
            alert = self._build_alert(tracker, "ELITE")
            self.alert_queue.append(alert)
            print(f"  🔴 ELITE ALPHA: {tracker.symbol} score={tracker.alpha_score:.2f}")
        elif tracker.alpha_score >= HIGH_ALPHA_SCORE and tracker.status == "tracking":
            tracker.status = "alerted"
            alert = self._build_alert(tracker, "HIGH")
            self.alert_queue.append(alert)
            print(f"  🟠 HIGH ALPHA: {tracker.symbol} score={tracker.alpha_score:.2f}")
        elif tracker.alpha_score >= MIN_ALPHA_SCORE and tracker.status == "tracking":
            tracker.status = "alerted"
            alert = self._build_alert(tracker, "MEDIUM")
            self.alert_queue.append(alert)

    def _build_alert(self, tracker: LaunchTracker, level: str) -> dict:
        """Build structured alert for downstream consumers."""
        return {
            "type": "pump_fun_alpha",
            "level": level,
            "mint": tracker.mint,
            "symbol": tracker.symbol,
            "alpha_score": tracker.alpha_score,
            "age_seconds": int(time.time() - tracker.created_at),
            "buyer_count": tracker.buyer_count,
            "known_whales": len(tracker.known_whale_buyers),
            "bonding_pct": tracker.bonding_progress_pct,
            "timestamp": time.time(),
            "action": (
                "SNIPE_NOW" if level == "ELITE"
                else "ENTER_FAST" if level == "HIGH"
                else "WATCH"
            ),
        }

    def get_active_alerts(self, max_age: int = 300) -> list:
        """Get recent alerts still within actionable window."""
        now = time.time()
        return [a for a in self.alert_queue if now - a["timestamp"] < max_age]

    def get_top_launches(self, limit: int = 20) -> list:
        """Get top-scored active launches for dashboard."""
        with self._lock:
            launches = list(self.active_launches.values())
            launches.sort(key=lambda l: l.alpha_score, reverse=True)
            return [
                {
                    "mint": l.mint[:12] + "...",
                    "symbol": l.symbol,
                    "age_seconds": int(time.time() - l.created_at),
                    "alpha_score": l.alpha_score,
                    "buyer_count": l.buyer_count,
                    "known_whales": len(l.known_whale_buyers),
                    "bonding_pct": l.bonding_progress_pct,
                    "score_components": l.score_components,
                    "status": l.status,
                }
                for l in launches[:limit]
            ]

    def run_loop(self):
        """Main monitoring loop."""
        print("[trenches] Pump.fun monitor started — scanning every 3s")

        # Seed with some well-known pump.fun whale addresses
        self.load_known_whales([
            # Top pump.fun traders (public leaderboard addresses — anonymized)
            "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v",  # USDC mint as placeholder
        ])

        while True:
            try:
                new = self.scan_new_launches()

                # Track first buyers for recent launches
                for tracker in list(self.active_launches.values()):
                    if tracker.status == "tracking":
                        self.track_first_buyers(tracker)

                # Clean old launches
                now = time.time()
                stale = [
                    m for m, t in self.active_launches.items()
                    if now - t.created_at > 3600 and t.status == "tracking"
                ]
                for m in stale:
                    self.active_launches[m].status = "dead"
                    self.completed_launches.append(self.active_launches.pop(m))

            except Exception as e:
                print(f"  [trenches] error: {e}")

            time.sleep(self._scan_interval)


# ═══════════════════════════════════════════════════════════════
# INFLUENCER TRACKER
# ═══════════════════════════════════════════════════════════════

class InfluencerTracker:
    """Tracks Twitter/X influencer mentions of pump.fun tokens."""

    # Known trenches-focused accounts
    TRENCHES_ACCOUNTS = [
        "blknoiz06",     # Ansem — legendary early calls
        "A1lon9",        # Pump.fun co-founder
        "artschOOlreject",
        "POE",
        "cexoffender_",
        "larpalt",
        "brianjungx",
        "cryptolyxe",
        "notthreadguy",
    ]

    def __init__(self):
        self.mentions: deque = deque(maxlen=500)
        self.account_stats: Dict[str, dict] = defaultdict(
            lambda: {"calls": 0, "wins": 0, "tokens_mentioned": set()}
        )

    def ingest_mention(self, handle: str, text: str, tokens: list, timestamp: float):
        """Record an influencer mentioning a token."""
        import re
        # Extract contract addresses from text (Solana base58 addresses)
        addresses = re.findall(r'[1-9A-HJ-NP-Za-km-z]{32,44}', text)

        mention = {
            "handle": handle,
            "text": text[:280],
            "tokens": tokens,
            "addresses": addresses,
            "timestamp": timestamp,
        }
        self.mentions.append(mention)

        stats = self.account_stats[handle]
        stats["calls"] += 1
        for t in tokens:
            stats["tokens_mentioned"].add(t)

    def get_hot_mentions(self, max_age: int = 600) -> list:
        """Get recent mentions still within signal window."""
        now = time.time()
        return [m for m in self.mentions if now - m["timestamp"] < max_age]

    def get_influencer_leaderboard(self) -> list:
        """Rank influencers by call count (eventually by win rate)."""
        leaderboard = []
        for handle, stats in self.account_stats.items():
            leaderboard.append({
                "handle": handle,
                "calls": stats["calls"],
                "tokens_mentioned": len(stats["tokens_mentioned"]),
            })
        leaderboard.sort(key=lambda x: x["calls"], reverse=True)
        return leaderboard[:15]


# ═══════════════════════════════════════════════════════════════
# GLOBAL INSTANCES
# ═══════════════════════════════════════════════════════════════

trenches = PumpFunMonitor()
influencers = InfluencerTracker()
