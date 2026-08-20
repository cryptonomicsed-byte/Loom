"""nostr_wire — putting Loom's signals on the ecosystem's Nostr wire.

`vantage_predictor.publish_signal` pushes a signal to one HTTP endpoint. That
makes it actionable by Vantage and invisible to everyone else: no other agent
can see what Loom predicted, and nothing records whether the prediction was any
good. A weaver whose signals only one consumer can read is not weaving.

A signal travels two ways here, and the second is the point:

  * as a **NIP-AE engram** (`kind:30174`) — the record that Loom emitted this
    signal at this time, addressable and encrypted to its owner;
  * as a **Crucible claim** (`kind:47001`) — the assertion that the prediction
    is right, which anyone can check later.

A directional call with a conviction score is the cleanest Crucible fit in this
ecosystem. Crucible prices confidence — being wrong at 0.99 costs far more than
being wrong at 0.55 — so a predictor that inflates conviction is penalised more
than one that hedges honestly, and it earns reliability *per domain*, so being
good at one market buys no authority in another. Claims also decay: a call has
a half-life, and one nobody rechecks becomes `Decayed` rather than staying
`Supported` forever.

## This module does not reimplement the wire contract

`minipae.py` is the Python implementation for this ecosystem — BIP-340, NIP-44,
canonical NIP-01 serialization, the engram `d`-tag HMAC, slug grammar, and the
adapter kit (`build_slug`, `sign_event`). There is one per language on purpose:
the contract's failure mode is silent divergence, so each extra copy is another
chance to diverge in a way only a *different* implementation can detect.

This module supplies only what is Loom's: how a signal maps onto an engram body
and a claim, and the `mem/loom/` namespace registered in minipae's
`NAMESPACES.md`.

    export PYTHONPATH=/path/to/minipae:$PYTHONPATH
"""
from __future__ import annotations

import json
from typing import Any, Dict, Optional

_MINIPAE_HINT = (
    "minipae is required for the Nostr wire channel. It is the Python "
    "implementation of the ecosystem wire contract; this module deliberately "
    "does not reimplement it. Put it on PYTHONPATH: "
    "export PYTHONPATH=/path/to/minipae:$PYTHONPATH"
)

try:  # pragma: no cover - trivial import guard
    import minipae as _m
except ImportError:  # pragma: no cover
    _m = None


def _minipae():
    """Return the minipae module, or raise with an actionable message.

    Deferred rather than raised at import time so importing this module stays
    cheap and the rest of Loom keeps working on a box with no Nostr channel.
    """
    if _m is None:
        raise RuntimeError(_MINIPAE_HINT)
    return _m


# Owned elsewhere -- minipae for 30174, crucible-core for 47001. Mirrored here
# for readability at the call site; changing one in isolation would break
# interoperability silently.
KIND_AGENT_ENGRAM = 30174
KIND_CLAIM = 47001

#: Namespace segment, registered in minipae's NAMESPACES.md before first write.
NAMESPACE = "loom"

#: Default half-life for a signal claim. Six hours: a directional call on a
#: liquid market is stale well before a day, and Crucible decays belief in
#: wall-clock time rather than waiting to be told the call expired.
DEFAULT_HALF_LIFE_SECS = 6 * 3600


def slug_signal(symbol: str, ts: Any) -> str:
    """Engram slug for one signal, keyed by symbol and emission time.

    Keyed by time as well as symbol because engrams are addressable and a
    symbol-only slug would make each new call silently replace the last -- the
    record of what Loom predicted an hour ago is exactly what makes the claim
    checkable afterwards.
    """
    return _minipae().build_slug(NAMESPACE, "signal", str(symbol), str(ts))


def slug_outcome(symbol: str) -> str:
    """Engram slug for the running outcome record of one symbol."""
    return _minipae().build_slug(NAMESPACE, "outcome", str(symbol))


def signal_record(signal: Dict[str, Any]) -> Dict[str, Any]:
    """The wire body for a signal.

    Flat and self-describing so a Rust, Julia or TypeScript reader can
    interpret it without importing Loom.
    """
    meta = signal.get("meta") or {}
    if isinstance(meta, str):
        try:
            meta = json.loads(meta)
        except (json.JSONDecodeError, TypeError):
            meta = {"raw": meta}

    return {
        "symbol": str(signal.get("symbol", "")),
        "direction": str(signal.get("direction", "")),
        "conviction": float(signal.get("conviction", 0.0) or 0.0),
        "horizon": signal.get("horizon"),
        "emitted_at": signal.get("ts") or signal.get("emitted_at"),
        "meta": meta,
    }


