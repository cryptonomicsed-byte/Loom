#!/usr/bin/env python3
"""Conformance test for LOOM's GlyphIndex memory leg.

Pins the frozen cross-language fold/Odù vectors (OSOVM/GLYPHINDEX_SPEC.md §6)
against glyph_memory.py's primitives, and checks that the Julia fractal-pass
handoff (export_nodes -> plan -> apply_plan) preserves the fold contract:
every macro-glyph is itself a valid content-addressed glyph.

Run:  python3 test_glyph_conformance.py
"""
import json
import os
import tempfile

import glyph_memory as gm

# Frozen cross-language vectors — the same table asserted by Vantage (Python
# canonical reference), larql-glyph (Rust), mnemopi (TS), Zero, and the
# polyglot conformance kit (Go/Elixir/Julia/Clojure/Move).
FROZEN = [
    ("Àṣẹ", 21841, 227, 58152),
    ("hello", 23636, 44, 11506),
    ("GlyphIndex", 13726, 68, 17595),
    ("😊🚀 Unicode test", 64591, 189, 48626),
    ("Ọ̀rúnmìlà", 17963, 204, 52390),
]


def test_frozen_fold_and_odu_vectors():
    for text, codepoint, base, composed in FROZEN:
        digest = gm.content_hash(text)
        assert digest.hex() == gm.content_hash(text).hex()
        assert ord(gm.glyph_fold(digest)) == codepoint, f"fold {text}"
        assert gm.odu_link(digest) == (base, composed), f"odu {text}"


def test_fold_total_matches_spec():
    assert gm._FOLD_TOTAL == 63422, "GIX-FOLD-v1 target space is 63,422 BMP scalars"


def test_remember_uses_content_address():
    mem = gm.GlyphMemory("loom-conformance")
    node = mem.remember("hello", importance=0.9, ts=1.0)
    digest = gm.content_hash("hello")
    assert node["id"] == digest.hex()
    assert node["glyph_codepoint"] == 23636
    assert node["odu_base"] == 44 and node["odu_composed"] == 11506


def test_fractal_handoff_preserves_fold_contract():
    """export -> plan -> apply must yield a macro-glyph whose id is a real
    content hash and whose codepoint/Odù re-derive from that id."""
    mem = gm.GlyphMemory("loom-conformance")
    ids = [mem.remember(f"dust tx noise {i}", importance=0.1, ts=float(i))["id"] for i in range(3)]
    mem.remember("Whale 0xAlpha rotated 2M USDC into SOL", importance=0.9)

    with tempfile.TemporaryDirectory() as d:
        nodes_path = os.path.join(d, "glyph_nodes.json")
        plan_path = os.path.join(d, "glyph_plan.json")
        mem.export_nodes(nodes_path)
        # The exported handoff carries the fold metadata the Julia pass reads.
        exported = json.load(open(nodes_path))
        assert {"odu_base", "glyph_codepoint", "id"} <= set(exported["nodes"][0])

        with open(plan_path, "w") as f:
            json.dump({"folds": [{"ids": ids}], "prune_ids": []}, f)
        created, pruned = mem.apply_plan(plan_path)
        assert (created, pruned) == (1, 0)

    macro = next(n for n in mem.nodes.values() if n["macro_of"])
    # Deterministic macro address = fold of "gix-macro:" + sorted member ids.
    digest = gm.content_hash("gix-macro:" + ",".join(sorted(ids)))
    assert macro["id"] == digest.hex()
    assert macro["glyph_codepoint"] == ord(gm.glyph_fold(digest))
    assert macro["odu_base"], "macro carries an Odù lineage"


def _run():
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in tests:
        fn()
    print(f"glyph_memory.py conformance ok ({len(tests)} checks)")


if __name__ == "__main__":
    _run()
