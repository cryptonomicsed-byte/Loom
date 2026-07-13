"""waggle_field — LOOM's connection to the Waggle stigmergic field.

Connection Map v2 §5: trade outcomes auto-deposit gold/dead-end on strategy
URIs, Fractal Oracle verdicts land on the shared `bounded` channel (closing
the visualization-to-substrate gap), MarketEvents fan out to both Axiom and
the field from one call, regime shifts surface as bounded-decay anomalies,
and the Sniper/Warrior/Scalper presets compete for capital through the
field's own gradient instead of a config flag.

The dead-cat-bounce filter (§5.7) needs no code here: it lives in the
substrate itself — the `bounded` channel's registration cross-inhibits gold
(low-mode, ref 0.5, floor 0.25), so a win reported inside territory the
Oracle currently classifies as a fragile escape zone is automatically read
with skepticism by every consumer of `effective_intensity` and weighted
gradients.

Stdlib only. Everything fails soft: a missing substrate never interrupts
trading, it just leaves no scent.

    from waggle_field import FieldLink
    field = FieldLink()
    field.trade_outcome("SCALPER", "sol/BONK", filled=True, pnl_pct=4.2)
    weights = field.preset_allocation(["SNIPER", "WARRIOR", "SCALPER"])
"""

from __future__ import annotations

import json
import math
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone

WAGGLE = os.environ.get("WAGGLE_URL", "http://127.0.0.1:7777").rstrip("/")
ORACLE = os.environ.get("FRACTAL_ORACLE_URL", "http://127.0.0.1:7778").rstrip("/")

# MarketEvent.event_type → signal kind on the field
EVENT_KIND = {
    "price_surge": "gold",
    "volume_spike": "explored",
    "whale_move": "gold",
    "pattern_detected": "explored",
    "agent_signal": "explored",
    "anomaly": "warn",
}


def _http(method: str, url: str, body=None, timeout: float = 5.0):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method,
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode())
    except (urllib.error.URLError, urllib.error.HTTPError, OSError, ValueError):
        return None  # field absent: trading must not break


