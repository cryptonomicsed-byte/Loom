#!/usr/bin/env python3
"""
Julia Bridge API — serves Julia graph engine state to LOOM mobile.
Runs on VPS port 8895. Lightweight HTTP server, no framework needed.
"""

import json
import os
import time
from http.server import HTTPServer, BaseHTTPRequestHandler

PORT = 8895
STATE_FILE = "/opt/loom/julia_state.json"
ALERT_FILE = "/opt/loom/julia_alerts.json"

_start_time = time.time()


class JuliaHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        path = self.path.split("?")[0]

        if path == "/api/health":
            self._json({"status": "ok", "uptime": int(time.time() - _start_time)})

        elif path == "/api/state":
            self._json(self._read(STATE_FILE, {"node_count": 0, "status": "no data"}))

        elif path == "/api/alerts":
            self._json(self._read(ALERT_FILE, []))

        elif path == "/api/subgraph":
            # Return specific sub-graph for visualization
            self._json({
                "nodes": [
                    {"id": "0xWhale_Alpha", "val": 8, "color": "#f43f5e", "kind": "whale"},
                    {"id": "0xInfluencer1", "val": 6, "color": "#f59e0b", "kind": "influencer"},
                    {"id": "0xDEX_Router", "val": 3, "color": "#8b5cf6", "kind": "router"},
                    {"id": "TOKEN_X", "val": 12, "color": "#10b981", "kind": "token"},
                ],
                "links": [
                    {"source": "0xWhale_Alpha", "target": "0xDEX_Router", "value": 500},
                    {"source": "0xInfluencer1", "target": "0xDEX_Router", "value": 500},
                    {"source": "0xDEX_Router", "target": "TOKEN_X", "value": 1000},
                ],
                "anomaly": {
                    "type": "tagged_convergence",
                    "token": "TOKEN_X",
                    "tagged_wallets": 4,
                    "severity": "high",
                },
            })

        else:
            self._json({"error": "not found"}, 404)

    def _read(self, path, default):
        try:
            if os.path.exists(path) and time.time() - os.path.getmtime(path) < 120:
                with open(path) as f:
                    return json.load(f)
        except Exception:
            pass
        return default

    def _json(self, data, status=200):
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(json.dumps(data, default=str).encode())

    def log_message(self, *a): pass


if __name__ == "__main__":
    print(f"  Julia Bridge API — http://0.0.0.0:{PORT}")
    print(f"  /api/state  /api/alerts  /api/subgraph")
    HTTPServer(("0.0.0.0", PORT), JuliaHandler).serve_forever()
