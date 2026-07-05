#!/usr/bin/env python3
"""
Vantage → LOOM Data Bridge.
Pulls Vantage's intelligence into LOOM's fabric for enriched analysis.
"""

import json
import time
import urllib.request
import threading

VANTAGE_URL = "http://2.25.70.156:8001"


class VantagePuller:
    """Pulls Vantage data into LOOM's event fabric."""

    def __init__(self, base_url=VANTAGE_URL):
        self.base = base_url.rstrip("/")
        self._cache = {}
        self._cache_ts = {}

    def _get(self, path, ttl=30):
        """Cached GET with TTL."""
        now = time.time()
        if path in self._cache and now - self._cache_ts.get(path, 0) < ttl:
            return self._cache[path]
        try:
            req = urllib.request.Request(f"{self.base}{path}",
                headers={"User-Agent": "LOOM-Puller/1.0"})
            with urllib.request.urlopen(req, timeout=8) as resp:
                data = json.loads(resp.read())
                self._cache[path] = data
                self._cache_ts[path] = now
                return data
        except Exception:
            return self._cache.get(path, {})

    # ── Data pullers ────────────────────────────────────────

    def pull_sentiment(self) -> dict:
        """Market sentiment from FinBERT + VADER."""
        return self._get("/api/intel/sentiment", ttl=60)

    def pull_arbitrage(self) -> list:
        """Cross-exchange arbitrage opportunities."""
        return self._get("/api/intel/arbitrage", ttl=60)

    def pull_whales(self) -> list:
        """BTC mempool whale transactions."""
        return self._get("/api/intel/whales", ttl=60)

    def pull_alpha(self) -> list:
        """High-conviction alpha feed from other agents."""
        return self._get("/api/intel/alpha", ttl=30)

    def pull_watchlist(self, agent_key=None) -> list:
        """Other agents' tracked wallets."""
        headers = {}
        if agent_key:
            headers["X-Agent-Key"] = agent_key
        try:
            req = urllib.request.Request(f"{self.base}/api/intel/watchlist",
                headers={**headers, "User-Agent": "LOOM-Puller/1.0"})
            with urllib.request.urlopen(req, timeout=8) as resp:
                return json.loads(resp.read())
        except Exception:
            return []

    def pull_memory_graph(self, agent_name="") -> dict:
        """Agent knowledge graph from memory vault."""
        return self._get(f"/api/intel/memory/graph?agent_name={agent_name}", ttl=120)

    def pull_yields(self) -> list:
        """DeFi yield opportunities."""
        return self._get("/api/intel/yields", ttl=300)

    def pull_debates(self) -> list:
        """Active agent debates."""
        return self._get("/api/intel/debate", ttl=30)

    # ── Enrichment loop ────────────────────────────────────

    def enrich_loop(self, fabric=None, whales=None, interval=30):
        """Continuously pull Vantage data into LOOM fabric."""
        print("  [vantage-pull] enriching LOOM with Vantage data...")

        while True:
            try:
                # Sentiment → fabric event
                sentiment = self.pull_sentiment()
                if sentiment and fabric:
                    score = sentiment.get("score", sentiment.get("sentiment_score", 0))
                    if isinstance(score, (int, float)):
                        fabric.ingest("vantage_sentiment", "market", abs(score)/10,
                                     source="vantage", metadata={"raw": str(sentiment)[:200]})

                # Alpha feed → fabric events
                alpha = self.pull_alpha()
                if alpha and fabric:
                    for sig in (alpha if isinstance(alpha, list) else alpha.get("signals", []))[:5]:
                        fabric.ingest("vantage_alpha", sig.get("symbol", "?"),
                                     sig.get("conviction", 0.5),
                                     source="vantage_alpha",
                                     metadata={"direction": sig.get("direction", "?")})

                # Cross-agent watchlist → feed into whale engine
                wl = self.pull_watchlist()
                if wl and whales:
                    for entry in (wl if isinstance(wl, list) else [])[:10]:
                        addr = entry.get("address", "")
                        if addr and addr not in whales.wallets:
                            whales.register_trade(addr, entry.get("label", "vantage"),
                                                "unknown", 0, time.time())

                # Arbitrage alerts
                arb = self.pull_arbitrage()
                if arb and fabric:
                    for a in (arb if isinstance(arb, list) else [])[:3]:
                        fabric.ingest("vantage_arb", a.get("pair", "?"),
                                     a.get("spread_pct", 1),
                                     source="vantage_arb")

            except Exception as e:
                print(f"  [vantage-pull] error: {e}")

            time.sleep(interval)


# Global instance
puller = VantagePuller()
