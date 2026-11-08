"""P1-06: /api/decide is entity-scoped.

Acceptance: an event matches only its own entity (symbol, raw or /USD);
a symbol with no events does not match; the mint is accepted but does not
change the match (Loom is symbol-level). Two tokens with different symbols
never share an entity match.
"""
from fabric import MarketEvent, entity_matches


def _ev(entity="AAA", symbol="AAA"):
    return MarketEvent(event_id="e", event_type="price_surge", entity=entity,
                       symbol=symbol, magnitude=0.5, confidence=0.5,
                       timestamp=1.0, source="test")


def test_entity_matches_its_own_symbol():
    assert entity_matches(_ev("AAA", "AAA"), "aaa")


def test_entity_matches_with_usd_suffix():
    assert entity_matches(_ev("AAA/USD", "AAA"), "AAA")


def test_entity_matches_raw_symbol_when_entity_differs():
    assert entity_matches(_ev("PAIR/X", "NEIL"), "neil")


def test_different_symbols_do_not_match():
    assert not entity_matches(_ev("AAA", "AAA"), "BBB")
    assert not entity_matches(_ev("AAA/USD", "AAA"), "BBB")


def test_empty_wanted_never_matches():
    assert not entity_matches(_ev("AAA", "AAA"), "")
    assert not entity_matches(_ev("AAA", "AAA"), None)
