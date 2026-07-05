#!/usr/bin/env python3
"""
LOOM Server — HTTP + WebSocket. Serves the perception surface (PWA)
and streams real-time events from the fabric via WebSocket.

Port: 8889
"""

import json
import asyncio
import threading
import time
import queue
import sys
import os
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from fabric import fabric
from sources import start_ingestion
from whales import whales
from twitter_mapper import twitter_mapper
from solana_sources import start_whale_scanner
from galaxy import GALAXY_HTML
from whale_seeds import KNOWN_WHALES
from vault import full_sync, build_galaxy_from_vault, _ensure_dirs
from transformer_predictor import get_model, predict_from_events
from vantage_predictor import VantagePredictor, start_predictor
from backtester import WhaleBacktester
from agents import AgentOrchestrator
from trading_agents import AgentManager, AGENTS
from trenches import trenches, influencers
from narrative import narrative
from engines import (flow_engine, contagion_engine, counterfactual_engine,
                     precursor_engine, rhythm_engine, NarrativeBrief)

# Global orchestrator
_orchestrator = None
_trading_manager = None

def get_orchestrator():
    global _orchestrator
    if _orchestrator is None:
        _orchestrator = AgentOrchestrator()
    return _orchestrator

def get_trading_manager():
    global _trading_manager
    if _trading_manager is None:
        _trading_manager = AgentManager()
    return _trading_manager

PORT = 8889
WS_MAGIC = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"

# ═══════════════════════════════════════════════════════════════
# WebSocket Clients
# ═══════════════════════════════════════════════════════════════

ws_clients = []
ws_lock = threading.Lock()

def broadcast_event(enriched: dict):
    """Push event to all WebSocket clients."""
    msg = json.dumps(enriched, default=str)
    with ws_lock:
        dead = []
        for client in ws_clients:
            try:
                client.put(msg)
            except Exception:
                dead.append(client)
        for d in dead:
            ws_clients.remove(d)

# Register with fabric
fabric.subscribe(broadcast_event)


# ═══════════════════════════════════════════════════════════════
# PERCEPTION SURFACE (PWA)
# ═══════════════════════════════════════════════════════════════

PERCEPTION_HTML = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1,user-scalable=no,viewport-fit=cover">
<meta name="theme-color" content="#000000">
<meta name="apple-mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-status-bar-style" content="black-translucent">
<title>LOOM — Agent Perception Fabric</title>
<style>
:root {
  --void: #000008;
  --deep: #080818;
  --surface: #101028;
  --border: #1a1a3e;
  --violet: #8B5CF6;
  --cyan: #06B6D4;
  --emerald: #10B981;
  --rose: #F43F5E;
  --amber: #F59E0B;
  --text: #E2E8F0;
  --muted: #64748B;
  --dim: #334155;
  --font: -apple-system,BlinkMacSystemFont,'Inter','SF Pro Display',sans-serif;
  --mono: 'SF Mono','JetBrains Mono','Fira Code',monospace;
}
*{margin:0;padding:0;box-sizing:border-box}
html,body{width:100%;height:100%;overflow:hidden;background:var(--void)}
body{
  font-family:var(--font);color:var(--text);font-size:13px;
  -webkit-font-smoothing:antialiased;
  touch-action:manipulation;
}

/* ── Canvas Background ─────────────────────────────────────── */
#bg-canvas{position:fixed;top:0;left:0;width:100%;height:100%;z-index:0;pointer-events:none}

/* ── Top Bar ───────────────────────────────────────────────── */
.top-bar{
  position:fixed;top:0;left:0;right:0;z-index:20;
  padding:12px 16px;
  backdrop-filter:blur(20px) saturate(180%);
  -webkit-backdrop-filter:blur(20px) saturate(180%);
  background:rgba(0,0,8,0.7);
  border-bottom:1px solid rgba(139,92,246,0.1);
  display:flex;align-items:center;justify-content:space-between;
}
.top-bar h1{font-size:18px;font-weight:800;letter-spacing:-1px;background:linear-gradient(135deg,var(--violet),var(--cyan));-webkit-background-clip:text;-webkit-text-fill-color:transparent}
.top-bar .stats{font-size:10px;color:var(--muted);font-family:var(--mono);display:flex;gap:12px}

