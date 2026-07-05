"""
LOOM Vault — OKF v0.1 compliant knowledge bundle for whale intelligence.
Each whale, cluster, token, and pattern is an OKF concept document.
Wired directly into the 3D memory galaxy via the fabric event stream.

OKF Spec: https://github.com/GoogleCloudPlatform/knowledge-catalog/blob/main/okf/SPEC.md

Architecture:
  Whale Engine → OKF Writer → Obsidian Vault (markdown files)
                              ↓
                        3D Galaxy reads vault
                              ↓
                        Supermemory ingests for semantic search
"""

import os
import json
import time
import hashlib
from datetime import datetime, timezone
from pathlib import Path


# ── Vault Configuration ───────────────────────────────────────

VAULT_ROOT = os.path.expanduser("~/Documents/LOOM Vault")
VAULT_PATHS = {
    "whales": "whales",
    "clusters": "clusters",
    "tokens": "tokens",
    "patterns": "patterns",
    "events": "events",
    "correlations": "correlations",
}

SUPERMEMORY_URL = "http://2.25.70.156:3002"  # VPS Supermemory


def _ensure_dirs():
    """Create vault directory structure."""
    for name, path in VAULT_PATHS.items():
        full = os.path.join(VAULT_ROOT, path)
        os.makedirs(full, exist_ok=True)

    # Create bundle root index.md
    index_path = os.path.join(VAULT_ROOT, "index.md")
    if not os.path.exists(index_path):
        _write_file(index_path, f"""---
okf_version: "0.1"
bundle: loom-whale-intelligence
created: {_now_iso()}
---

# LOOM Whale Intelligence Bundle

Open Knowledge Format v0.1 bundle. Agent-native whale tracking intelligence.

## Directories

- **[[whales/]]** — Whale wallet dossiers (Tier 1-4, PV scored)
- **[[clusters/]]** — Coordinated whale groups and their attack patterns
- **[[tokens/]]** — Token profiles with whale activity
- **[[patterns/]]** — Precursor signature library
- **[[events/]]** — Significant market events with causal chains
- **[[correlations/]]** — Cross-entity correlation pairs
""")

    # Create log.md
    log_path = os.path.join(VAULT_ROOT, "log.md")
    if not os.path.exists(log_path):
        _write_file(log_path, f"""---
okf_version: "0.1"
bundle: loom-whale-intelligence
created: {_now_iso()}
---

# Activity Log

Chronological record of whale intelligence updates. Append-only.
""")


def _now_iso():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _write_file(path, content):
    with open(path, "w") as f:
        f.write(content)


def _append_log(entry):
    log_path = os.path.join(VAULT_ROOT, "log.md")
    ts = _now_iso()
    with open(log_path, "a") as f:
        f.write(f"\n## {ts}\n{entry}\n")


# ── OKF Concept Writers ───────────────────────────────────────

def write_whale_dossier(whale: dict):
    """Write OKF concept document for a whale wallet."""
    addr = whale.get("address", "unknown")
    short = whale.get("address_short", addr[:12])
    tier = whale.get("tier", 4)
    pv = whale.get("predictive_value", 0)
    wr = whale.get("win_rate", 0)
    label = whale.get("label", "unknown")
    cluster = whale.get("cluster_id", "")
    twitter = whale.get("twitter_handle", "")
    trades = whale.get("total_trades", 0)
    avg_r = whale.get("avg_return", 0)
    last_active = whale.get("last_active", 0)
    active_str = datetime.fromtimestamp(last_active).strftime("%Y-%m-%dT%H:%M:%SZ") if last_active else "unknown"

    # Tier label
    tier_labels = {1: "Elite", 2: "Strong", 3: "Good", 4: "Tracking"}
    tier_name = tier_labels.get(tier, "Unknown")

    # Cluster link
    cluster_link = f"[[../clusters/{cluster[:12]}.md|Cluster {cluster[:8]}]]" if cluster else "None"

    # Twitter link
    twitter_link = f"[@{twitter}](https://x.com/{twitter})" if twitter else "None"

    # Determine node_kind for galaxy rendering
    node_kind = "leader" if "leader" in str(label) else ("scout" if "scout" in str(label) else "amplifier" if "amplifier" in str(label) else "whale")

    frontmatter = f"""---
okf_version: "0.1"
bundle: loom-whale-intelligence
type: WhaleDossier
title: "{short} — {tier_name} Whale"
description: "Tier {tier} whale with predictive value {pv:.3f} and {wr:.0f}% win rate across {trades} trades."
tags: [whale, tier-{tier}, {node_kind}]
timestamp: {_now_iso()}
tier: {tier}
predictive_value: {pv}
win_rate: {wr}
total_trades: {trades}
average_return: {avg_r}
cluster_id: {cluster[:12] if cluster else ""}
twitter_handle: {twitter}
labels: {json.dumps([label])}
node_kind: {node_kind}
last_active: {active_str}
---"""

    body = f"""# {short}

**Tier {tier} — {tier_name}** | PV: {pv:.3f} | Win Rate: {wr:.0f}% | Trades: {trades}

## Profile

- **Role:** {label}
- **Average Return:** {avg_r:.1f}%
- **Cluster:** {cluster_link}
- **Twitter:** {twitter_link}
- **Last Active:** {active_str}

## Recent Activity

See [[../log.md|Activity Log]] for trade history.

## Relationships

- [[../clusters/|View Cluster]]
- [[../tokens/|Traded Tokens]]
- [[../correlations/|Correlated Entities]]
"""

    path = os.path.join(VAULT_ROOT, VAULT_PATHS["whales"], f"{short.replace('/', '_')}.md")
    _write_file(path, frontmatter + "\n" + body)
    return path