class FieldLink:
    """LOOM's handle on the scent field + the shared fractal-oracle."""

    def __init__(self, agent: str = "loom"):
        self.agent = agent
        self._watch_id = None

    # ── watch: trade outcomes as instrument readings (§5.1) ─────────────

    def _watch(self):
        """Trade outcomes flow through a watch so they carry watch-derived
        trust: the execution engine observed the fill, nobody self-reported."""
        if self._watch_id:
            return self._watch_id
        out = _http("POST", f"{WAGGLE}/v1/watches", {
            "agent": self.agent, "name": "confluence forge outcomes",
            "resource_prefix": "loom://strategy/",
            "map": {"success": "gold", "failure": "dead-end"}})
        if out:
            self._watch_id = out["watch"]["id"]
        return self._watch_id

    def trade_outcome(self, preset: str, market: str, *, filled: bool,
                      pnl_pct: float = 0.0, reason: str = "",
                      commission_usd: float = 0.0, decision_ms: float = 0.0) -> dict | None:
        """Filled/positive → gold on the strategy-parameter URI; rejected or
        stopped out → dead-end. Intensity scales with |pnl|.

        commission_usd (fees + slippage) and decision_ms (wall-clock of the
        decision path) attach as the deposit's cost, so cost-aware routing
        (sniff optimize=cost_efficiency) can prefer strategies whose gold was
        cheap to produce over ones whose equal-strength gold burned fees or
        latency (round 2, #4). Confluence Forge's backtester already computes
        commission/slippage — same numbers, this destination."""
        wid = self._watch()
        if not wid:
            return None
        win = filled and pnl_pct >= 0
        event = {
            "resource": f"{preset}/{market}",
            "outcome": "success" if win else "failure",
            "intensity": min(10.0, 1.0 + abs(pnl_pct) / 5.0),
            "note": reason or f"pnl {pnl_pct:+.1f}%",
            "meta": {"pnl_pct": f"{pnl_pct:.2f}", "market": market}}
        if commission_usd or decision_ms:
            # stamp provenance so a cost-efficiency spread is auditable to its
            # instrumentation, not trusted blind: LOOM meters real fees+slippage
            # (dollars) and decision wall-clock (ms), not token counts.
            event["cost"] = {
                "dollars": commission_usd, "wall_clock_ms": decision_ms,
                "source": {"producer": "loom",
                           "method": "commission+slippage+decision-latency",
                           "units": "dollars+ms"}}
        return _http("POST", f"{WAGGLE}/v1/ingest/{wid}", event)

    # ── oracle verdicts onto the bounded channel (§5.2) ──────────────────

    def oracle_verdict(self, resource: str, re: float, im: float,
                       depth: int = 2) -> dict | None:
        """One call: the shared fractal-oracle scores the point AND deposits
        the bounded verdict itself (watch-derived — the instrument reports).
        Returns the oracle result."""
        return _http("POST", f"{ORACLE}/v1/invoke", {
            "tool": "escape_time_risk",
            "arg": {"re": re, "im": im, "depth": depth},
            "deposit": {"resource": resource, "agent": "fractal-oracle"}})

    def swarm_stability(self, points: list[tuple[float, float]],
                        resource: str = "") -> dict | None:
        """Aggregate swarm verdict; optionally deposited for Yemọja's
        spawn-throttling to read (§8.3)."""
        body = {"tool": "swarm_stability_map", "arg": {"points": points}}
        if resource:
            body["deposit"] = {"resource": resource}
        return _http("POST", f"{ORACLE}/v1/invoke", body)

    # ── MarketEvent fan-out (§5.3) ────────────────────────────────────────

    def market_event(self, ev) -> dict:
        """One MarketEvent → a Waggle deposit AND an Axiom message_pulse
        payload, from the same call. Pass the fabric.MarketEvent (or its
        to_dict()); returns the pulse dict for the galaxy feed."""
        d = ev.to_dict() if hasattr(ev, "to_dict") else dict(ev)
        kind = EVENT_KIND.get(d.get("event_type", ""), "explored")
        resource = f"market://{d.get('entity', 'unknown')}"
        _http("POST", f"{WAGGLE}/v1/signals", {
            "agent": self.agent, "resource": resource, "kind": kind,
            "subtype": d.get("event_type", ""),
            "intensity": max(0.5, 10.0 * float(d.get("magnitude", 0.1))
                             * float(d.get("confidence", 0.5))),
            "half_life_s": 900,  # markets move fast; scent should too
            "note": f"{d.get('event_type')} via {d.get('source')}",
            "meta": {"source": str(d.get("source", "")),
                     "symbol": str(d.get("symbol", ""))}})
        return {  # Axiom GraphEngine message_pulse shape
            "type": "message_pulse",
            "from": f"loom-{d.get('source', 'fabric')}",
            "to": resource,
            "payload": {"event": d.get("event_type"),
                        "magnitude": d.get("magnitude"),
                        "anomaly": d.get("anomaly_score", 0)},
        }

    # ── regime-shift detection via bounded decay anomaly (§5.4) ──────────

    def regime_shift(self, resource: str, *, hours: float = 6.0,
                     half_life_s: float = 7200.0) -> dict | None:
        """The leading indicator: not 'the strategy lost money' (lagging) but
        'the ground under it is destabilizing'. Compares the bounded verdict
        now against the journal's state `hours` ago; RSI is stability lost
        per half-life. > 0.5 = early warning, > 1.0 = active regime break.
        Reads only the field's own verdict history — no new model."""
        at = (datetime.now(timezone.utc) - timedelta(hours=hours)) \
            .strftime("%Y-%m-%dT%H:%M:%SZ")
        q = urllib.parse.urlencode({"resource": resource, "kind": "bounded", "at": at})
        past = _http("GET", f"{WAGGLE}/v1/recall?{q}")
        now = _http("GET", f"{WAGGLE}/v1/sniff?" + urllib.parse.urlencode(
            {"resource": resource, "kind": "bounded"}))
        if not past or not now:
            return None
        p_sigs, n_sigs = past.get("signals") or [], now.get("signals") or []
        if not p_sigs or not n_sigs:
            return None
        s_then = p_sigs[0]["intensity"] / 10.0
        s_now = n_sigs[0]["intensity"] / 10.0
        rsi = (s_then - s_now) * (half_life_s / (hours * 3600.0))
        return {"resource": resource, "s_then": round(s_then, 4),
                "s_now": round(s_now, 4), "rsi": round(rsi, 4),
                "warning": rsi > 0.5, "regime_break": rsi > 1.0}

    # ── preset competition via quorum-gated selection (§5.5) ─────────────

    def preset_allocation(self, presets: list[str],
                          temperature: float = 1.0) -> dict[str, float]:
        """Sniper/Warrior/Scalper compete through the field: one batched
        sniff prices every preset's live gold+bounded gradient
        (trust-weighted, dead-cat-filtered by the substrate), and capital
        weights follow a softmax over the totals. Self-rebalancing — no
        human flips a config flag. Returns {preset: weight}, uniform when
        the field is cold or absent."""
        uris = [f"loom://strategy/{p}" for p in presets]
        out = _http("POST", f"{WAGGLE}/v1/sniff/batch",
                    {"uris": uris, "weighted": True})
        uniform = {p: 1.0 / len(presets) for p in presets} if presets else {}
        if not out:
            return uniform
        results = out.get("results") or {}
        totals = {p: float((results.get(f"loom://strategy/{p}") or {}).get("total", 0.0))
                  for p in presets}
        if all(v <= 0 for v in totals.values()):
            return uniform
        exps = {p: math.exp(v / max(temperature, 1e-9)) for p, v in totals.items()}
        z = sum(exps.values())
        return {p: round(e / z, 4) for p, e in exps.items()}

    # ── Oracle-confirmed reputation feed for Ṣàngó (§5.6) ────────────────

    def stability_reputation(self, trader: str, gold_resources: list[str],
                             *, depth: int = 2) -> dict | None:
        """A trader whose gold regions the Oracle later confirms bounded
        earns reputation faster than one whose wins sit in fragile escape
        zones. Re-scans the trader's gold territory, computes the confirmed
        fraction, and deposits it on sango://reputation/<trader> for the
        on-chain relay to pick up."""
        if not gold_resources:
            return None
        confirmed = 0
        for res in gold_resources:
            ex = _http("GET", f"{WAGGLE}/v1/explain?" +
                       urllib.parse.urlencode({"resource": res}))
            if not ex:
                continue
            by_kind = ex.get("by_kind_raw") or {}
            if by_kind.get("bounded", 0.0) >= 5.0:  # stability ≥ 0.5 live
                confirmed += 1
        frac = confirmed / len(gold_resources)
        return _http("POST", f"{WAGGLE}/v1/signals", {
            "agent": self.agent, "resource": f"sango://reputation/{trader}",
            "kind": "gold", "intensity": 10.0 * frac, "decay": "power",
            "note": f"{confirmed}/{len(gold_resources)} gold regions oracle-confirmed bounded",
            "meta": {"trader": trader, "confirmed_fraction": f"{frac:.3f}"}})