/* ── Event Stream ──────────────────────────────────────────── */
#stream{
  position:fixed;top:52px;left:0;right:0;bottom:0;
  overflow-y:auto;overflow-x:hidden;z-index:10;
  padding:8px 12px 80px;
  scroll-behavior:smooth;
}
#stream::-webkit-scrollbar{width:3px}
#stream::-webkit-scrollbar-track{background:transparent}
#stream::-webkit-scrollbar-thumb{background:var(--dim);border-radius:3px}

/* ── Event Card ────────────────────────────────────────────── */
.event-card{
  position:relative;margin-bottom:10px;padding:14px;
  background:var(--surface);
  border:1px solid var(--border);
  border-radius:14px;
  backdrop-filter:blur(12px);
  -webkit-backdrop-filter:blur(12px);
  overflow:hidden;
  animation:cardIn 0.4s cubic-bezier(0.16,1,0.3,1);
  transform-origin:top center;
  transition:transform 0.15s ease,border-color 0.3s;
}
.event-card:active{transform:scale(0.98)}
@keyframes cardIn{
  from{opacity:0;transform:translateY(20px) scale(0.95)}
  to{opacity:1;transform:translateY(0) scale(1)}
}

.event-glow{
  position:absolute;top:0;left:0;width:4px;height:100%;
  border-radius:4px 0 0 4px;
}
.glow-surge{background:linear-gradient(180deg,var(--cyan),var(--violet))}
.glow-signal{background:linear-gradient(180deg,var(--emerald),var(--cyan))}
.glow-anomaly{background:linear-gradient(180deg,var(--rose),var(--amber))}
.glow-trade{background:linear-gradient(180deg,var(--amber),var(--violet))}
.glow-default{background:var(--dim)}

.event-header{display:flex;align-items:center;gap:8px;margin-bottom:6px}
.event-type{
  font-size:9px;font-weight:700;text-transform:uppercase;letter-spacing:1px;
  padding:2px 8px;border-radius:99px;
}
.type-surge{background:rgba(6,182,212,0.12);color:var(--cyan)}
.type-signal{background:rgba(16,185,129,0.12);color:var(--emerald)}
.type-anomaly{background:rgba(244,63,94,0.12);color:var(--rose)}
.type-trade{background:rgba(245,158,11,0.12);color:var(--amber)}

.event-entity{font-size:15px;font-weight:800;letter-spacing:-0.5px;flex:1}
.event-conf{font-size:10px;color:var(--muted);font-family:var(--mono)}
.event-mag{
  width:48px;height:4px;border-radius:2px;background:var(--dim);overflow:hidden;margin-top:2px;
}
.event-mag-fill{height:100%;border-radius:2px;transition:width 0.6s cubic-bezier(0.16,1,0.3,1)}

.event-meta{display:flex;gap:8px;font-size:10px;color:var(--muted);margin-top:6px}
.event-source{padding:1px 6px;border-radius:4px;background:var(--deep);border:1px solid var(--border);font-size:9px}
.event-causal{font-size:9px;color:var(--violet);margin-top:4px}
.event-correlations{display:flex;flex-wrap:wrap;gap:4px;margin-top:6px}
.corr-tag{
  font-size:9px;padding:2px 6px;border-radius:4px;
  background:rgba(139,92,246,0.08);color:var(--violet);border:1px solid rgba(139,92,246,0.15);
}
.anomaly-badge{
  background:rgba(244,63,94,0.15);color:var(--rose);
  font-size:9px;padding:2px 8px;border-radius:99px;font-weight:700;margin-left:auto;
}

/* ── Empty State ───────────────────────────────────────────── */
#empty-state{
  position:absolute;top:50%;left:50%;transform:translate(-50%,-50%);
  text-align:center;color:var(--muted);
}
#empty-state .icon{font-size:48px;margin-bottom:12px;opacity:0.3}
#empty-state .title{font-size:16px;font-weight:700;color:var(--text);margin-bottom:4px}
#empty-state .sub{font-size:11px;opacity:0.6}