def write_cluster_profile(cluster: dict):
    """Write OKF concept for a whale cluster."""
    cid = cluster.get("id", "unknown")
    members = cluster.get("members", 0)
    leader = cluster.get("leader", "?")
    coordination = cluster.get("coordination", 0)
    threat = cluster.get("threat_level", "unknown")
    short_id = cid[:12]

    frontmatter = f"""---
okf_version: "0.1"
bundle: loom-whale-intelligence
type: WhaleCluster
title: "Cluster {short_id}"
description: "{members}-wallet cluster with {coordination:.2f} coordination score. Threat: {threat}."
tags: [cluster, {threat}-threat]
timestamp: {_now_iso()}
member_count: {members}
coordination_score: {coordination}
threat_level: {threat}
leader: {leader[:12] if leader else "unknown"}
node_kind: cluster
---"""

    body = f"""# Cluster {short_id}

**{members} wallets** | Coordination: {coordination:.2f} | Threat: {threat}

## Members

Leader: [[../whales/{leader[:12] if leader else 'unknown'}.md|{leader[:8] if leader else '?'}]]

See [[../whales/|Whale Directory]] for all member dossiers.

## Attack Signature

Coordination score of {coordination:.2f} indicates {"highly synchronized" if coordination > 0.7 else "moderately coordinated" if coordination > 0.4 else "loosely associated"} behavior.
"""

    path = os.path.join(VAULT_ROOT, VAULT_PATHS["clusters"], f"{short_id}.md")
    _write_file(path, frontmatter + "\n" + body)
    return path


def write_token_profile(token: dict):
    """Write OKF concept for a token."""
    addr = token.get("address", "unknown")
    symbol = token.get("symbol", addr[:8])
    whale_count = token.get("whale_count", 0)
    change = token.get("change_24h", 0)
    conviction = token.get("conviction", 0)
    volume = token.get("volume", 0)

    direction = "🟢 Bullish" if change > 5 else ("🔴 Bearish" if change < -5 else "🟡 Neutral")

    frontmatter = f"""---
okf_version: "0.1"
bundle: loom-whale-intelligence
type: TokenProfile
title: "{symbol}"
description: "Token tracked by {whale_count} whales. {direction} ({change:+.1f}% 24h)."
tags: [token, solana, memecoin]
timestamp: {_now_iso()}
symbol: {symbol}
whale_count: {whale_count}
change_24h: {change}
conviction: {conviction}
volume_24h: {volume}
node_kind: token
---"""

    body = f"""# {symbol}

**{whale_count} whales tracking** | {direction} | Conviction: {conviction:.1f}/10

## Market Data

- 24h Change: {change:+.1f}%
- Volume: ${volume:,.0f}
- Address: `{addr}`

## Tracked Whales

See [[../whales/|Whale Directory]] for wallets trading this token.
"""

    path = os.path.join(VAULT_ROOT, VAULT_PATHS["tokens"], f"{symbol}.md")
    _write_file(path, frontmatter + "\n" + body)
    return path


