#!/usr/bin/env python3
"""
LOOM Fast Server — HTTP server starts first, daemons follow.
Serves galaxy, stream, and all API endpoints without blocking.
"""

import json
import time
import threading
import os
import sys
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

PORT = 8889

# Load galaxy HTML from static file (fresh, no pyc issues)
GALAXY_PATH = "/data/data/com.termux/files/home/loom/galaxy.html"
try:
    with open(GALAXY_PATH) as f:
        GALAXY_HTML = f.read()
except Exception:
    GALAXY_HTML = "<h1>Galaxy not found</h1>"

# Simple stream HTML
STREAM_HTML = r"""<!DOCTYPE html>
<html><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>LOOM Stream</title>
<style>
:root{--bg:#06060A;--surface:#0E0E14;--text:#E2E8F0;--muted:#64748B;--accent:#8B5CF6;--profit:#10B981;--loss:#EF4444}
*{margin:0;padding:0;box-sizing:border-box}
body{background:var(--bg);color:var(--text);font-family:-apple-system,BlinkMacSystemFont,sans-serif;font-size:13px;padding:8px 12px 60px}
.header{position:sticky;top:0;background:var(--bg);padding:8px 0;border-bottom:1px solid #1E1E2E;display:flex;justify-content:space-between;align-items:center;z-index:10}
.header h1{font-size:16px;font-weight:800;color:var(--accent)}
.status{font-size:10px;color:var(--muted)}
.card{background:var(--surface);border:1px solid #1E1E2E;border-radius:10px;padding:10px 12px;margin:6px 0;animation:fadeIn .3s ease}
@keyframes fadeIn{from{opacity:0;transform:translateY(8px)}to{opacity:1;transform:translateY(0)}}
.card .title{font-weight:700;margin-bottom:4px}
.card .meta{font-size:10px;color:var(--muted)}
.bottom{position:fixed;bottom:0;left:0;right:0;background:rgba(6,6,10,.9);border-top:1px solid #1E1E2E;display:flex;padding:6px}
.bottom a{flex:1;text-align:center;font-size:10px;color:var(--muted);text-decoration:none;padding:8px}
.bottom a.active{color:var(--accent)}
</style></head>
<body>
<div class="header"><h1>LOOM Stream</h1><div class="status" id="status">connecting...</div></div>
<div id="feed"><div style="text-align:center;padding:40px;color:var(--muted)">◎ Waiting for market events...</div></div>
<div class="bottom">
  <a href="/galaxy">Galaxy</a>
  <a href="/" class="active">Stream</a>
  <a href="/api/health" target="_blank">Health</a>
</div>
<script>
const ws = new WebSocket(`ws://${location.host}/ws`);
ws.onopen = () => document.getElementById('status').innerHTML = '<span style="color:var(--profit)">● live</span>';
ws.onclose = () => document.getElementById('status').innerHTML = '<span style="color:var(--loss)">● offline</span>';
ws.onmessage = (m) => {
  try {
    const d = JSON.parse(m.data);
    if (d.type === 'ping') return;
    const ev = d.event || d;
    const el = document.getElementById('feed');
    if (el.children.length === 1 && el.children[0].textContent.includes('Waiting')) el.innerHTML = '';
    const card = document.createElement('div');
    card.className = 'card';
    card.innerHTML = `<div class="title">${ev.t||'event'} — ${ev.e||'?'}</div><div class="meta">mag ${(ev.m||0).toFixed(2)} · conf ${(ev.c||0).toFixed(2)} · ${ev.src||'?'}</div>`;
    el.insertBefore(card, el.firstChild);
    while (el.children.length > 50) el.lastChild.remove();
  } catch(e) {}
};
</script>
</body></html>"""


class FastHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        path = urlparse(self.path).path

        if path == "/" or path == "/index.html":
            self._html(STREAM_HTML)
        elif path == "/galaxy":
            self._html(GALAXY_HTML)
        elif path == "/api/health":
            self._json({"status": "ok", "uptime": int(time.time() - _start_time)})
        elif path == "/api/state":
            self._json(_cached_state)
        elif path == "/api/whales/leaderboard":
            self._json(_cached_leaderboard)
        elif path == "/api/decide":
            self._json({"consensus": {"direction": "WAIT", "conviction": 0, "debate": False}})
        elif path == "/api/brief":
            self._json(_cached_brief)
        elif path == "/api/whales/galaxy":
            self._json(_cached_galaxy)
        elif path == "/ws":
            self._ws_upgrade()
        else:
            self._json({"error": "not found"}, 404)

    def _html(self, content):
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write(content.encode())

    def _json(self, data, status=200):
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(json.dumps(data, default=str).encode())

    def _ws_upgrade(self):
        import hashlib, base64, struct
        key = self.headers.get("Sec-WebSocket-Key", "")
        if not key:
            self.send_response(400); self.end_headers(); return
        accept = base64.b64encode(hashlib.sha1((key + "258EAFA5-E914-47DA-95CA-C5AB0DC85B11").encode()).digest()).decode()
        self.send_response(101)
        self.send_header("Upgrade", "websocket")
        self.send_header("Connection", "Upgrade")
        self.send_header("Sec-WebSocket-Accept", accept)
        self.end_headers()
        import queue
        q = queue.Queue()
        _ws_clients.append(q)
        try:
            while True:
                try:
                    msg = q.get(timeout=5)
                    payload = msg.encode()
                    frame = bytearray([0x81])
                    n = len(payload)
                    if n < 126: frame.append(n)
                    elif n < 65536: frame.extend([126, (n>>8)&255, n&255])
                    self.wfile.write(bytes(frame) + payload)
                    self.wfile.flush()
                except queue.Empty:
                    self.wfile.write(b'\x81\x0F{"type":"ping"}')
                    self.wfile.flush()
        except Exception:
            pass
        finally:
            if q in _ws_clients:
                _ws_clients.remove(q)

    def log_message(self, *a): pass


