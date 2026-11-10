#!/usr/bin/env python3
"""
LOOM OHLCV Feed — paced market data from GeckoTerminal + DexScreener.

Sources (both public, no API key):
  - GeckoTerminal : OHLCV candles + pool metadata.  ~30 req/min → paced.
  - DexScreener   : pair metadata (liquidity, volume, txns, pair age,
                    price-change windows) + token-profiles for universe hints.

Design:
  - PacedFetcher enforces a minimum interval per host with jitter, plus
    exponential backoff on HTTP 429/5xx and transient network errors.
  - On-disk JSON cache under <repo>/reports/.cache/ with a TTL, so repeated
    backtest runs do not re-hit the rate limits.
  - No pandas dependency — numpy + stdlib only.

Usage:
    from ohlcv_feed import get_token_market_data
    md = get_token_market_data("DezXAZ8z7PnrnRJjz3wXBoRgixCa6xjnB7YaB1pPB263", days=30)
    # md["ohlcv"] -> dict(ts, open, high, low, close, volume) as numpy arrays
    # md["pairs"] -> DexScreener pairs (solana-first), md["pool"] -> GT pool
"""

import json
import os
import threading
import time
import urllib.parse
import urllib.request
import urllib.error

import numpy as np

# ── Config ─────────────────────────────────────────────────────
GT_NETWORK = "solana"
GT_BASE = "https://api.geckoterminal.com/api/v2"
DEX_BASE = "https://api.dexscreener.com"
DEX_PROFILES_URL = "https://api.dexscreener.com/token-profiles/latest/v1"

CACHE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "reports", ".cache")
CACHE_TTL_PAIR = 6 * 3600      # pair/pool metadata freshness (s)
CACHE_TTL_OHLCV = 6 * 3600     # OHLCV freshness (s)

UA = "LOOM-signal-panel/1.0 (trading-signal-enhancement)"

# Per-host pacing: GeckoTerminal ≈30 req/min → 2.2s spacing; DexScreener 1s.
HOST_PACE = {
    "api.geckoterminal.com": 2.2,
    "api.dexscreener.com": 1.0,
}


class PacedFetcher:
    """Thread-safe HTTP GET with per-host pacing, jitter and backoff."""

    def __init__(self, timeout: float = 15.0, max_retries: int = 3):
        self.timeout = timeout
        self.max_retries = max_retries
        self._last: dict = {}
        self._lock = threading.Lock()

    def _pace(self, host: str):
        interval = HOST_PACE.get(host, 1.5)
        with self._lock:
            last = self._last.get(host, 0.0)
            wait = interval - (time.time() - last)
            if wait > 0:
                time.sleep(wait + 0.15)  # small jitter to de-phase
            self._last[host] = time.time()

    def fetch_json(self, url: str, headers: dict = None) -> dict:
        host = urllib.parse.urlsplit(url).hostname or "unknown"
        hdrs = {"User-Agent": UA}
        if headers:
            hdrs.update(headers)

        for attempt in range(self.max_retries):
            self._pace(host)
            try:
                req = urllib.request.Request(url, headers=hdrs)
                with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                    return json.loads(resp.read())
            except urllib.error.HTTPError as e:
                if e.code in (429, 500, 502, 503, 504):
                    time.sleep(min(2 ** attempt, 8))
                    continue
                if e.code == 404:
                    return {}
                raise
            except (urllib.error.URLError, OSError, ValueError) as e:
                if attempt < self.max_retries - 1:
                    time.sleep(min(2 ** attempt, 8))
                    continue
                raise
        return {}


_fetcher = PacedFetcher()


# ── Disk cache ─────────────────────────────────────────────────

def _cache_path(name: str) -> str:
    safe = "".join(c if c.isalnum() or c in "-_." else "_" for c in name)
    return os.path.join(CACHE_DIR, f"{safe}.json")


def _cache_load(path: str, ttl: float):
    try:
        with open(path) as f:
            data = json.load(f)
        if time.time() - data.get("fetched_at", 0) < ttl:
            return data.get("payload")
    except Exception:
        pass
    return None


def _cache_save(path: str, payload):
    try:
        os.makedirs(CACHE_DIR, exist_ok=True)
        with open(path, "w") as f:
            json.dump({"fetched_at": time.time(), "payload": payload}, f)
    except Exception:
        pass


# ── DexScreener ────────────────────────────────────────────────

def fetch_dex_token(mint: str) -> dict:
    """Full DexScreener record for a token mint (all chains/pairs)."""
    url = f"{DEX_BASE}/latest/dex/tokens/{mint}"
    try:
        return _fetcher.fetch_json(url)
    except Exception:
        return {}


def dex_solana_pairs(mint: str, limit: int = 5) -> list:
    """Solana pairs for a mint, ranked by liquidity, enriched with flags."""
    data = fetch_dex_token(mint)
    pairs = data.get("pairs") or []
    sol = [p for p in pairs if p.get("chainId") == "solana"]
    if not sol:
        sol = pairs  # fall back to any chain so the caller sees the token
    sol.sort(key=lambda p: (p.get("liquidity") or {}).get("usd", 0) or 0,
             reverse=True)
    return sol[:limit]


def fetch_dex_profiles(limit: int = 30) -> list:
    """Newest token profiles (universe hints). Public endpoint, no key."""
    try:
        data = _fetcher.fetch_json(DEX_PROFILES_URL)
        if isinstance(data, list):
            return data[:limit]
    except Exception:
        pass
    return []


