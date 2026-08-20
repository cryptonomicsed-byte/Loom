"""Tests for Loom's Nostr wire channel (stdlib unittest).

Requires minipae on the path, since this module delegates every wire primitive
to it rather than reimplementing:

    PYTHONPATH=/path/to/minipae python3 -m unittest test_nostr_wire

Skipped rather than failed when minipae is absent, so a box with no Nostr
channel configured can still run Loom's suite.
"""
from __future__ import annotations

import json
import unittest

import nostr_wire

try:
    import minipae as m
    HAVE_MINIPAE = True
except ImportError:  # pragma: no cover
    m = None
    HAVE_MINIPAE = False


A_SIGNAL = {
    "symbol": "SOL/USDC",
    "direction": "long",
    "conviction": 0.78,
    "horizon": "6h",
    "ts": 1700000000,
    "meta": {"events_used": 42, "source": "transformer"},
}

SECKEY = bytes([0x33]) * 32


def _pubkey():
    return m.pubkey_from_secret(int.from_bytes(SECKEY, "big"))


class TestSignalRecord(unittest.TestCase):
    def test_record_is_flat_and_self_describing(self):
        rec = nostr_wire.signal_record(A_SIGNAL)
        for field in ("symbol", "direction", "conviction", "horizon",
                      "emitted_at", "meta"):
            self.assertIn(field, rec)
        self.assertEqual(rec["conviction"], 0.78)
        self.assertEqual(rec["emitted_at"], 1700000000)

    def test_meta_stored_as_json_text_is_parsed_back_to_structure(self):
        rec = nostr_wire.signal_record({**A_SIGNAL, "meta": json.dumps({"a": 1})})
        self.assertEqual(rec["meta"], {"a": 1})

    def test_unparseable_meta_is_preserved_rather_than_dropped(self):
        rec = nostr_wire.signal_record({**A_SIGNAL, "meta": "not json"})
        self.assertEqual(rec["meta"], {"raw": "not json"})

    def test_a_missing_conviction_does_not_crash_or_become_none(self):
        # Conviction is scored by Crucible, so it must always be a number.
        rec = nostr_wire.signal_record({"symbol": "X", "direction": "long"})
        self.assertEqual(rec["conviction"], 0.0)


@unittest.skipUnless(HAVE_MINIPAE, "minipae not on PYTHONPATH")
class TestSlugs(unittest.TestCase):
    def test_symbol_with_a_slash_still_yields_a_valid_slug(self):
        # "SOL/USDC" would otherwise inject a path separator and split into two
        # segments, silently changing the address shape.
        slug = nostr_wire.slug_signal("SOL/USDC", 1700000000)
        self.assertTrue(m.validate_slug(slug), slug)
        self.assertEqual(slug, "mem/loom/signal/sol-usdc/1700000000")

    def test_slug_is_keyed_by_time_so_calls_do_not_overwrite_each_other(self):
        # Engrams are addressable: a symbol-only slug would make each new call
        # replace the previous one, destroying the record that makes a claim
        # checkable afterwards.
        a = nostr_wire.slug_signal("SOL/USDC", 1700000000)
        b = nostr_wire.slug_signal("SOL/USDC", 1700000060)
        self.assertNotEqual(a, b)

    def test_outcome_slug_is_namespaced_to_loom(self):
        self.assertTrue(nostr_wire.slug_outcome("SOL/USDC").startswith("mem/loom/"))


@unittest.skipUnless(HAVE_MINIPAE, "minipae not on PYTHONPATH")
class TestEvents(unittest.TestCase):
    def test_engram_is_a_valid_signed_nip_ae_event(self):
        ev = nostr_wire.build_signal_engram(A_SIGNAL, SECKEY, _pubkey())
        self.assertEqual(ev["kind"], nostr_wire.KIND_AGENT_ENGRAM)
        self.assertEqual(ev["id"], m.event_id(ev))
        self.assertTrue(
            m.schnorr_verify(bytes.fromhex(ev["id"]), _pubkey(),
                             bytes.fromhex(ev["sig"]))
        )

    def test_engram_content_decrypts_back_to_the_signal(self):
        # The round trip that matters: another runtime holding the key must
        # recover the record, not just a blob.
        pub = _pubkey()
        ev = nostr_wire.build_signal_engram(A_SIGNAL, SECKEY, pub)
        kc = m.conversation_key(SECKEY, pub)
        recovered = json.loads(m.nip44_decrypt(ev["content"], kc))
        self.assertEqual(recovered["symbol"], "SOL/USDC")
        self.assertEqual(recovered["conviction"], 0.78)

    def test_the_traded_symbol_never_appears_in_the_clear(self):
        # The d tag is HMAC'd and the content encrypted, so a relay operator
        # cannot read which markets Loom is calling.
        ev = nostr_wire.build_signal_engram(A_SIGNAL, SECKEY, _pubkey())
        self.assertNotIn("SOL/USDC", json.dumps(ev))
        self.assertNotIn("sol-usdc", json.dumps(ev))

    def test_claim_is_a_valid_signed_crucible_event(self):
        ev = nostr_wire.build_signal_claim(A_SIGNAL, "sha256:abc", SECKEY)
        self.assertEqual(ev["kind"], nostr_wire.KIND_CLAIM)
        self.assertEqual(ev["id"], m.event_id(ev))
        self.assertTrue(
            m.schnorr_verify(bytes.fromhex(ev["id"]), _pubkey(),
                             bytes.fromhex(ev["sig"]))
        )

    def test_claim_carries_the_predictors_own_conviction(self):
        # Crucible scores against the stated confidence, so flattening it to a
        # default would erase the incentive not to overstate.
        ev = nostr_wire.build_signal_claim(A_SIGNAL, "sha256:abc", SECKEY)
        body = json.loads(ev["content"])
        self.assertEqual(body["conviction"], 0.78)
        self.assertEqual(body["falsifier"], "sha256:abc")

    def test_claim_half_life_defaults_to_six_hours(self):
        # A directional call on a liquid market is stale well before a day, and
        # Crucible decays belief in wall-clock time.
        ev = nostr_wire.build_signal_claim(A_SIGNAL, "sha256:abc", SECKEY)
        self.assertEqual(json.loads(ev["content"])["half_life_secs"], 6 * 3600)

    def test_a_claim_without_a_falsifier_is_refused(self):
        with self.assertRaises(ValueError):
            nostr_wire.build_signal_claim(A_SIGNAL, "", SECKEY)

    def test_build_signal_events_omits_the_claim_without_a_falsifier(self):
        pub = _pubkey()
        self.assertEqual(set(nostr_wire.build_signal_events(A_SIGNAL, SECKEY, pub)),
                         {"engram"})
        self.assertEqual(
            set(nostr_wire.build_signal_events(A_SIGNAL, SECKEY, pub,
                                               falsifier="sha256:abc")),
            {"engram", "claim"},
        )


if __name__ == "__main__":
    unittest.main()
