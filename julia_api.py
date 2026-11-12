#!/usr/bin/env python3
"""
Julia Bridge API — serves the Julia graph engine's exported state.

Reads the files julia_daemon.jl writes (same LOOM_JULIA_STATE_DIR, default
./state next to this file). Port 8895, loopback-only by default.

Fixes vs the original:
  * paths were hardcoded to /opt/loom (the retired Hostinger box)
  * bound to 0.0.0.0 with CORS * — now 127.0.0.1 unless LOOM_JULIA_BIND overrides
  * /api/subgraph returned a HARDCODED fake graph (a "whale" converging on
    TOKEN_X, severity "high") as if it were live — now returns only what exists
  * /api/health said "ok" even with the daemon dead — now reports daemon freshness
"""

import json
import os
import time
from http.server import HTTPServer, BaseHTTPRequestHandler

HERE = os.path.dirname(os.path.abspath(__file__))
PORT = int(os.environ.get("LOOM_JULIA_PORT", "8895"))
BIND = os.environ.get("LOOM_JULIA_BIND", "127.0.0.1")
STATE_DIR = os.environ.get("LOOM_JULIA_STATE_DIR", os.path.join(HERE, "state"))
STATE_FILE = os.path.join(STATE_DIR, "julia_state.json")
ALERT_FILE = os.path.join(STATE_DIR, "julia_alerts.json")
STALE_AFTER = int(os.environ.get("LOOM_JULIA_STALE_AFTER", "120"))  # seconds

_start_time = time.time()


def _age(path):
    try:
        return time.time() - os.path.getmtime(path)
    except OSError:
        return None


class JuliaHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        path = self.path.split("?")[0]

        if path == "/api/health":
            age = _age(STATE_FILE)
            fresh = age is not None and age < STALE_AFTER
            state = self._read(STATE_FILE, {})
            self._json({
                "status": "ok",                      # the API itself is up
                "daemon": "fresh" if fresh else ("stale" if age is not None else "no_state"),
                "state_age_s": None if age is None else round(age, 1),
                "source": state.get("source", "unknown"),
                "uptime": int(time.time() - _start_time),
            })

        elif path == "/api/state":
            self._json(self._read(STATE_FILE, {"node_count": 0, "status": "no data"}))

        elif path == "/api/alerts":
            self._json(self._read(ALERT_FILE, []))

        elif path == "/api/subgraph":
            # The daemon does not export a subgraph yet. Say so instead of
            # inventing one.
            self._json({
                "nodes": [], "links": [], "anomaly": None,
                "status": "not_exported",
                "note": "julia_daemon.jl exports state and alerts only; no subgraph export exists yet",
            })

        else:
            self._json({"error": "not found"}, 404)

    def _read(self, path, default):
        try:
            age = _age(path)
            if age is not None and age < STALE_AFTER:
                with open(path) as f:
                    return json.load(f)
        except Exception:
            pass
        return default

    def _json(self, data, status=200):
        body = json.dumps(data, default=str).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):
        pass


if __name__ == "__main__":
    print(f"  Julia Bridge API — http://{BIND}:{PORT}  state_dir={STATE_DIR}", flush=True)
    print("  /api/health  /api/state  /api/alerts  /api/subgraph", flush=True)
    HTTPServer((BIND, PORT), JuliaHandler).serve_forever()
