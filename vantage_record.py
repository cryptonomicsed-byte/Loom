"""signal_record/1 for Loom's pushes into Vantage (handoff P1-05).

Both of Loom's pushes -- vantage_predictor.publish_signal and
vantage_puller.push_consensus_to_vantage -- posted to
/api/trading/signals/ingest with X-Agent-Key, no agent_id and an unnormalised
conviction, so Vantage refused every one (the trading door is system-tool
auth and needs agent_id; conviction must be 0..1). They also targeted
2.25.70.156, a retired host.

Now they post to /api/intel/signals/ingest with the intel system-tool
headers (env VANTAGE_TOOL_INTEL), conviction normalised to 0..1 and capped at
0.69, as a signal_record/1 carrying today's legacy fields alongside (so it is
ingested either way, whether Vantage's SIGNAL_RECORD_V1 is on or off).
X-Agent-Key stays on pulls only.
"""
from __future__ import annotations

import json
import os
import secrets
import time
import urllib.request
from typing import Optional

SCHEMA = "signal_record/1"
CAP = 0.69
LINEAGE = "loom-fabric"
SOURCES = ("loom-multi-agent-v2", "loom-whale-transformer")
_CROCKFORD = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"


def vantage_url() -> str:
    return os.environ.get("VANTAGE_URL", "http://127.0.0.1:8001").rstrip("/")


def loom_base() -> str:
    return os.environ.get("LOOM_PUBLIC_BASE", "http://127.0.0.1:8889").rstrip("/")


def ulid(ts_ms: Optional[int] = None) -> str:
    """48-bit millisecond time + 80 random bits, Crockford base32 (26 chars)."""
    t = int(time.time() * 1000) if ts_ms is None else int(ts_ms)
    n = (t << 80) | secrets.randbits(80)
    out = []
    for _ in range(26):
        out.append(_CROCKFORD[n & 31])
        n >>= 5
    return "".join(reversed(out))


def normalise_conviction(raw) -> float:
    """0..1 out, capped at 0.69. A value above 1 is read as the 0-10 scale
    Loom's consensus uses (so 9.0 -> 0.9 -> capped 0.69)."""
    try:
        c = float(raw)
    except (TypeError, ValueError):
        return 0.0
    if c != c:  # NaN
        return 0.0
    if c > 1.0:
        c = c / 10.0
    return round(max(0.0, min(CAP, c)), 4)


def _direction(d: str) -> str:
    d = (d or "").strip().upper()
    if d in ("BUY", "LONG", "BULLISH"):
        return "long"
    if d in ("SELL", "SHORT", "BEARISH"):
        return "short"
    return "hold"


def _asset(symbol: str, mint: str = "") -> dict:
    base = (symbol or "?").split("/")[0].upper()[:20]
    chain = {"BTC": "bitcoin", "ETH": "ethereum"}.get(base, "solana")
    return {"symbol": base, "chain": chain, "address": mint or ""}


def build(symbol: str, direction: str, conviction, source: str, *, mint: str = "",
          detail: str = "", evidence: Optional[dict] = None, version: str = "loom",
          created_at: Optional[int] = None) -> dict:
    if source not in SOURCES:
        raise ValueError(f"source must be one of {SOURCES}")
    asset = _asset(symbol, mint)
    conv = normalise_conviction(conviction)
    now = int(time.time()) if created_at is None else int(created_at)
    rec = {
        "schema": SCHEMA,
        "id": ulid(now * 1000),
        "created_at": now,
        "source": source,
        # Evidence pointer: Loom's own scoped decision for this entity, which
        # can be re-queried (P1-06 makes /api/decide entity-scoped).
        "source_ref": f"url:{loom_base()}/api/decide?symbol={asset['symbol']}"
                      + (f"&mint={mint}" if mint else ""),
        "asset": asset,
        "type": "prediction" if source == "loom-whale-transformer" else "consensus",
        "direction": _direction(direction),
        "conviction": conv,
        "horizon_hours": 24,
        "producer": {"kind": "daemon", "agent_id": None, "version": version[:40],
                     "lineage": LINEAGE, "env": os.environ.get("LOOM_ENV", "contabo-vps"),
                     "blind": True},
        "evidence": evidence or {},
        "tags": ["loom"],
        "detail": str(detail or "")[:500],
    }
    # Legacy fields alongside, so the payload ingests whether or not Vantage
    # has SIGNAL_RECORD_V1 on.
    rec.update({"symbol": asset["symbol"], "mint": mint or ""})
    return rec


def post(record: dict, timeout: float = 8.0, opener=None) -> dict:
    key = os.environ.get("VANTAGE_TOOL_INTEL", "")
    if not key:
        return {"published": False, "reason": "VANTAGE_TOOL_INTEL not set"}
    req = urllib.request.Request(
        f"{vantage_url()}/api/intel/signals/ingest", data=json.dumps(record).encode(), method="POST",
        headers={"Content-Type": "application/json", "X-Vantage-Tool": "intel",
                 "X-Vantage-Tool-Key": key, "User-Agent": "LOOM/2.0"})
    try:
        with (opener or urllib.request.urlopen)(req, timeout=timeout) as resp:
            return {"published": True, "status": resp.status, "result": json.loads(resp.read() or b"{}")}
    except urllib.error.HTTPError as e:
        return {"published": False, "status": e.code, "reason": e.read()[:300].decode(errors="replace")}
    except Exception as e:
        return {"published": False, "reason": str(e)}
