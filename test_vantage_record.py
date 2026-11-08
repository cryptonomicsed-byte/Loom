"""P1-05: Loom's pushes go to the intel door as signal_record/1.

Acceptance (Loom side): conviction 9.0 becomes 0.69; the push uses the intel
door with system-tool headers and never X-Agent-Key; the record carries the
mint when known and lineage loom-fabric. The 200-and-lands-in-pool-and-ledger
half is checked against Vantage itself (see the PR).
"""
import io
import json
import re

import pytest

import vantage_record as vr


def test_conviction_scales_and_caps():
    assert vr.normalise_conviction(9.0) == 0.69
    assert vr.normalise_conviction(0.42) == 0.42
    assert vr.normalise_conviction(0.95) == 0.69
    assert vr.normalise_conviction(-1) == 0.0
    assert vr.normalise_conviction("nope") == 0.0
    assert vr.normalise_conviction(float("nan")) == 0.0


def test_ulid_shape_and_order():
    a, b = vr.ulid(1_700_000_000_000), vr.ulid(1_700_000_000_001)
    assert re.fullmatch(r"[0-9A-HJKMNP-TV-Z]{26}", a) and a[:10] < b[:10]


def test_record_fields():
    mint = "DezXAZ8z7PnrnRJjz3wXBoRgixCa6xjnB7YaB1pPB263"  # secret-scan:allow (BONK mint, public constant)
    r = vr.build("BONK/USDC", "BUY", 9.0, "loom-whale-transformer", mint=mint)
    assert r["schema"] == "signal_record/1" and r["conviction"] == 0.69
    assert r["asset"] == {"symbol": "BONK", "chain": "solana", "address": mint}
    assert r["direction"] == "long" and r["producer"]["lineage"] == "loom-fabric"
    assert r["source_ref"].startswith("url:") and "mint=" in r["source_ref"]
    assert r["symbol"] == "BONK" and r["mint"] == mint  # legacy fields alongside
    btc = vr.build("BTC/USD", "SELL", 0.5, "loom-multi-agent-v2")
    assert btc["asset"] == {"symbol": "BTC", "chain": "bitcoin", "address": ""} and btc["direction"] == "short"
    with pytest.raises(ValueError):
        vr.build("X", "BUY", 0.5, "made-up-source")


class _Resp(io.BytesIO):
    status = 200
    def __enter__(self): return self
    def __exit__(self, *a): return False


def test_post_uses_the_intel_door_with_tool_headers(monkeypatch):
    monkeypatch.setenv("VANTAGE_TOOL_INTEL", "k" * 40)
    monkeypatch.setenv("VANTAGE_URL", "http://vantage.test:8001/")
    seen = {}
    def opener(req, timeout):
        seen["url"], seen["headers"], seen["body"] = req.full_url, dict(req.header_items()), json.loads(req.data)
        return _Resp(b'{"status": "ingested"}')
    out = vr.post(vr.build("SOL", "BUY", 0.5, "loom-whale-transformer"), opener=opener)
    assert out["published"] is True
    assert seen["url"] == "http://vantage.test:8001/api/intel/signals/ingest"
    h = {k.lower(): v for k, v in seen["headers"].items()}
    assert h["x-vantage-tool"] == "intel" and h["x-vantage-tool-key"] == "k" * 40
    assert "x-agent-key" not in h


def test_no_tool_key_means_no_post(monkeypatch):
    monkeypatch.delenv("VANTAGE_TOOL_INTEL", raising=False)
    def opener(req, timeout):
        raise AssertionError("must not post without the tool key")
    assert vr.post(vr.build("SOL", "BUY", 0.5, "loom-whale-transformer"), opener=opener)["published"] is False


def test_default_vantage_url_is_local_not_the_retired_host(monkeypatch):
    monkeypatch.delenv("VANTAGE_URL", raising=False)
    assert vr.vantage_url() == "http://127.0.0.1:8001"
    import vantage_predictor, vantage_puller  # noqa: F401  (import-time constants)
    src = open("vantage_predictor.py").read() + open("vantage_puller.py").read()
    assert "http://2.25.70.156" not in src  # no URL on the retired host (comments may name it)
