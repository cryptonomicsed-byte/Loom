#!/usr/bin/env python3
"""
LOOM GlyphIndex memory — sovereign, content-addressed agent memory.

Implements the keyless core of the ecosystem GlyphIndex contract
(spec: OSOVM/GLYPHINDEX_SPEC.md; canonical reference implementation:
Vantage/backend/glyph_index.py) with zero dependencies, matching LOOM's
flat-file style. Sealing/keys live in the identity layer (BIPỌ̀N39 /
Cloakseed); LOOM keeps the queryable projection: glyph identities,
embeddings, and the JSON handoff to the Julia fractal pass.

Python ↔ Julia exchange (same pattern as julia_daemon.jl / julia_api.py):
    export_nodes()  → glyph_nodes.json   (read by glyph_fractal.jl)
    apply_plan()    ← glyph_plan.json    (written by glyph_fractal.jl)
"""

import hashlib
import json
import math
import time

NODES_FILE = "/opt/loom/glyph_nodes.json"
PLAN_FILE = "/opt/loom/glyph_plan.json"

# GIX-FOLD-v1 ranges — identical in every ecosystem repo.
_FOLD_RANGES = ((0x0020, 0xD7FF - 0x0020 + 1),
                (0xE000, 0xFDCF - 0xE000 + 1),
                (0xFDF0, 0xFFFD - 0xFDF0 + 1))
_FOLD_TOTAL = sum(count for _, count in _FOLD_RANGES)


def content_hash(text: str) -> bytes:
    return hashlib.sha256(text.encode("utf-8")).digest()


def glyph_fold(digest: bytes) -> str:
    """GIX-FOLD-v1: 32-byte digest → one valid BMP glyph (display alias)."""
    if len(digest) != 32:
        raise ValueError("glyph_fold requires a 32-byte digest")
    idx = int.from_bytes(digest, "big") % _FOLD_TOTAL
    for start, count in _FOLD_RANGES:
        if idx < count:
            return chr(start + idx)
        idx -= count
    raise AssertionError("unreachable")


def odu_link(digest: bytes):
    return digest[0], (digest[0] << 8) | digest[1]


def embed(text: str, dim: int = 256):
    """Deterministic hashing 3-gram embedder (stdlib-only, L2-normalized)."""
    vec = [0.0] * dim
    lowered = text.lower()
    for i in range(max(len(lowered) - 2, 1)):
        gram = lowered[i:i + 3]
        h = hashlib.sha256(gram.encode("utf-8")).digest()
        vec[(h[0] << 8 | h[1]) % dim] += 1.0
    norm = math.sqrt(sum(v * v for v in vec)) or 1.0
    return [v / norm for v in vec]


def cosine(a, b):
    return sum(x * y for x, y in zip(a, b))


class GlyphMemory:
    """In-process glyph memory for a LOOM agent (narrative, whale intel,
    trading notes). Stores metadata + embeddings only — plaintext chunks stay
    with the caller, sealed blobs stay with the identity layer."""

    def __init__(self, owner: str):
        self.owner = owner
        self.nodes = {}  # canonical_id -> node dict

    def remember(self, chunk: str, importance: float = 0.5, ts: float = None):
        digest = content_hash(chunk)
        cid = digest.hex()
        base, composed = odu_link(digest)
        node = {
            "id": cid,
            "glyph_codepoint": ord(glyph_fold(digest)),
            "odu_base": base,
            "odu_composed": composed,
            "created_at": ts if ts is not None else time.time(),
            "importance": importance,
            "embedding": embed(chunk),
            "macro_of": [],
        }
        self.nodes[cid] = node
        return node

    def search(self, query: str, k: int = 3):
        qvec = embed(query)
        scored = sorted(
            ((cosine(qvec, n["embedding"]), n["created_at"], n)
             for n in self.nodes.values()),
            key=lambda s: (-s[0], -s[1]))
        return [n for _, _, n in scored[:k]]

    # ---- Julia fractal-pass handoff -----------------------------------
    def export_nodes(self, path: str = NODES_FILE):
        with open(path, "w") as f:
            json.dump({"owner": self.owner,
                       "nodes": list(self.nodes.values())}, f)
        return path

    def apply_plan(self, path: str = PLAN_FILE):
        """Apply a glyph_fractal.jl plan: fold clusters into macro-glyphs
        (deterministic id: fold of sorted member ids), drop pruned nodes.
        Returns (macros_created, pruned)."""
        with open(path) as f:
            plan = json.load(f)
        created = 0
        for fold in plan.get("folds", []):
            ids = [i for i in fold["ids"] if i in self.nodes]
            if not ids:
                continue
            members = [self.nodes[i] for i in ids]
            digest = content_hash("gix-macro:" + ",".join(sorted(ids)))
            cid = digest.hex()
            base, composed = odu_link(digest)
            dim = len(members[0]["embedding"])
            centroid = [sum(m["embedding"][d] for m in members) / len(members)
                        for d in range(dim)]
            norm = math.sqrt(sum(v * v for v in centroid)) or 1.0
            macro = {
                "id": cid,
                "glyph_codepoint": ord(glyph_fold(digest)),
                "odu_base": base,
                "odu_composed": composed,
                "created_at": max(m["created_at"] for m in members),
                "importance": max(m["importance"] for m in members),
                "embedding": [v / norm for v in centroid],
                "macro_of": sorted(ids),
            }
            for i in ids:
                del self.nodes[i]
            self.nodes[cid] = macro
            created += 1
        pruned = 0
        for i in plan.get("prune_ids", []):
            if i in self.nodes:
                del self.nodes[i]
                pruned += 1
        return created, pruned


if __name__ == "__main__":
    # Smoke test (no /opt/loom needed): remember → search → export/apply.
    import tempfile, os

    mem = GlyphMemory("loom-agent")
    mem.remember("Whale 0xAlpha rotated 2M USDC into SOL", importance=0.9)
    mem.remember("Narrative: AI agents meta heating up on CT", importance=0.7)
    for i in range(3):
        mem.remember(f"dust tx noise {i}", importance=0.1, ts=float(i))

    top = mem.search("whale rotation into solana", k=1)
    assert "id" in top[0] and top[0]["importance"] == 0.9, top[0]

    with tempfile.TemporaryDirectory() as d:
        nodes_path = os.path.join(d, "glyph_nodes.json")
        plan_path = os.path.join(d, "glyph_plan.json")
        mem.export_nodes(nodes_path)
        noise_ids = sorted(n["id"] for n in mem.nodes.values()
                           if n["importance"] <= 0.35)
        with open(plan_path, "w") as f:
            json.dump({"folds": [{"ids": noise_ids}], "prune_ids": []}, f)
        created, pruned = mem.apply_plan(plan_path)
        assert (created, pruned) == (1, 0)
        assert len(mem.nodes) == 3  # 2 keepers + 1 macro
    print("glyph_memory.py smoke test OK —", len(mem.nodes), "nodes")