def patch_trading_agents(manager, field: FieldLink | None = None):
    """Opt-in: wrap every TradingAgent.exit_position on an AgentManager so
    closed positions auto-deposit their outcome. No-op on the trading logic
    itself; the field only observes.

        from waggle_field import FieldLink, patch_trading_agents
        patch_trading_agents(manager)
    """
    field = field or FieldLink()
    for agent in getattr(manager, "agents", []):
        original = agent.exit_position

        def wrapped(position, reason, *a, _orig=original, _agent=agent, **kw):
            out = _orig(position, reason, *a, **kw)
            try:
                entry = getattr(position, "entry_price_sol", 0) or 0
                cur = getattr(position, "current_price_sol", entry)
                pnl = ((cur - entry) / entry * 100.0) if entry else 0.0
                field.trade_outcome(
                    _agent.profile.name, getattr(position, "token", "unknown"),
                    filled=True, pnl_pct=pnl, reason=str(reason))
            except Exception:
                pass  # scent is best-effort, trading is not
            return out

        agent.exit_position = wrapped
    return field


if __name__ == "__main__":
    # smoke: requires waggled on :7777 (and optionally the oracle on :7778)
    fl = FieldLink()
    print("outcome:", bool(fl.trade_outcome("SCALPER", "SOL", filled=True, pnl_pct=4.2)))
    print("alloc:", fl.preset_allocation(["SNIPER", "WARRIOR", "SCALPER"]))
    print("verdict:", (fl.oracle_verdict("loom://strategy/SCALPER/SOL", -0.75, 0.1) or {}).get("result"))
    print("regime:", fl.regime_shift("loom://strategy/SCALPER/SOL"))
