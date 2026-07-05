#!/usr/bin/env python3
"""
Solana Wallet Scanner — Enriches Vantage wallets with real on-chain data.
Pulls transaction history, calculates PV scores, feeds whale engine.
"""

import json
import time
import urllib.request
import threading
from collections import defaultdict

SOLANA_RPC = "https://api.mainnet-beta.solana.com"
BIRDEYE_API = "https://public-api.birdeye.com"


class SolanaWalletScanner:
    """Scans Solana for real wallet activity to enrich LOOM's engine."""

    def __init__(self):
        self.connected = self._check_rpc()
        self._scan_batch = []

    def _check_rpc(self):
        try:
            r = self._rpc("getHealth")
            return r.get("result") == "ok"
        except Exception:
            return False

    def _rpc(self, method, params=None):
        body = json.dumps({
            "jsonrpc": "2.0", "id": 1,
            "method": method,
            "params": params or [],
        }).encode()
        req = urllib.request.Request(SOLANA_RPC, data=body, headers={
            "Content-Type": "application/json", "User-Agent": "LOOM-Scanner/1.0",
        })
        with urllib.request.urlopen(req, timeout=10) as resp:
            return json.loads(resp.read())

    def get_balance(self, address: str) -> float:
        """Get SOL balance for a wallet."""
        try:
            r = self._rpc("getBalance", [address])
            return r.get("result", {}).get("value", 0) / 1e9
        except Exception:
            return 0

    def get_recent_transactions(self, address: str, limit: int = 20) -> list:
        """Get recent transaction signatures for a wallet."""
        try:
            r = self._rpc("getSignaturesForAddress", [
                address,
                {"limit": limit},
            ])
            return r.get("result", [])
        except Exception:
            return []

    def get_token_accounts(self, address: str) -> list:
        """Get token accounts held by a wallet."""
        try:
            body = json.dumps({
                "jsonrpc": "2.0", "id": 1,
                "method": "getTokenAccountsByOwner",
                "params": [
                    address,
                    {"programId": "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA"},
                    {"encoding": "jsonParsed"},
                ],
            }).encode()
            req = urllib.request.Request(SOLANA_RPC, data=body, headers={
                "Content-Type": "application/json", "User-Agent": "LOOM-Scanner/1.0",
            })
            with urllib.request.urlopen(req, timeout=10) as resp:
                r = json.loads(resp.read())
                accounts = r.get("result", {}).get("value", [])
                tokens = []
                for acc in accounts:
                    info = acc.get("account", {}).get("data", {}).get("parsed", {}).get("info", {})
                    amount = info.get("tokenAmount", {})
                    if float(amount.get("uiAmount", 0)) > 0:
                        tokens.append({
                            "mint": info.get("mint", ""),
                            "amount": float(amount.get("uiAmount", 0)),
                            "decimals": amount.get("decimals", 0),
                        })
                return tokens
        except Exception:
            return []

    def scan_wallet(self, address: str) -> dict:
        """
        Full scan of a single wallet.
        Returns enriched profile for whale engine.
        """
        result = {
            "address": address,
            "balance_sol": 0,
            "tx_count": 0,
            "tokens_held": 0,
            "top_tokens": [],
            "active": False,
            "last_active": 0,
        }

        # Balance
        result["balance_sol"] = round(self.get_balance(address), 4)

        # Recent transactions
        txs = self.get_recent_transactions(address, 20)
        result["tx_count"] = len(txs)
        if txs:
            result["active"] = True
            result["last_active"] = txs[0].get("blockTime", 0)

        # Token holdings
        tokens = self.get_token_accounts(address)
        result["tokens_held"] = len(tokens)
        result["top_tokens"] = [t["mint"][:12] for t in tokens[:5]]

        return result

    def enrich_whale(self, address: str, wallet_obj) -> bool:
        """Apply real on-chain data to a whale engine wallet object."""
        if not self.connected:
            return False

        try:
            profile = self.scan_wallet(address)

            # Update wallet with real data
            if profile["active"]:
                wallet_obj.total_trades = max(wallet_obj.total_trades, profile["tx_count"])

                # If wallet has significant balance, boost tier
                if profile["balance_sol"] > 100:
                    wallet_obj.labels.append("high_balance")
                    if "exchange" not in wallet_obj.labels:
                        wallet_obj._update_tier()

                # If wallet holds many tokens, it's a trader
                if profile["tokens_held"] > 10:
                    wallet_obj.labels.append("active_trader")

            return True
        except Exception:
            return False

    def batch_scan(self, whales_engine, batch_size: int = 10) -> int:
        """
        Scan a batch of wallets from the whale engine.
        Returns number enriched.
        """
        if not self.connected:
            return 0

        # Get wallets that need enrichment (no real trade data yet)
        candidates = []
        for addr, w in whales_engine.wallets.items():
            if w.total_trades <= 1:  # Only placeholder trades
                candidates.append(addr)

        if not candidates:
            return 0

        # Take a batch
        batch = candidates[:batch_size]
        enriched = 0

        for addr in batch:
            w = whales_engine.wallets.get(addr)
            if w and self.enrich_whale(addr, w):
                enriched += 1

        if enriched:
            print(f"  [solscan] enriched {enriched}/{len(batch)} wallets (balance, tx count, tokens)")

        return enriched

    def scan_loop(self, whales_engine, interval: int = 120):
        """Continuous wallet enrichment loop."""
        if not self.connected:
            print("  [solscan] Solana RPC unreachable — skipping")
            return

        print(f"  [solscan] scanning Solana wallets every {interval}s...")

        while True:
            try:
                self.batch_scan(whales_engine, batch_size=10)
            except Exception as e:
                print(f"  [solscan] error: {e}")
            time.sleep(interval)


# Global instance
solscan = SolanaWalletScanner()