def write_pattern_signature(pattern: dict):
    """Write OKF concept for a detected pump/accumulation pattern."""
    pid = pattern.get("pattern_id", hashlib.md5(str(time.time()).encode()).hexdigest()[:12])
    signature = pattern.get("signature", "unknown")
    occurrences = pattern.get("occurrences", 1)
    first_seen = pattern.get("first_seen", _now_iso())

    frontmatter = f"""---
okf_version: "0.1"
bundle: loom-whale-intelligence
type: PrecursorSignature
title: "Pattern {pid}"
description: "Precursor signature detected {occurrences} times: {signature}"
tags: [pattern, precursor, signal]
timestamp: {_now_iso()}
pattern_id: {pid}
occurrences: {occurrences}
first_seen: {first_seen}
node_kind: knowledge
---"""

    body = f"""# Pattern {pid}

**{occurrences} occurrences** | First seen: {first_seen}

## Signature

{signature}

## Predictive Value

This pattern has been observed {occurrences} times. Historical accuracy data is accumulating.
"""

    path = os.path.join(VAULT_ROOT, VAULT_PATHS["patterns"], f"{pid}.md")
    _write_file(path, frontmatter + "\n" + body)
    return path


def write_hot_file(active_whales: list, recent_events: list):
    """Write hot.md — current session state. Overwritten each tick."""
    ts = _now_iso()
    lines = [f"""---
okf_version: "0.1"
bundle: loom-whale-intelligence
type: SessionState
title: "LOOM Active Session"
timestamp: {ts}
---

# Active Session — {ts}

## Active Whales ({len(active_whales)})
"""]

    for w in active_whales[:10]:
        lines.append(f"- [[whales/{w.get('address_short', w.get('address','?')[:12]).replace('/', '_')}.md|{w.get('label', 'whale')}]] — Tier {w.get('tier', '?')} — PV {w.get('predictive_value', 0):.3f}")

    lines.append(f"\n## Recent Events ({len(recent_events)})")
    for e in recent_events[-10:]:
        lines.append(f"- {e.get('t', '?')} — {e.get('e', '?')} — mag {e.get('m', 0):.2f}")

    path = os.path.join(VAULT_ROOT, "hot.md")
    _write_file(path, "\n".join(lines))


# ── Supermemory Sync ──────────────────────────────────────────

