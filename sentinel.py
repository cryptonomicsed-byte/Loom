#!/usr/bin/env python3
"""
Ares Sentinel — LOOM Command Center.
Single control panel for all daemons, Julia engine, and trading agents.

Port 8885. Serves HTML dashboard + control API.
"""

import json
import time
import os
import sys
import subprocess
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler

PORT = 8885

# Services we monitor
SERVICES = {
    "loom_fast": {"port": 8889, "name": "LOOM Fast Server", "type": "local"},
    "ares_dashboard": {"port": 8880, "name": "Ares Dashboard", "type": "local"},
    "julia_engine": {"host": "2.25.70.156", "port": 8895, "name": "Julia Graph Engine", "type": "vps"},
    "freqtrade": {"host": "2.25.70.156", "port": 9870, "name": "Freqtrade Trading", "type": "vps"},
    "vantage": {"host": "2.25.70.156", "port": 8001, "name": "Vantage API", "type": "vps"},
}

DAEMON_HEALTH = {}

_start_time = time.time()

def check_service(svc):
    """Check if a service is reachable."""
    key = svc["name"]
    try:
        if svc["type"] == "local":
            import urllib.request
            url = f"http://localhost:{svc['port']}/api/health"
            req = urllib.request.Request(url, headers={"User-Agent": "AresSentinel/1.0"})
            with urllib.request.urlopen(req, timeout=3) as resp:
                data = json.loads(resp.read())
                DAEMON_HEALTH[key] = {"status": "online", "data": data, "since": time.time()}
                return True
        else:
            # VPS service — use SSH tunnel check
            result = subprocess.run(
                ["ssh", "-o", "ConnectTimeout=3", svc["host"],
                 f"curl -s http://localhost:{svc['port']}/api/health 2>/dev/null || echo 'dead'"],
                capture_output=True, text=True, timeout=5
            )
            if "ok" in result.stdout.lower():
                DAEMON_HEALTH[key] = {"status": "online", "since": time.time()}
                return True
    except Exception:
        pass

    DAEMON_HEALTH[key] = {"status": "offline", "since": DAEMON_HEALTH.get(key, {}).get("since", time.time())}
    return False

def health_loop():
    while True:
        for svc in SERVICES.values():
            check_service(svc)
        time.sleep(15)

threading.Thread(target=health_loop, daemon=True).start()

# ═══════════════════════════════════════════════════════════════
# DASHBOARD HTML
# ═══════════════════════════════════════════════════════════════

DASHBOARD = r"""<!DOCTYPE html>
<html><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Ares Sentinel</title>
<style>
:root{--bg:#0a0a10;--surface:#12121c;--border:#1e1e32;--text:#e2e8f0;--muted:#64748b;--accent:#8b5cf6;--online:#10b981;--offline:#ef4444;--warn:#f59e0b}
*{margin:0;padding:0;box-sizing:border-box}
body{background:var(--bg);color:var(--text);font-family:-apple-system,BlinkMacSystemFont,sans-serif;font-size:13px;padding:12px 12px 60px}
.header{display:flex;justify-content:space-between;align-items:center;padding:8px 0;border-bottom:1px solid var(--border);margin-bottom:12px}
.header h1{font-size:15px;font-weight:800;color:var(--accent)}
.header .time{font-size:10px;color:var(--muted)}
.grid{display:grid;grid-template-columns:1fr 1fr;gap:8px;margin-bottom:12px}
.card{background:var(--surface);border:1px solid var(--border);border-radius:8px;padding:10px}
.card h3{font-size:11px;color:var(--muted);text-transform:uppercase;letter-spacing:.5px;margin-bottom:6px}
.card .value{font-size:18px;font-weight:800}
.card .label{font-size:10px;color:var(--muted);margin-top:2px}
.status-bar{display:flex;flex-direction:column;gap:4px;margin-bottom:12px}
.status-row{display:flex;justify-content:space-between;align-items:center;padding:8px 10px;background:var(--surface);border:1px solid var(--border);border-radius:6px}
.status-dot{width:8px;height:8px;border-radius:50%;margin-right:8px}
.controls{display:flex;gap:6px;flex-wrap:wrap;margin-bottom:12px}
.btn{background:var(--surface);border:1px solid var(--border);color:var(--text);padding:8px 14px;border-radius:6px;font-size:11px;cursor:pointer}
.btn:hover{background:var(--accent);border-color:var(--accent)}
.btn.danger{border-color:var(--offline);color:var(--offline)}
.btn.danger:hover{background:var(--offline);color:white}
.log{background:#000;border:1px solid var(--border);border-radius:6px;padding:10px;font-family:monospace;font-size:10px;max-height:200px;overflow-y:auto;color:var(--muted)}
.bottom-bar{position:fixed;bottom:0;left:0;right:0;background:var(--bg);border-top:1px solid var(--border);display:flex;padding:6px}
.bottom-bar a{flex:1;text-align:center;font-size:10px;color:var(--muted);text-decoration:none;padding:6px}
.bottom-bar a:hover{color:var(--accent)}
</style></head>
<body>
<div class="header"><h1>◈  ARES SENTINEL</h1><div class="time" id="clock"></div></div>

<div class="grid">
  <div class="card"><h3>Daemons</h3><div class="value" id="online-count" style="color:var(--online)">-</div><div class="label">online</div></div>
  <div class="card"><h3>Alerts</h3><div class="value" id="alert-count" style="color:var(--warn)">-</div><div class="label">active</div></div>
  <div class="card"><h3>Julia Nodes</h3><div class="value" id="julia-nodes">-</div><div class="label">wallets tracked</div></div>
  <div class="card"><h3>Uptime</h3><div class="value" id="sentinel-uptime">-</div><div class="label">sentinel</div></div>
</div>

<div class="status-bar" id="status-bar"></div>

<div class="controls">
  <button class="btn" onclick="action('loom/restart')">⟳ Restart LOOM</button>
  <button class="btn" onclick="action('julia/restart')">⟳ Restart Julia</button>
  <button class="btn" onclick="action('loom/reload')">↻ Reload Vault</button>
  <button class="btn" onclick="action('brief/generate')">📋 Force Brief</button>
</div>

<div class="log" id="log"></div>

<div class="bottom-bar">
  <a href="http://localhost:8889">LOOM Stream</a>
  <a href="http://localhost:8889/galaxy">Galaxy</a>
  <a href="http://localhost:8880">Ares Dashboard</a>
  <a href="#" onclick="action('health')">Refresh</a>
</div>

<script>
function update() {
  fetch('/api/control/health').then(r=>r.json()).then(d=>{
    var online=0;
    var bar=document.getElementById('status-bar');
    bar.innerHTML='';
    d.services.forEach(s=>{
      var c=s.status==='online'?'var(--online)':'var(--offline)';
      if(s.status==='online') online++;
      bar.innerHTML+='<div class=status-row><span><span class=status-dot style=background:'+c+'></span>'+s.name+'</span><span style=color:'+c+'>'+s.status+'</span></div>';
    });
    document.getElementById('online-count').textContent=online+'/'+d.services.length;
    document.getElementById('alert-count').textContent=d.alerts||0;
    document.getElementById('julia-nodes').textContent=d.julia_nodes||0;
    var u=Math.floor(d.uptime/60);
    document.getElementById('sentinel-uptime').textContent=u+'m';
    if(d.last_log) {
      var l=document.getElementById('log');
      l.innerHTML=d.last_log.map(x=>'<div>'+x+'</div>').join('');
    }
  });
  var now=new Date();
  document.getElementById('clock').textContent=now.toLocaleTimeString();
}
function action(cmd) {
  fetch('/api/control/'+cmd).then(r=>r.json()).then(d=>{
    update();
  });
}
update();
setInterval(update,10000);
</script>
</body></html>"""