/* ── Bottom Nav ────────────────────────────────────────────── */
.bottom-nav{
  position:fixed;bottom:0;left:0;right:0;z-index:20;
  display:flex;padding:6px 8px env(safe-area-inset-bottom,6px);
  backdrop-filter:blur(20px);-webkit-backdrop-filter:blur(20px);
  background:rgba(0,0,8,0.8);
  border-top:1px solid rgba(139,92,246,0.1);
}
.nav-btn{
  flex:1;text-align:center;padding:8px 4px;font-size:10px;color:var(--muted);
  border:none;background:none;cursor:pointer;font-family:var(--font);
  transition:color 0.2s;
}
.nav-btn.active{color:var(--violet)}
.nav-btn .icon{font-size:18px;display:block;margin-bottom:2px}

/* ── Pulse ─────────────────────────────────────────────────── */
@keyframes pulse{0%,100%{box-shadow:0 0 0 0 rgba(139,92,246,0.3)}50%{box-shadow:0 0 0 8px rgba(139,92,246,0)}}
.new-pulse{animation:pulse 1s ease-out}

/* ── Particle canvas config ────────────────────────────────── */
@media(min-width:768px){
  body{max-width:480px;margin:0 auto}
  .top-bar,.bottom-nav{max-width:480px;left:50%;transform:translateX(-50%);width:100%}
}
</style>
</head>
<body>

<canvas id="bg-canvas"></canvas>

<div class="top-bar">
  <h1>LOOM</h1>
  <div class="stats">
    <span id="stat-events">0 events</span>
    <span id="stat-anomaly">0 anoms</span>
    <span id="stat-patterns">0 patterns</span>
  </div>
</div>

<div id="stream">
  <div id="empty-state">
    <div class="icon">◎</div>
    <div class="title">LOOM Online</div>
    <div class="sub">Waiting for market events...</div>
    <div class="sub" style="margin-top:4px;opacity:0.3">Agents are watching</div>
  </div>
</div>

<div class="bottom-nav">
  <button class="nav-btn active" data-view="stream"><span class="icon">◎</span>Stream</button>
  <button class="nav-btn" data-view="topology"><span class="icon">◉</span>Topology</button>
  <button class="nav-btn" data-view="anomalies"><span class="icon">◍</span>Anomalies</button>
</div>

<script>
// ── Particles ─────────────────────────────────────────────────
const canvas = document.getElementById('bg-canvas');
const ctx = canvas.getContext('2d');
let particles = [];

function resize() {
  canvas.width = window.innerWidth;
  canvas.height = window.innerHeight;
}
resize();
window.addEventListener('resize', resize);

// Create particle field
for (let i = 0; i < 40; i++) {
  particles.push({
    x: Math.random() * canvas.width,
    y: Math.random() * canvas.height,
    vx: (Math.random() - 0.5) * 0.3,
    vy: (Math.random() - 0.5) * 0.3,
    r: Math.random() * 1.5 + 0.5,
    alpha: Math.random() * 0.3 + 0.05,
  });
}

function drawParticles() {
  ctx.clearRect(0, 0, canvas.width, canvas.height);
  for (const p of particles) {
    p.x += p.vx;
    p.y += p.vy;
    if (p.x < 0) p.x = canvas.width;
    if (p.x > canvas.width) p.x = 0;
    if (p.y < 0) p.y = canvas.height;
    if (p.y > canvas.height) p.y = 0;
    ctx.beginPath();
    ctx.arc(p.x, p.y, p.r, 0, Math.PI * 2);
    ctx.fillStyle = `rgba(139,92,246,${p.alpha})`;
    ctx.fill();
  }

  // Draw connections between close particles
  for (let i = 0; i < particles.length; i++) {
    for (let j = i + 1; j < particles.length; j++) {
      const dx = particles[i].x - particles[j].x;
      const dy = particles[i].y - particles[j].y;
      const dist = Math.sqrt(dx * dx + dy * dy);
      if (dist < 80) {
        ctx.beginPath();
        ctx.moveTo(particles[i].x, particles[i].y);
        ctx.lineTo(particles[j].x, particles[j].y);
        ctx.strokeStyle = `rgba(139,92,246,${0.08 * (1 - dist / 80)})`;
        ctx.stroke();
      }
    }
  }
  requestAnimationFrame(drawParticles);
}
drawParticles();

