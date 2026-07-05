#!/usr/bin/env python3
"""
LOOM → Vantage Watchlist Bridge.

Pushes LOOM's whale wallet registry into Vantage's watchlist
so other agents can discover and follow the same wallets.

Vantage endpoints:
  POST   /api/intel/watchlist       — Add wallet to tracked list
  GET    /api/intel/watchlist       — List tracked wallets
  PATCH  /api/intel/watchlist/{id}  — Update wallet fields

Vantage classifies wallets as:
  - wallet         (standard)
  - exchange       (CEX/DEX)
  - contract       (smart contract)
  - smart_wallet   (Gnosis safe, etc.)
"""

import json
import time
import urllib.request
import threading
import os

VANTAGE_URL = "http://2.25.70.156:8001"
AGENT_KEY = os.environ.get("VANTAGE_AGENT_KEY", "loom-whale-tracker")


class VantageWatchlist:
    """Syncs LOOM whale wallets to Vantage's watchlist."""

    def __init__(self, base_url=VANTAGE_URL, agent_key=AGENT_KEY):
        self.base = base_url.rstrip("/")
        self.key = agent_key
        self._synced = set()  # Already pushed wallet addresses
        self._lock = threading.Lock()

    def _headers(self):
        return {
            "Content-Type": "application/json",
            "X-Agent-Key": self.key,
            "User-Agent": "LOOM-Watchlist/1.0",
        }

    def add_wallet(self, address, label="", chain="solana",
                   address_type="wallet", notes="") -> bool:
        """Push a single wallet to Vantage watchlist."""
        with self._lock:
            if address in self._synced:
                return True  # Already tracked

            try:
                body = json.dumps({
                    "chain": chain,
                    "address": address,
                    "label": label or f"LOOM-{address[:8]}",
                    "address_type": address_type,
                    "notes": notes or f"Tracked by LOOM whale engine",
                }).encode()

                req = urllib.request.Request(
                    f"{self.base}/api/intel/watchlist",
                    data=body, headers=self._headers(), method="POST",
                )
                with urllib.request.urlopen(req, timeout=8) as resp:
                    result = json.loads(resp.read())
                    self._synced.add(address)
                    return True
            except Exception as e:
                print(f"  [watchlist] failed to add {address[:12]}: {e}")
                return False

    def push_leaderboard(self, whales_engine, limit=20) -> int:
        """Push LOOM's top whales to Vantage watchlist."""
        if not whales_engine:
            return 0

        lb = whales_engine.get_leaderboard(limit)
        pushed = 0

        for w in lb:
            addr = w.get("addr", "")
            tier = w.get("tier", 4)
            pv = w.get("pv", 0)
            wr = w.get("wr", 0)
            label = w.get("label", "")

            # Map LOOM tier to Vantage address_type
            if label == "cluster":
                addr_type = "smart_wallet"
            else:
                addr_type = "wallet"

            notes = f"LOOM Tier {tier} | PV={pv:.3f} | WR={wr:.0f}% | {label}"

            if self.add_wallet(addr, f"LOOM-{label}-{addr[:6]}",
                              "solana", addr_type, notes):
                pushed += 1

        if pushed:
            print(f"  [watchlist] pushed {pushed} whales to Vantage")
        return pushed

    def list_tracked(self) -> list:
        """List all wallets currently in Vantage watchlist."""
        try:
            req = urllib.request.Request(
                f"{self.base}/api/intel/watchlist",
                headers=self._headers(),
            )
            with urllib.request.urlopen(req, timeout=8) as resp:
                return json.loads(resp.read())
        except Exception:
            return []

    def sync_loop(self, whales_engine, interval=120):
        """Continuous sync: push new whales every N seconds."""
        while True:
            try:
                self.push_leaderboard(whales_engine)
            except Exception as e:
                print(f"  [watchlist] sync error: {e}")
            time.sleep(interval)


# Global instance
watchlist = VantageWatchlist()
