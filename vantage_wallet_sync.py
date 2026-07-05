#!/usr/bin/env python3
"""
Vantage Wallet Sync — Pulls ALL tracked wallets from Vantage into LOOM.
Scans, scores, and enriches them with LOOM's whale intelligence.
"""

import json
import time
import urllib.request
import os
import threading

VANTAGE_URL = "http://2.25.70.156:8001"
KEY_FILE = "/data/data/com.termux/files/home/loom/.vantage_key"


class VantageWalletSync:
    """Bidirectional wallet sync between Vantage and LOOM."""

    def __init__(self):
        self.key = self._load_key()
        self._synced_count = 0
        self._last_sync = 0

    def _load_key(self):
        try:
            with open(KEY_FILE) as f:
                return f.read().strip()
        except Exception:
            return ""

    def _headers(self):
        return {
            "X-Agent-Key": self.key,
            "User-Agent": "LOOM-WalletSync/2.0",
        }

    def pull_all_wallets(self) -> list:
        """Pull every wallet from Vantage watchlist."""
        try:
            req = urllib.request.Request(
                f"{VANTAGE_URL}/api/intel/watchlist",
                headers=self._headers(),
            )
            with urllib.request.urlopen(req, timeout=10) as resp:
                data = json.loads(resp.read())
                return data.get("wallets", data if isinstance(data, list) else [])
        except Exception as e:
            print(f"  [wallet-sync] pull failed: {e}")
            return []

    def sync_to_loom(self, whales_engine, twitter_mapper=None, solana_sources=None) -> int:
        """
        Pull all Vantage wallets → register in LOOM whale engine.
        Scores them, detects clusters, enriches with on-chain data.

        Returns number of new wallets synced.
        """
        vantage_wallets = self.pull_all_wallets()
        if not vantage_wallets:
            return 0

        new_count = 0
        enriched = 0

        for vw in vantage_wallets:
            addr = vw.get("address", "")
            if not addr:
                continue

            # Register in whale engine if not already tracked
            if addr not in whales_engine.wallets:
                # Seed with trade count=1 so it shows on leaderboard
                whales_engine.register_trade(
                    addr,
                    token=vw.get("label", "vantage")[:20] or "vantage_tracked",
                    token_address=addr,
                    entry_sol=0.001,  # Minimal value to appear on leaderboard
                    entry_time=time.time(),
                )
                wallet = whales_engine.wallets.get(addr)
                if wallet:
                    wallet.labels.append(vw.get("address_type", "wallet"))
                    # Tag exchange wallets specially
                    if vw.get("address_type") == "exchange":
                        wallet.labels.append("exchange")
                    if any(x in (vw.get("label", "") or "").lower()
                           for x in ["binance", "coinbase", "kraken", "okx"]):
                        wallet.labels.append("major_exchange")
                new_count += 1

            # Enrich with on-chain data if Solana sources available
            if solana_sources and solana_sources.connected:
                try:
                    holders = solana_sources.get_token_holders(addr)
                    if holders:
                        enriched += 1
                except Exception:
                    pass

        # Detect new clusters (use dummy token — real clustering from Julia)
        try:
            if new_count > 3:
                whales_engine.detect_clusters("vantage_batch", 300)
        except Exception:
            pass

        self._synced_count = len(vantage_wallets)
        self._last_sync = time.time()

        if new_count:
            print(f"  [wallet-sync] pulled {self._synced_count} wallets from Vantage "
                  f"({new_count} new, {enriched} on-chain enriched)")

        return new_count

    def push_back_to_vantage(self, whales_engine, limit=20) -> int:
        """
        Push LOOM-scored wallets back to Vantage watchlist.
        Adds PV scores, tier, and cluster info as notes.
        """
        if not whales_engine:
            return 0

        lb = whales_engine.get_leaderboard(limit)
        pushed = 0

        for w in lb:
            addr = w.get("addr", "")
            tier = w.get("tier", 4)
            pv = w.get("pv", 0)
            wr = w.get("wr", 0)
            cluster = w.get("cluster", "")
            label = w.get("label", "")

            notes = (f"LOOM Tier {tier} | PV={pv:.3f} | WR={wr:.0f}% | "
                    f"Cluster: {cluster or 'solo'} | Role: {label}")

            try:
                body = json.dumps({
                    "chain": "solana",
                    "address": addr,
                    "label": f"LOOM-T{tier}-{label}-{addr[:6]}",
                    "address_type": "wallet",
                    "notes": notes,
                }).encode()

                req = urllib.request.Request(
                    f"{VANTAGE_URL}/api/intel/watchlist",
                    data=body,
                    headers={**self._headers(), "Content-Type": "application/json"},
                    method="POST",
                )
                with urllib.request.urlopen(req, timeout=8) as resp:
                    pushed += 1
            except Exception:
                pass  # Already in watchlist or rate limited

        if pushed:
            print(f"  [wallet-sync] pushed {pushed} scored wallets back to Vantage")
        return pushed

    def sync_loop(self, whales_engine, twitter_mapper=None,
                  solana_sources=None, interval=60):
        """Continuous bidirectional sync."""
        print("  [wallet-sync] starting bidirectional Vantage ↔ LOOM sync...")

        while True:
            try:
                # Pull Vantage wallets → LOOM
                self.sync_to_loom(whales_engine, twitter_mapper, solana_sources)
                # Push LOOM scores → Vantage  
                self.push_back_to_vantage(whales_engine)
            except Exception as e:
                print(f"  [wallet-sync] error: {e}")
            time.sleep(interval)


# Global instance
wallet_sync = VantageWalletSync()