// ── WebSocket ─────────────────────────────────────────────────
const ws = new WebSocket(`ws://${location.host}/ws`);
let eventCount = 0;
let anomalyCount = 0;
let patternCount = 0;

ws.onopen = () => console.log('[LOOM] connected');
ws.onclose = () => setTimeout(() => location.reload(), 3000);

ws.onmessage = (msg) => {
  const data = JSON.parse(msg.data);
  const event = data.event || {};
  renderEvent(event, data);
};

// ── Render ────────────────────────────────────────────────────
function renderEvent(event, enriched) {
  eventCount++;
  if (event.anom > 0.5) anomalyCount++;
  if (enriched.pattern) patternCount++;

  document.getElementById('stat-events').textContent = eventCount + ' events';
  document.getElementById('stat-anomaly').textContent = anomalyCount + ' anoms';
  document.getElementById('stat-patterns').textContent = patternCount + ' pats';

  // Remove empty state
  const empty = document.getElementById('empty-state');
  if (empty) empty.remove();

  const stream = document.getElementById('stream');
  const card = document.createElement('div');
  card.className = 'event-card new-pulse';

  // Glow color
  const glowMap = {
    price_surge: 'glow-surge', volume_spike: 'glow-surge',
    agent_signal: 'glow-signal', anomaly: 'glow-anomaly',
    trade_executed: 'glow-trade',
  };
  const glow = glowMap[event.t] || 'glow-default';

  // Type label
  const typeMap = {
    price_surge: 'type-surge', price_move: 'type-surge',
    volume_spike: 'type-surge', agent_signal: 'type-signal',
    anomaly: 'type-anomaly', trade_executed: 'type-trade',
  };
  const typeCls = typeMap[event.t] || '';

  const typeLabel = event.t.replace(/_/g, ' ');
  const magPct = Math.round(event.m * 100);

  let html = `<div class="event-glow ${glow}"></div>`;
  html += `<div class="event-header">`;
  html += `<span class="event-type ${typeCls}">${typeLabel}</span>`;
  html += `<span class="event-entity">${event.e}</span>`;
  html += `<span class="event-conf">c:${event.c.toFixed(2)}</span>`;
  if (event.anom > 0.4) html += `<span class="anomaly-badge">!</span>`;
  html += `</div>`;

  // Magnitude bar
  html += `<div class="event-mag"><div class="event-mag-fill" style="width:${magPct}%;background:linear-gradient(90deg,var(--violet),var(--cyan))"></div></div>`;

  // Meta: source, magnitude
  html += `<div class="event-meta">`;
  html += `<span class="event-source">${event.src}</span>`;
  html += `<span>mag ${event.m.toFixed(2)}</span>`;
  if (enriched.collective_conviction > 0) {
    html += `<span style="color:var(--emerald)">collective ${enriched.collective_conviction.toFixed(2)}</span>`;
  }
  html += `</div>`;

  // Causal chain
  if (enriched.causal_chain && enriched.causal_chain.upstream.length > 0) {
    html += `<div class="event-causal">↳ caused by: ${enriched.causal_chain.upstream.join(' → ')}</div>`;
  }

  // Correlations
  if (enriched.correlations && enriched.correlations.length > 0) {
    html += `<div class="event-correlations">`;
    for (const corr of enriched.correlations.slice(0, 4)) {
      html += `<span class="corr-tag">${corr.entity} ${corr.strength}</span>`;
    }
    html += `</div>`;
  }

  card.innerHTML = html;
  stream.insertBefore(card, stream.firstChild);

  // Limit visible cards
  while (stream.children.length > 80) {
    stream.lastChild.remove();
  }

  // Haptic on anomaly
  if (event.anom > 0.6 && navigator.vibrate) {
    navigator.vibrate([50, 30, 50]);
  }
}