def build_signal_engram(
    signal: Dict[str, Any],
    seckey: bytes,
    owner_pubkey: bytes,
) -> Dict[str, Any]:
    """Build a signed NIP-AE engram recording a signal.

    Content is NIP-44 encrypted and the slug HMAC'd into the ``d`` tag, both by
    ``minipae.build_event`` -- a relay operator learns Loom wrote something
    without learning which symbol it is trading.

    The body goes in as a dict: minipae does its own canonical encoding before
    encrypting, and handing it a string would nest one JSON document in another.
    """
    record = signal_record(signal)
    return _minipae().build_event(
        slug_signal(record["symbol"] or "unknown", record["emitted_at"] or 0),
        record,
        seckey,
        owner_pubkey,
    )


def build_signal_claim(
    signal: Dict[str, Any],
    falsifier: str,
    seckey: bytes,
    half_life_secs: int = DEFAULT_HALF_LIFE_SECS,
) -> Dict[str, Any]:
    """Build a signed Crucible claim asserting a signal will prove correct.

    Crucible's one rule: an assertion must say how it could be proven wrong.
    ``falsifier`` is the content address of the WASM predicate that checks the
    call against realised price. Crucible rejects a claim without one at parse
    time, so this refuses rather than emitting something that will bounce.

    The predictor's own conviction rides on the claim rather than being
    flattened to a default, because Crucible scores against it: overstating
    costs more than hedging.
    """
    if not falsifier:
        raise ValueError(
            "a Crucible claim requires a falsifier; Crucible rejects claims without one"
        )

    record = signal_record(signal)
    content = json.dumps(
        {
            "statement": f"{record['symbol']} {record['direction']}",
            "falsifier": falsifier,
            "symbol": record["symbol"],
            "direction": record["direction"],
            "conviction": record["conviction"],
            "emitted_at": record["emitted_at"],
            "half_life_secs": half_life_secs,
        },
        separators=(",", ":"),
        # The id is hashed over this content. Python's default escaping would
        # produce an id no other implementation agrees with.
        ensure_ascii=False,
    )

    m = _minipae()
    tags = [
        ["falsifier", falsifier],
        ["symbol", m.normalize_slug_segment(record["symbol"] or "unknown")],
        ["half_life", str(half_life_secs)],
    ]
    return m.sign_event(KIND_CLAIM, content, tags, seckey)


def build_signal_events(
    signal: Dict[str, Any],
    seckey: bytes,
    owner_pubkey: bytes,
    falsifier: Optional[str] = None,
) -> Dict[str, Dict[str, Any]]:
    """Build the signed events for a signal without contacting a relay.

    Returned as ``{"engram": ..., "claim": ...}``, claim present only when a
    falsifier is supplied. Separated from publishing so a caller can inspect or
    batch, and so this is testable with no network.
    """
    events = {"engram": build_signal_engram(signal, seckey, owner_pubkey)}
    if falsifier:
        events["claim"] = build_signal_claim(signal, falsifier, seckey)
    return events


async def publish_signal(
    signal: Dict[str, Any],
    seckey: bytes,
    owner_pubkey: bytes,
    relay: str,
    falsifier: Optional[str] = None,
    authenticated: bool = True,
) -> Dict[str, Any]:
    """Publish a signal to ``relay`` as an engram, and as a claim if falsifiable.

    Async because ``minipae.publish`` is -- it speaks websockets.

    Returns what the relay actually said, per event, unmodified. A rejection
    stays a rejection: minipae's own history includes a bug where an
    ``auth-required`` refusal read as ``ok: True``, and a signal that silently
    failed to publish is indistinguishable from one never made -- which is the
    difference between a track record and an empty one.

    ``authenticated`` uses NIP-42, which the production Buzz relay requires for
    writes.
    """
    m = _minipae()
    events = build_signal_events(signal, seckey, owner_pubkey, falsifier)

    results: Dict[str, Any] = {}
    for name, ev in events.items():
        if authenticated:
            results[name] = await m.publish_authenticated(relay, ev, seckey)
        else:
            results[name] = await m.publish(relay, ev)
    return results
