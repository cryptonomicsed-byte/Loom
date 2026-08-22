#!/usr/bin/env python3
"""
Vantage → LOOM Data Bridge v2.
Pulls Vantage's intelligence into LOOM's fabric for enriched analysis.

v2 changes:
  - X-Agent-Key on ALL requests (was missing → all pulls failed silently)
  - Fixed fabric.ingest() → proper fabric.ingest_signal() calls
  - Added: Pyth market data, whale_monitor signals, DEX liquidity, debates, threat intel
  - Loads key from Loom/.vantage_key file
"""

import json
import time
import urllib.request
import threading
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

VANTAGE_URL = "http://2.25.70.156:8001"
KEY_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".vantage_key")


def _load_key():
    try:
        with open(KEY_FILE) as f:
            return f.read().strip()
    except Exception:
        # Fallback to home dir key
        try:
            with open(os.path.expanduser("~/.vantage_key")) as f:
                return f.read().strip()
        except Exception:
            return ""


class VantagePuller:
    """Pulls Vantage data into LOOM's event fabric."""

    def __init__(self, base_url=VANTAGE_URL):
        self.base = base_url.rstrip("/")
        self.key = _load_key()
        self._cache = {}
        self._cache_ts = {}
        print(f"  [vantage-pull] key={'✓' if self.key else '✗ (no auth)'}")

    def _headers(self):
        return {
            "X-Agent-Key": self.key,
            "User-Agent": "LOOM-Puller/2.0",
        }

    def _get(self, path, ttl=30):
        """Cached GET with TTL and auth."""
        now = time.time()
        if path in self._cache and now - self._cache_ts.get(path, 0) < ttl:
            return self._cache[path]
        try:
            req = urllib.request.Request(
                f"{self.base}{path}",
                headers=self._headers(),
            )
            with urllib.request.urlopen(req, timeout=8) as resp:
                data = json.loads(resp.read())
                self._cache[path] = data
                self._cache_ts[path] = now
                return data
        except Exception as e:
            # Return stale cache on error rather than empty
            return self._cache.get(path, {})

    # ── Existing endpoints (fixed) ──────────────────────────

    def pull_sentiment(self) -> dict:
        """Market sentiment from CoinGecko breadth + Fear & Greed."""
        return self._get("/api/intel/sentiment", ttl=60)

    def pull_arbitrage(self) -> dict:
        """Cross-exchange arbitrage opportunities."""
        return self._get("/api/intel/arbitrage", ttl=60)

    def pull_whales(self) -> dict:
        """BTC mempool whale transactions."""
        return self._get("/api/intel/whales", ttl=60)

    def pull_alpha(self) -> dict:
        """High-conviction alpha feed."""
        return self._get("/api/intel/alpha", ttl=30)

    def pull_watchlist(self) -> list:
        """Other agents' tracked wallets."""
        data = self._get("/api/intel/watchlist", ttl=60)
        return data.get("wallets", data if isinstance(data, list) else [])

    def pull_memory_graph(self, agent_name="") -> dict:
        """Agent knowledge graph from memory vault."""
        return self._get(f"/api/intel/memory/graph?agent_name={agent_name}", ttl=120)

    def pull_yields(self) -> list:
        """DeFi yield opportunities."""
        return self._get("/api/intel/yields", ttl=300)

    # ── NEW: Pyth Market Data ───────────────────────────────

    def pull_market(self) -> dict:
        """Pyth Network real-time prices — 98 tokens, institutional grade."""
        return self._get("/api/intel/market", ttl=15)

    # ── NEW: DEX Liquidity ──────────────────────────────────

    def pull_dex(self, query="SOL") -> dict:
        """DEX pairs and liquidity for a symbol."""
        return self._get(f"/api/intel/dex?query={query}", ttl=30)

    # ── NEW: Signals by source ──────────────────────────────

    def pull_signals(self, limit=30) -> dict:
        """All trading signals from 13 sources."""
        return self._get(f"/api/intel/signals?limit={limit}", ttl=15)

    def pull_signals_by_source(self, source, limit=10) -> list:
        """Filter signals by specific source."""
        data = self.pull_signals(limit=max(limit, 30))
        signals = data.get("signals", [])
        return [s for s in signals if s.get("source") == source]

    # ── Enrichment loop (rewritten) ─────────────────────────

    def enrich_loop(self, fabric=None, whales=None, orchestrator=None, interval=30):
        """Continuously pull Vantage data into LOOM fabric."""
        print("  [vantage-pull] enriching LOOM with Vantage data...")

        # Give fabric time to initialize
        time.sleep(5)

        while True:
            try:
                self._enrich_cycle(fabric, whales, orchestrator)
            except Exception as e:
                print(f"  [vantage-pull] cycle error: {e}")

            time.sleep(interval)

    def _enrich_cycle(self, fabric, whales, orchestrator=None):
        """One enrichment cycle — pull everything and feed fabric."""

        # 1. Pyth Market Data → price events for every token
        market = self.pull_market()
        if market and fabric:
            tokens = market.get("tokens", [])
            total_feeds = market.get("total_feeds_available", 0)
            for token in tokens:
                try:
                    symbol = token.get("symbol", "?")
                    price = token.get("price", 0)
                    if price <= 0 or symbol == "?":
                        continue
                    token["_total_feeds"] = total_feeds
                    fabric.ingest_pyth_price(token)
                except Exception:
                    pass
            if tokens:
                print(f"  [vantage-pull] market: {len(tokens)} Pyth prices (of {total_feeds} feeds)")

        # 2. Sentiment → fabric
        sentiment = self.pull_sentiment()
        if sentiment and fabric:
            sent_data = sentiment.get("sentiment", sentiment)
            overall = sent_data.get("overall", "neutral")
            fear_greed = sent_data.get("fear_greed", 50)
            try:
                fabric.ingest_signal({
                    "symbol": "FEAR_GREED",
                    "source": "vantage_sentiment",
                    "type": "sentiment",
                    "score": fear_greed / 10,
                    "conviction": 7,
                    "change_6h": 0,
                    "ts": time.time(),
                    "price": fear_greed,
                })
            except Exception:
                pass

        # 3. Signals → fabric (all 13 sources)
        signals_data = self.pull_signals()
        if signals_data and fabric:
            count = 0
            for sig in signals_data.get("signals", []):
                try:
                    if sig.get("score", 0) <= 0 and sig.get("type") not in ("whale", "debate", "threat_intel"):
                        continue  # Skip zero-score signals unless special type
                    fabric.ingest_signal({
                        "symbol": sig.get("symbol", "?"),
                        "source": sig.get("source", "vantage"),
                        "type": sig.get("type", "signal"),
                        "score": sig.get("score", 0),
                        "conviction": min(10, sig.get("score", 0)),
                        "change_6h": 0,
                        "ts": time.time(),
                        "price": sig.get("price", 0),
                        "name": sig.get("name", ""),
                        "address": sig.get("address", ""),
                    })
                    count += 1
                except Exception:
                    pass
            if count:
                print(f"  [vantage-pull] signals: {count} events")

        # 4. Alpha feed → fabric
        alpha = self.pull_alpha()
        if alpha and fabric:
            items = alpha.get("items", [])
            for item in items[:10]:
                try:
                    fabric.ingest_signal({
                        "symbol": item.get("symbol", "?"),
                        "source": "vantage_alpha",
                        "type": item.get("signal", "alpha"),
                        "score": item.get("conviction", 0),
                        "conviction": min(10, item.get("conviction", 5)),
                        "change_6h": item.get("change_24h", 0),
                        "ts": time.time(),
                        "price": item.get("price", 0),
                    })
                except Exception:
                    pass

        # 5. Whale_monitor signals → whale engine (parse wallet from detail)
        whale_signals = self.pull_signals_by_source("whale_monitor", limit=10)
        if whale_signals and whales:
            for ws in whale_signals:
                try:
                    symbol = ws.get("symbol", "?")
                    detail = ws.get("detail", "")
                    # Parse wallet address from detail like "Binance Hot Wallet 1: -2921 SOL"
                    amount_sol = ws.get("conviction", 0) * 1000  # Scale conviction to SOL
                    direction = ws.get("direction", "BUY")
                    entry_sol = abs(amount_sol) if amount_sol > 0 else 0.5
                    # Register as a tracked entity
                    whales.register_trade(
                        f"vantage_whale_{symbol}",
                        token=symbol,
                        token_address=f"vantage:{symbol}",
                        entry_sol=entry_sol,
                        entry_time=time.time(),
                    )
                except Exception:
                    pass
            if whale_signals:
                print(f"  [vantage-pull] whale_monitor: {len(whale_signals)} signals")

        # 6. Trading agent debates → feed as external agent signals
        debate_signals = self.pull_signals_by_source("trading_agents", limit=10)
        if debate_signals and fabric:
            for ds in debate_signals:
                try:
                    # Feed as a high-priority agent_signal for the orchestrator
                    fabric.ingest_signal({
                        "symbol": ds.get("symbol", "?"),
                        "source": "vantage_debate",
                        "type": "debate",
                        "score": ds.get("conviction", 0.5) * 10,
                        "conviction": ds.get("conviction", 0.5) * 10,
                        "change_6h": 0,
                        "ts": ds.get("ts", time.time()),
                        "price": 0,
                        "direction": ds.get("direction", "WAIT"),
                        "detail": ds.get("detail", ""),
                    })
                except Exception:
                    pass
            if debate_signals:
                print(f"  [vantage-pull] debates: {len(debate_signals)} signals")

        # 7. Dune on-chain → enrich wallet dossiers
        dune_signals = self.pull_signals_by_source("dune", limit=10)
        if dune_signals and fabric:
            for ds in dune_signals:
                try:
                    fabric.ingest_signal({
                        "symbol": ds.get("symbol", "?"),
                        "source": "dune",
                        "type": "onchain",
                        "score": ds.get("conviction", 0.3) * 10,
                        "conviction": ds.get("conviction", 0.3) * 10,
                        "change_6h": 0,
                        "ts": ds.get("ts", time.time()),
                        "price": 0,
                        "detail": ds.get("detail", ""),
                    })
                except Exception:
                    pass

        # 8. STIX threat intel → high-priority events
        stix_count = 0
        for stix_src in ["stix_actors", "stix_sanctions"]:
            stix_signals = self.pull_signals_by_source(stix_src, limit=10)
            if stix_signals and fabric:
                for ss in stix_signals:
                    try:
                        fabric.ingest_signal({
                            "symbol": ss.get("symbol", "?"),
                            "source": stix_src,
                            "type": "threat_intel",
                            "score": ss.get("conviction", 0.7) * 10,
                            "conviction": ss.get("conviction", 0.7) * 10,
                            "change_6h": 0,
                            "ts": ss.get("ts", time.time()),
                            "price": 0,
                            "direction": ss.get("direction", "SELL"),
                            "detail": ss.get("detail", ""),
                        })
                        stix_count += 1
                    except Exception:
                        pass
        if stix_count:
            print(f"  [vantage-pull] threat_intel: {stix_count} events")

        # 6. Arbitrage → fabric
        arb = self.pull_arbitrage()
        if arb and fabric:
            opps = arb.get("opportunities", [])
            for a in opps[:5]:
                try:
                    fabric.ingest_signal({
                        "symbol": a.get("pair", "?").split("/")[0],
                        "source": "vantage_arb",
                        "type": "arbitrage",
                        "score": a.get("spread_pct", 0) * 10,
                        "conviction": min(10, a.get("spread_pct", 0) * 20),
                        "change_6h": a.get("spread_pct", 0),
                        "ts": time.time(),
                        "price": a.get("buy_price", 0),
                    })
                except Exception:
                    pass

        # 7. Watchlist → whale engine
        wl = self.pull_watchlist()
        if wl and whales:
            for entry in wl[:20]:
                try:
                    addr = entry.get("address", "")
                    if addr and addr not in getattr(whales, 'wallets', {}):
                        whales.register_trade(
                            addr,
                            token=entry.get("label", "vantage")[:20],
                            token_address=addr,
                            entry_sol=0.001,
                            entry_time=time.time(),
                        )
                except Exception:
                    pass

        # 9. DEX liquidity → LiquidityFlowEngine pool tracking
        dex_symbols = ["SOL", "BTC", "ETH", "BONK", "WIF"]
        dex_total = 0
        for sym in dex_symbols:
            try:
                dex = self.pull_dex(sym)
                if dex:
                    pairs = dex.get("pairs", [])
                    for p in pairs[:3]:
                        liq = p.get("liquidity_usd", 0)
                        vol = p.get("volume_24h", 0)
                        price = p.get("price_usd", 0)
                        if liq > 0:
                            from engines import flow_engine
                            if flow_engine:
                                flow_engine.track_pool_liquidity(
                                    pair=p.get("pair", f"{sym}/USD"),
                                    dex=p.get("dex", "unknown"),
                                    chain=p.get("chain", "solana"),
                                    liquidity_usd=liq,
                                    volume_24h=vol,
                                    price_usd=price,
                                )
                                dex_total += 1
            except Exception:
                pass
        if dex_total:
            pools = 0
            try:
                from engines import flow_engine
                pools = len(flow_engine.get_pool_liquidity()) if flow_engine else 0
            except Exception:
                pass
            print(f"  [vantage-pull] dex: {dex_total} pairs → {pools} pools tracked")

        # 10. Cross-platform debate fusion
        if orchestrator:
            try:
                # Feed Vantage debates → Loom orchestrator
                fused = self.feed_debates_to_orchestrator(orchestrator)
                # Run a consensus cycle for each symbol with external votes
                if fused > 0:
                    symbols_with_votes = set(
                        v["symbol"] for v in orchestrator.get_external_votes("")
                        if isinstance(v, dict)
                    ) if hasattr(orchestrator, 'external_votes') else set()
                    # Actually get symbols from external_votes dict keys
                    symbols_with_votes = list(orchestrator.external_votes.keys())[-3:]
                    for sym in symbols_with_votes:
                        result = orchestrator.decide(sym)
                        if result.get("consensus", {}).get("cross_platform"):
                            self.push_consensus_to_vantage(result)
            except Exception:
                pass

    # ── Cross-platform debate fusion ─────────────────────────

    def push_consensus_to_vantage(self, orchestrator_result: dict) -> bool:
        """Push Loom's agent consensus back to Vantage for cross-platform debate."""
        try:
            symbol = orchestrator_result.get("symbol", "?")
            consensus = orchestrator_result.get("consensus", {})
            body = json.dumps({
                "symbol": symbol,
                "direction": consensus.get("direction", "WAIT"),
                "conviction": consensus.get("conviction", 0),
                "source": "loom-multi-agent-v2",
                "debate": consensus.get("debate", False),
                "cross_platform": consensus.get("cross_platform", False),
                "external_vote_count": orchestrator_result.get("external_vote_count", 0),
                "timestamp": time.time(),
            }).encode()

            req = urllib.request.Request(
                f"{self.base}/api/trading/signals/ingest",
                data=body,
                headers={
                    **self._headers(),
                    "Content-Type": "application/json",
                },
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=8) as resp:
                return True
        except Exception:
            return False

    def feed_debates_to_orchestrator(self, orchestrator) -> int:
        """Pull Vantage trading_agent debates and feed as external votes."""
        debate_signals = self.pull_signals_by_source("trading_agents", limit=10)
        count = 0
        for ds in debate_signals:
            try:
                symbol = ds.get("symbol", "?")
                direction = ds.get("direction", "WAIT")
                conviction = ds.get("conviction", 0.5)
                detail = ds.get("detail", "")
                # Parse agent breakdown from detail: "analyst=BUY tech=BUY risk=BUY"
                parts = dict(p.split("=") for p in detail.split() if "=" in p)
                for agent_name, vote in parts.items():
                    if vote in ("BUY", "SELL", "WAIT"):
                        orchestrator.add_external_vote(
                            symbol=symbol,
                            direction=vote,
                            conviction=conviction,
                            agent_name=f"vantage_{agent_name}",
                            source="vantage_debate",
                        )
                        count += 1
            except Exception:
                pass
        if count:
            print(f"  [vantage-pull] fused {count} external votes into orchestrator")
        return count


# Global instance
puller = VantagePuller()