// ── Bottom Nav ────────────────────────────────────────────────
document.querySelectorAll('.nav-btn').forEach(btn => {
  btn.addEventListener('click', () => {
    document.querySelectorAll('.nav-btn').forEach(b => b.classList.remove('active'));
    btn.classList.add('active');
    // Future: switch views
  });
});

// ── Gyro tilt effect ─────────────────────────────────────────
if (window.DeviceOrientationEvent) {
  window.addEventListener('deviceorientation', (e) => {
    const gamma = e.gamma || 0; // left-right tilt
    const beta = e.beta || 0;   // front-back tilt
    const cards = document.querySelectorAll('.event-card');
    const tiltX = gamma * 0.03;
    const tiltY = beta * 0.03;
    cards.forEach((card, i) => {
      card.style.transform = `perspective(800px) rotateY(${tiltX}deg) rotateX(${-tiltY}deg)`;
    });
  });
}

// ── Ambient Light ─────────────────────────────────────────────
if ('AmbientLightSensor' in window) {
  try {
    const sensor = new AmbientLightSensor();
    sensor.onreading = () => {
      const lux = sensor.illuminance;
      const opacity = Math.max(0.5, Math.min(1, lux / 500));
      document.body.style.opacity = opacity;
    };
    sensor.start();
  } catch(e) {}
}
</script>
</body>
</html>"""


# ═══════════════════════════════════════════════════════════════
# HTTP + WebSocket Server
# ═══════════════════════════════════════════════════════════════

LEADERBOARD_HTML = "<html><body><h1>Leaderboard</h1><p>Coming soon.</p></body></html>"


def build_galaxy_data() -> dict:
    """Build galaxy data from OKF vault + live whale engine state."""
    return build_galaxy_from_vault()

import base64
import hashlib
import struct
import socket

class LoomServer(BaseHTTPRequestHandler):

    def do_GET(self):
        path = urlparse(self.path).path

        if path == "/" or path == "/index.html":
            self._serve_html(PERCEPTION_HTML)

        elif path == "/galaxy":
            # Serve from static file to bypass pyc caching
            galaxy_path = "/data/data/com.termux/files/home/loom/galaxy.html"
            try:
                with open(galaxy_path) as f:
                    html = f.read()
                self._serve_html(html)
            except Exception as e:
                self._serve_html(f"<h1>Error loading galaxy</h1><pre>{e}</pre>")

        elif path == "/leaderboard":
            self._serve_html(LEADERBOARD_HTML)

        elif path == "/manifest.json":
            self._serve_json({
                "name": "LOOM",
                "short_name": "LOOM",
                "start_url": "/",
                "display": "standalone",
                "background_color": "#000008",
                "theme_color": "#000008",
                "icons": [],
            })

        elif path == "/api/state":
            self._serve_json(fabric.get_state())

        elif path == "/api/whales/leaderboard":
            self._serve_json(whales.get_leaderboard(20))

        elif path == "/api/whales/active":
            self._serve_json(whales.get_active_whales())

        elif path == "/api/whales/clusters":
            clusters = [c.to_dict() for c in whales.clusters.values()]
            self._serve_json(clusters)

        elif path == "/api/whales/rhythm-violators":
            self._serve_json(whales.get_rhythm_violators())

        elif path == "/api/twitter/matches":
            self._serve_json({
                "matches": [
                    {"wallet": w[:8], **m}
                    for w, m in twitter_mapper.get_all_matches().items()
                ]
            })

        elif path == "/api/whales/galaxy":
            self._serve_json(build_galaxy_data())

        elif path == "/api/transformer/predict":
            events = []
            for e in list(fabric.causal.recent)[-20:]:
                events.append({
                    "type": e.event_type,
                    "magnitude": e.magnitude,
                    "entity": e.entity,
                    "wallet_count": 0,
                    "confidence": e.confidence,
                })
            pred = predict_from_events(events)
            self._serve_json(pred)

        elif path == "/api/backtest":
            events = []
            for e in list(fabric.causal.recent)[-100:]:
                events.append({
                    "type": e.event_type,
                    "magnitude": e.magnitude,
                    "entity": e.entity,
                    "wallet_count": 0,
                    "confidence": e.confidence,
                })
            if len(events) < 10:
                self._serve_json({"error": "need at least 10 events"})
                return
            tester = WhaleBacktester(get_model())
            result = tester.backtest_sequence(events, window=10, step=3)
            self._serve_json(result)

        elif path == "/api/decide":
            params = parse_qs(urlparse(self.path).query)
            symbol = params.get("symbol", ["UNKNOWN"])[0]
            events = []
            for e in list(fabric.causal.recent)[-30:]:
                events.append({
                    "type": e.event_type,
                    "magnitude": e.magnitude,
                    "entity": e.entity,
                    "wallet_count": 0,
                    "confidence": e.confidence,
                    "metadata": {},
                })
            orch = get_orchestrator()
            result = orch.decide(symbol, events)
            self._serve_json(result)

        elif path == "/api/agents/stats":
            mgr = get_trading_manager()
            self._serve_json(mgr.get_all_stats())

        elif path == "/api/agents/profiles":
            profiles = []
            for p in AGENTS:
                profiles.append({
                    "name": p.name,
                    "strategy": p.strategy,
                    "target_gain": p.target_gain_pct,
                    "stop_loss": p.stop_loss_pct,
                    "max_sol": p.max_position_sol,
                    "min_conviction": p.min_conviction,
                    "description": p.description,
                })
            self._serve_json(profiles)

        elif path == "/api/trenches/alerts":
            self._serve_json(trenches.get_active_alerts())

        elif path == "/api/trenches/launches":
            self._serve_json(trenches.get_top_launches(20))

        elif path == "/api/trenches/influencers":
            self._serve_json(influencers.get_influencer_leaderboard())

        elif path == "/api/narrative":
            self._serve_json(narrative.get_state())

        elif path == "/api/flow":
            self._serve_json(flow_engine.detect_river())

        elif path == "/api/contagion":
            self._serve_json(contagion_engine.get_active_cascades())

        elif path == "/api/counterfactual":
            self._serve_json(counterfactual_engine.get_counterfactual_signals())

        elif path == "/api/rhythm-alerts":
            self._serve_json(rhythm_engine.get_active_alerts())

        elif path == "/api/brief":
            brief = NarrativeBrief(
                narrative_engine=narrative,
                flow_engine=flow_engine,
                contagion_engine=contagion_engine,
                counterfactual_engine=counterfactual_engine,
                precursor_engine=precursor_engine,
                rhythm_engine=rhythm_engine,
                whale_engine=whales,
                trenches_monitor=trenches,
            )
            self._serve_json(brief.generate_json())

        elif path == "/api/health":
            self._serve_json({
                "status": "ok",
                "fabric": "active",
                "whales": len(whales.wallets),
                "clusters": len(whales.clusters),
                "twitter_matches": len(twitter_mapper.matches),
            })

        elif path == "/ws":
            self._handle_websocket()

        else:
            self.send_response(404)
            self.end_headers()

    def _serve_html(self, html: str):
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        self.wfile.write(html.encode())

    def _serve_json(self, data):
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(json.dumps(data, default=str).encode())

    def _handle_websocket(self):
        """WebSocket upgrade handshake + event stream."""
        key = self.headers.get("Sec-WebSocket-Key", "")
        if not key:
            self.send_response(400)
            self.end_headers()
            return

        # Handshake
        accept = base64.b64encode(
            hashlib.sha1((key + WS_MAGIC).encode()).digest()
        ).decode()

        self.send_response(101)
        self.send_header("Upgrade", "websocket")
        self.send_header("Connection", "Upgrade")
        self.send_header("Sec-WebSocket-Accept", accept)
        self.end_headers()

        # Client message queue
        client_queue = queue.Queue()
        ws_clients.append(client_queue)

        try:
            while True:
                try:
                    msg = client_queue.get(timeout=1)
                    frame = self._ws_frame(msg)
                    self.wfile.write(frame)
                    self.wfile.flush()
                except queue.Empty:
                    # Send heartbeat
                    frame = self._ws_frame('{"type":"ping"}')
                    try:
                        self.wfile.write(frame)
                        self.wfile.flush()
                    except Exception:
                        break
        except Exception:
            pass
        finally:
            if client_queue in ws_clients:
                ws_clients.remove(client_queue)

    def _ws_frame(self, data: str) -> bytes:
        """Create WebSocket text frame."""
        payload = data.encode("utf-8")
        length = len(payload)
        frame = bytearray()
        frame.append(0x81)  # FIN + text opcode

        if length < 126:
            frame.append(length)
        elif length < 65536:
            frame.append(126)
            frame.extend(struct.pack(">H", length))
        else:
            frame.append(127)
            frame.extend(struct.pack(">Q", length))

        frame.extend(payload)
        return bytes(frame)

    def log_message(self, format, *args):
        pass


def seed_known_whales():
    """Pre-seed whale registry with known profitable Solana wallets."""
    import time as _time
    now = _time.time()

    for w in KNOWN_WHALES:
        addr = w["address"]
        # Register multiple simulated trades to build dossier
        for i, trade in enumerate(w.get("simulated_trades", [])):
            whales.register_trade(
                address=addr,
                token=trade["token"],
                token_address=trade["token_address"],
                entry_sol=trade["entry_sol"],
                entry_time=now - (len(w.get("simulated_trades", [])) - i) * 3600,
            )
            # Simulate exit with profit
            if trade.get("exit_sol"):
                whales.register_exit(
                    address=addr,
                    token_address=trade["token_address"],
                    exit_sol=trade["exit_sol"],
                    exit_time=now - (len(w.get("simulated_trades", [])) - i) * 3600 + trade.get("hold_min", 45) * 60,
                )

        # Set labels
        wallet = whales.wallets.get(addr)
        if wallet:
            for label in w.get("labels", []):
                if label not in wallet.labels:
                    wallet.labels.append(label)
            wallet.active_hours = w.get("active_hours", [])
            wallet.active_days = w.get("active_days", [])

        # Twitter mapping
        if w.get("twitter"):
            twitter_mapper.ingest_tweet(
                w["twitter"], w.get("twitter_bio", ""), now,
            )
            twitter_mapper.matches[addr] = {
                "handle": w["twitter"],
                "confidence": w.get("twitter_confidence", 0.7),
                "evidence": ["seeded"],
                "first_matched": now,
                "last_matched": now,
            }
            twitter_mapper.handle_index[w["twitter"]] = addr

    print(f"  [whales] seeded {len(KNOWN_WHALES)} known whales")


def run():
    print(f"\n  ◎  LOOM — Agent Perception Fabric")
    print(f"  http://localhost:{PORT}")
    print(f"  Fabric: active  |  Whales: tracking  |  WS: /ws\n")

    # Start source ingestion
    start_ingestion()

    # Start whale scanner (Solana + Birdeye + pump.fun)
    start_whale_scanner()

    # Seed known whales
    seed_known_whales()

    # Initial vault sync
    full_sync(whales, twitter_mapper, fabric)

    # Start periodic vault sync
    import threading as _th
    def _vault_sync_loop():
        import time as _t
        while True:
            _t.sleep(30)
            try:
                full_sync(whales, twitter_mapper, fabric)
            except Exception:
                pass
    _th.Thread(target=_vault_sync_loop, daemon=True).start()

    # Start Vantage signal predictor (transformer → Vantage API)
    start_predictor(interval=120)

    # Start autonomous trading agents
    mgr = get_trading_manager()
    mgr.start(interval=60)

    # Start pump.fun trenches monitor
    import threading as _th
    _th.Thread(target=trenches.run_loop, daemon=True).start()

    # Bridge: fabric events → narrative engine
    def _bridge_fabric_to_narrative():
        while True:
            import time as _t; _t.sleep(10)
            try:
                for e in list(fabric.causal.recent)[-20:]:
                    narrative.ingest(
                        e.event_type, e.entity, "token",
                        e.magnitude, source="fabric",
                        metadata={"confidence": e.confidence, "anomaly": e.anomaly_score},
                    )
            except Exception:
                pass
    _th.Thread(target=_bridge_fabric_to_narrative, daemon=True).start()

    # Start HTTP server
    server = HTTPServer(("0.0.0.0", PORT), LoomServer)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n  shutdown")
        server.shutdown()


if __name__ == "__main__":
    run()