# Global state
_start_time = time.time()
_cached_state = {"event_count": 0, "recent_events": [], "anomalies": []}
_cached_leaderboard = []
_cached_brief = {}
_narrative_brief = None
_last_wl_sync = 0.0
_cached_galaxy = {
    "nodes": [
        {"id": "loom:center", "name": "LOOM", "val": 20, "color": "#ffffff", "kind": "agent"},
        {"id": "whale:1", "name": "Whale Alpha", "val": 8, "color": "#f43f5e", "kind": "whale", "tier": 1},
        {"id": "whale:2", "name": "Whale Beta", "val": 6, "color": "#f59e0b", "kind": "whale", "tier": 2},
        {"id": "token:bonk", "name": "BONK", "val": 4, "color": "#10b981", "kind": "token"},
        {"id": "token:popcat", "name": "POPCAT", "val": 3, "color": "#10b981", "kind": "token"},
    ],
    "links": [
        {"source": "loom:center", "target": "whale:1", "color": "#f43f5e", "width": 0.8},
        {"source": "loom:center", "target": "whale:2", "color": "#f59e0b", "width": 0.5},
        {"source": "whale:1", "target": "token:bonk", "color": "#10b981", "width": 0.4},
        {"source": "whale:2", "target": "token:popcat", "color": "#10b981", "width": 0.3},
        {"source": "token:bonk", "target": "token:popcat", "color": "#a855f7", "width": 0.2},
    ],
}
_ws_clients = []


def broadcast(msg):
    dead = []
    for q in _ws_clients:
        try:
            q.put(msg)
        except Exception:
            dead.append(q)
    for d in dead:
        _ws_clients.remove(d)


def background_loader():
    """Load full LOOM modules in background, update caches."""
    time.sleep(3)  # Let HTTP server start first
    try:
        from fabric import fabric
        from whales import whales
        from sources import start_ingestion
        from whale_seeds import KNOWN_WHALES
        from vault import full_sync, build_galaxy_from_vault
        from twitter_mapper import twitter_mapper
        from engines import NarrativeBrief
        from model_bridge import bridge as llm_bridge
        from vantage_watchlist import watchlist as vt_watchlist
        from vantage_puller import puller as vt_puller
        from vantage_wallet_sync import wallet_sync as vt_wallet_sync
        from solana_wallet_scanner import solscan

        # Initialize LLM-powered brief engine
        global _narrative_brief
        _narrative_brief = NarrativeBrief(
            whale_engine=whales,
            trenches_monitor=None,
            omniroute=llm_bridge,
        )

        # Seed whales
        import time as _t
        now = _t.time()
        for w in KNOWN_WHALES:
            addr = w["address"]
            for i, trade in enumerate(w.get("simulated_trades", [])):
                whales.register_trade(addr, trade["token"], trade["token_address"],
                                     trade["entry_sol"], now - (len(w.get("simulated_trades",[]))-i)*3600)
                if trade.get("exit_sol"):
                    whales.register_exit(addr, trade["token_address"], trade["exit_sol"],
                                        now - (len(w.get("simulated_trades",[]))-i)*3600 + trade.get("hold_min",45)*60)
            wallet = whales.wallets.get(addr)
            if wallet:
                for label in w.get("labels", []):
                    if label not in wallet.labels:
                        wallet.labels.append(label)
            if w.get("twitter"):
                twitter_mapper.matches[addr] = {"handle": w["twitter"], "confidence": w.get("twitter_confidence",0.7),
                                                 "evidence":["seeded"],"first_matched":now,"last_matched":now}
                twitter_mapper.handle_index[w["twitter"]] = addr

        full_sync(whales, twitter_mapper, fabric)
        start_ingestion()

        # Push detected whales to Vantage watchlist
        vt_watchlist.push_leaderboard(whales)

        # Start Vantage → LOOM enrichment loop
        import threading
        threading.Thread(target=vt_puller.enrich_loop,
                        args=(fabric, whales, 30), daemon=True).start()

        # Start Vantage ↔ LOOM bidirectional wallet sync
        threading.Thread(target=vt_wallet_sync.sync_loop,
                        args=(whales, twitter_mapper, None, 60), daemon=True).start()

        # Start Solana wallet scanner (enriches Vantage wallets with on-chain data)
        threading.Thread(target=solscan.scan_loop,
                        args=(whales, 120), daemon=True).start()

        # Update caches periodically
        while True:
            _t.sleep(5)
            global _cached_state, _cached_leaderboard, _cached_brief
            try:
                _cached_state = fabric.get_state()
                _cached_leaderboard = whales.get_leaderboard(10)
                if _narrative_brief:
                    _cached_brief = _narrative_brief.generate_json()
                # Periodic watchlist sync (every 5 cycles = 25s)
                if _t.time() - _last_wl_sync > 120:
                    vt_watchlist.push_leaderboard(whales)
                    _last_wl_sync = _t.time()
            except Exception:
                pass
    except Exception as e:
        print(f"[bg] loader error: {e}")


if __name__ == "__main__":
    print(f"\n  ◎  LOOM Fast — http://localhost:{PORT}")
    print(f"  Galaxy: /galaxy  |  Stream: /  |  Health: /api/health\n")

    # Start background loader
    threading.Thread(target=background_loader, daemon=True).start()

    # Start HTTP server immediately
    HTTPServer(("0.0.0.0", PORT), FastHandler).serve_forever()