# ═══════════════════════════════════════════════════════════════
# SERVER
# ═══════════════════════════════════════════════════════════════

log_buffer = []

def log(msg):
    ts = time.strftime("%H:%M:%S")
    entry = f"[{ts}] {msg}"
    log_buffer.append(entry)
    if len(log_buffer) > 50:
        log_buffer.pop(0)
    print(entry)

class SentinelHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        path = self.path.split("?")[0]

        if path == "/":
            self._html(DASHBOARD)

        elif path == "/api/control/health":
            nodes = 0
            try:
                r = subprocess.run(["ssh", "-o", "ConnectTimeout=2", "2.25.70.156",
                    "cat /opt/loom/julia_state.json 2>/dev/null | python3 -c 'import json,sys; print(json.load(sys.stdin).get(\"node_count\",0))'"],
                    capture_output=True, text=True, timeout=5)
                nodes = int(r.stdout.strip() or 0)
            except: pass

            self._json({
                "services": [
                    {"name": svc["name"], "status": DAEMON_HEALTH.get(svc["name"], {}).get("status", "unknown")}
                    for svc in SERVICES.values()
                ],
                "alerts": 0,
                "julia_nodes": nodes,
                "uptime": int(time.time() - _start_time),
                "last_log": log_buffer[-15:],
            })

        elif path == "/api/control/loom/restart":
            log("Restarting LOOM...")
            subprocess.Popen(["pkill", "-f", "fast_server"], stderr=subprocess.DEVNULL)
            time.sleep(1)
            subprocess.Popen(["python3", "/data/data/com.termux/files/home/loom/fast_server.py"],
                           cwd="/data/data/com.termux/files/home/loom")
            self._json({"action": "loom_restart", "status": "ok"})

        elif path == "/api/control/julia/restart":
            log("Restarting Julia engine...")
            subprocess.run(["ssh", "2.25.70.156", "pkill -f julia_daemon; sleep 1; cd /opt/loom && /usr/local/bin/julia julia_daemon.jl > daemon.log 2>&1 &"],
                         timeout=10)
            self._json({"action": "julia_restart", "status": "ok"})

        elif path == "/api/control/loom/reload":
            log("Reloading vault...")
            self._json({"action": "vault_reload", "status": "ok"})

        elif path == "/api/control/brief/generate":
            log("Forcing Narrative Brief...")
            self._json({"action": "brief_generate", "status": "ok"})

        elif path == "/api/control/health":
            self._json({"status": "ok", "uptime": int(time.time() - _start_time)})

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

    def log_message(self, *a): pass


if __name__ == "__main__":
    log("Ares Sentinel starting...")
    print(f"\n  ◈  Sentinel — http://localhost:{PORT}")
    HTTPServer(("0.0.0.0", PORT), SentinelHandler).serve_forever()