def sync_to_supermemory(whale: dict):
    """Push whale dossier to Supermemory vector store (fire-and-forget)."""
    def _do_sync():
        try:
            import urllib.request
            addr = whale.get("address", "")
            short = whale.get("address_short", addr[:12])
            tier = whale.get("tier", 4)
            label = whale.get("label", "")

            content = (
                f"Whale wallet {short}. Tier {tier}. "
                f"Role: {label}. "
                f"Predictive value: {whale.get('predictive_value', 0):.3f}. "
                f"Win rate: {whale.get('win_rate', 0):.0f}%. "
                f"Total trades: {whale.get('total_trades', 0)}. "
                f"Average return: {whale.get('avg_return', 0):.1f}%."
            )

            body = json.dumps({
                "content": content,
                "containerTag": f"whale:tier{tier}",
                "metadata": whale,
                "customId": f"loom:whale:{addr[:16]}",
            }).encode()

            req = urllib.request.Request(
                f"{SUPERMEMORY_URL}/v3/documents",
                data=body,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            urllib.request.urlopen(req, timeout=3)
        except Exception:
            pass

    import threading
    threading.Thread(target=_do_sync, daemon=True).start()


# ── Full Sync ─────────────────────────────────────────────────

def full_sync(whales_engine, twitter, fabric):
    """Sync all whale data to the vault."""
    _ensure_dirs()

    count = 0

    # Sync whales
    for addr, w in whales_engine.wallets.items():
        if w.total_trades >= 1:
            dossier = w.to_dict()
            dossier["address_short"] = addr[:8] + "..." + addr[-4:]
            dossier["label"] = w.labels[0] if w.labels else "unknown"
            dossier["cluster_id"] = w.cluster_id or ""
            dossier["twitter_handle"] = w.twitter_handle
            dossier["last_active"] = w.last_active
            write_whale_dossier(dossier)
            sync_to_supermemory(dossier)
            count += 1

    # Sync clusters
    for cid, c in whales_engine.clusters.items():
        write_cluster_profile(c.to_dict())

    # Sync tokens
    for token_addr, holders in whales_engine.token_index.items():
        if holders:
            write_token_profile({
                "address": token_addr,
                "symbol": "?" if len(token_addr) > 10 else token_addr,
                "whale_count": len(holders),
            })

    # Sync hot file
    active = whales_engine.get_active_whales(max_age_sec=3600)
    recent = list(fabric.causal.recent)[-20:]
    write_hot_file(
        [{"address_short": w["addr"], "label": w.get("label", "?"), "tier": w["tier"], "predictive_value": w["pv"]}
         for w in active],
        [e.to_compact() for e in recent],
    )

    _append_log(f"Synced {count} whale dossiers, {len(whales_engine.clusters)} clusters, {len(whales_engine.token_index)} tokens")

    print(f"  [vault] synced {count} whales to {VAULT_ROOT}")
    return count


# ── Galaxy Integration ────────────────────────────────────────

def build_galaxy_from_vault() -> dict:
    """Build 3D galaxy data directly from the OKF vault.
    This is what the Three.js galaxy visualizer consumes."""
    nodes = []
    links = []

    # Center node
    nodes.append({"id": "loom:center", "name": "LOOM", "val": 20, "color": "#ffffff", "kind": "agent"})

    # Scan whale dossiers
    whale_dir = os.path.join(VAULT_ROOT, VAULT_PATHS["whales"])
    cluster_dir = os.path.join(VAULT_ROOT, VAULT_PATHS["clusters"])
    token_dir = os.path.join(VAULT_ROOT, VAULT_PATHS["tokens"])

    clusters_seen = set()

    if os.path.exists(whale_dir):
        for fname in sorted(os.listdir(whale_dir)):
            if not fname.endswith(".md"):
                continue
            path = os.path.join(whale_dir, fname)
            try:
                fm = _parse_frontmatter(path)
                node = {
                    "id": f"whale:{fname}",
                    "name": fm.get("title", fname.replace(".md", "")),
                    "val": 4 + fm.get("predictive_value", 0) * 10,
                    "color": _tier_color(fm.get("tier", 4)),
                    "kind": fm.get("node_kind", "whale"),
                    "tier": fm.get("tier", 4),
                    "pv": fm.get("predictive_value", 0),
                    "wr": fm.get("win_rate", 0),
                    "twitter": fm.get("twitter_handle", ""),
                    "cluster_id": fm.get("cluster_id", ""),
                }
                nodes.append(node)
                links.append({"source": "loom:center", "target": node["id"], "color": node["color"], "width": 0.3 + fm.get("predictive_value", 0) * 0.5})

                # Link to cluster
                cid = fm.get("cluster_id", "")
                if cid:
                    cnode_id = f"cluster:{cid}"
                    if cnode_id not in clusters_seen:
                        clusters_seen.add(cnode_id)
                    links.append({"source": cnode_id, "target": node["id"], "color": "#06b6d4", "width": 0.4})
            except Exception:
                pass

    # Cluster hubs
    for cnode_id in clusters_seen:
        nodes.append({"id": cnode_id, "name": cnode_id.replace("cluster:", "Cluster "), "val": 8, "color": "#06b6d4", "kind": "cluster"})
        links.append({"source": "loom:center", "target": cnode_id, "color": "#06b6d4", "width": 1.0})

    # Token nodes
    if os.path.exists(token_dir):
        for fname in sorted(os.listdir(token_dir)):
            if not fname.endswith(".md"):
                continue
            try:
                fm = _parse_frontmatter(os.path.join(token_dir, fname))
                node = {
                    "id": f"token:{fname}",
                    "name": fm.get("title", "?"),
                    "val": 3 + min(8, fm.get("whale_count", 0) * 0.5),
                    "color": "#10b981",
                    "kind": "token",
                    "change_24h": fm.get("change_24h", 0),
                    "conviction": fm.get("conviction", 0),
                    "volume": fm.get("volume_24h", 0),
                }
                nodes.append(node)
                links.append({"source": "loom:center", "target": node["id"], "color": "#10b981", "width": 0.2})
            except Exception:
                pass

    return {"nodes": nodes, "links": links}


def _parse_frontmatter(path: str) -> dict:
    """Parse YAML frontmatter from a markdown file."""
    with open(path) as f:
        content = f.read()

    if not content.startswith("---"):
        return {}

    parts = content.split("---", 2)
    if len(parts) < 3:
        return {}

    try:
        import yaml
        return yaml.safe_load(parts[1]) or {}
    except Exception:
        # Fallback: basic key:value parsing
        result = {}
        for line in parts[1].strip().split("\n"):
            if ":" in line:
                k, v = line.split(":", 1)
                result[k.strip()] = v.strip()
        return result


def _tier_color(tier: int) -> str:
    return {1: "#f43f5e", 2: "#f59e0b", 3: "#8b5cf6", 4: "#64748b"}.get(tier, "#64748b")


# ── Init ──────────────────────────────────────────────────────

_ensure_dirs()
print(f"[vault] initialized at {VAULT_ROOT}")