# ── GeckoTerminal ──────────────────────────────────────────────

def fetch_gt_pools(mint: str, limit: int = 10) -> list:
    """Top pools for a token on the configured network."""
    url = f"{GT_BASE}/networks/{GT_NETWORK}/tokens/{mint}/pools?page=1"
    try:
        data = _fetcher.fetch_json(url)
        pools = data.get("data") or []
        def reserve(p):
            a = p.get("attributes") or {}
            return float(a.get("reserve_in_usd") or 0)
        pools.sort(key=reserve, reverse=True)
        return pools[:limit]
    except Exception:
        return []


def fetch_gt_ohlcv(pool_address: str, timeframe: str = "hour",
                   aggregate: int = 1, limit: int = 750) -> list:
    """OHLCV rows [[ts, o, h, l, c, v], ...] for a pool (oldest → newest).

    GeckoTerminal returns rows newest-first with `limit` capped at 1000.
    """
    url = (f"{GT_BASE}/networks/{GT_NETWORK}/pools/{pool_address}/ohlcv/"
           f"{timeframe}?aggregate={aggregate}&limit={limit}")
    try:
        data = _fetcher.fetch_json(url)
        rows = (data.get("data") or {}).get("attributes") or {}
        ohlcv = rows.get("ohlcv_list") or []
        ohlcv = [r for r in ohlcv if isinstance(r, list) and len(r) >= 6]
        ohlcv.sort(key=lambda r: r[0])  # oldest → newest
        return ohlcv
    except Exception:
        return []


# ── Unified market-data assembly ───────────────────────────────

def _pick_gt_pool(pools: list) -> dict:
    """Pick the most liquid pool whose base token is most likely the target."""
    if not pools:
        return None
    return pools[0]


def get_token_market_data(mint: str, days: int = 30, use_cache: bool = True) -> dict:
    """Assemble market data for a token.

    Returns:
        {
          "mint": str, "symbol": str, "fetched_at": float,
          "pairs": [dex pair dicts...],      # liquidity-ranked, solana-first
          "pool":  {gt pool dict} | None,    # most liquid GT pool
          "ohlcv": {ts, open, high, low, close, volume} (numpy arrays) | None,
          "bars": int,
          "errors": [str...]
        }
    """
    key = f"{mint}_{days}d"
    if use_cache:
        cached = _cache_load(_cache_path("md_" + key), CACHE_TTL_PAIR)
        if cached:
            return cached

    out = {"mint": mint, "symbol": "?", "fetched_at": time.time(),
           "pairs": [], "pool": None, "ohlcv": None, "bars": 0, "errors": []}

    # 1) DexScreener pair metadata
    pairs = dex_solana_pairs(mint)
    if pairs:
        out["pairs"] = pairs
        base = pairs[0].get("baseToken") or {}
        out["symbol"] = base.get("symbol") or out["symbol"]

    # 2) GeckoTerminal pool + OHLCV
    pools = fetch_gt_pools(mint)
    pool = _pick_gt_pool(pools)
    if pool:
        out["pool"] = pool
        attrs = pool.get("attributes") or {}
        name = attrs.get("name") or ""
        # symbol hint: pool name looks like "TOKEN / QUOTE"
        if name and "/" in name:
            out["symbol"] = name.split("/")[0].strip() or out["symbol"]
        pool_addr = attrs.get("address")
        if pool_addr:
            limit = min(750, days * 24 + 24)  # 1h bars, small headroom
            rows = fetch_gt_ohlcv(pool_addr, "hour", 1, limit)
            if rows:
                arr = np.array(rows, dtype=np.float64)
                out["ohlcv"] = {
                    "ts": arr[:, 0],
                    "open": arr[:, 1],
                    "high": arr[:, 2],
                    "low": arr[:, 3],
                    "close": arr[:, 4],
                    "volume": arr[:, 5],
                }
                out["bars"] = len(rows)

    if use_cache:
        _cache_save(_cache_path("md_" + key), out)
    return out


# ── Convenience ────────────────────────────────────────────────

def ohlcv_slice(md: dict, days: int = 30) -> dict:
    """Trim ohlcv arrays to the last N days (1h bars → 24N rows)."""
    o = md.get("ohlcv")
    if not o:
        return None
    n = min(len(o["ts"]), days * 24)
    return {k: v[-n:] for k, v in o.items()}


if __name__ == "__main__":
    import sys
    mint = sys.argv[1] if len(sys.argv) > 1 else "DezXAZ8z7PnrnRJjz3wXBoRgixCa6xjnB7YaB1pPB263"
    md = get_token_market_data(mint, days=30)
    print(f"symbol={md['symbol']} pairs={len(md['pairs'])} pool={bool(md['pool'])} bars={md['bars']} errors={md['errors']}")
    if md["ohlcv"]:
        c = md["ohlcv"]["close"]
        print(f"close: n={len(c)} first={c[0]:.8f} last={c[-1]:.8f} "
              f"min={c.min():.8f} max={c.max():.8f}")
    if md["pairs"]:
        p = md["pairs"][0]
        print("top pair:", p.get("dexId"), p.get("baseToken", {}).get("symbol"),
              "liq_usd=", (p.get("liquidity") or {}).get("usd"),
              "vol24h=", (p.get("volume") or {}).get("h24"))
