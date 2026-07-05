"""
LOOM Whale Galaxy — 3D spatial perception surface.
Renders whale wallets, clusters, tokens, and correlations
as an immersive Three.js force-directed galaxy.
Inspired by Vantage's 3D Memory Galaxy.
Served at /galaxy from the LOOM server.
"""

GALAXY_HTML = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1,user-scalable=no">
<title>LOOM Whale Galaxy</title>
<style>
*{margin:0;padding:0;box-sizing:border-box}
html,body{width:100%;height:100%;overflow:hidden;background:#020010;font-family:-apple-system,BlinkMacSystemFont,sans-serif}
#galaxy{position:fixed;top:0;left:0;width:100%;height:100%;z-index:1}
#hud{position:fixed;top:12px;left:12px;right:12px;z-index:10;pointer-events:none;
  display:flex;justify-content:space-between;align-items:flex-start}
#hud > * {pointer-events:auto}
.hud-card{
  background:rgba(5,5,20,0.85);backdrop-filter:blur(12px);
  -webkit-backdrop-filter:blur(12px);border:1px solid rgba(139,92,246,0.15);
  border-radius:12px;padding:10px 14px;font-size:11px;color:#c4b5fd;
}
.hud-card .val{font-size:20px;font-weight:800;color:#e2e8f0;font-family:monospace}
.hud-card .lbl{font-size:9px;color:#64748b;text-transform:uppercase;margin-top:2px}
#detail{
  position:fixed;bottom:80px;left:12px;right:12px;z-index:10;
  background:rgba(5,5,20,0.9);backdrop-filter:blur(16px);
  border:1px solid rgba(139,92,246,0.2);border-radius:14px;
  padding:14px;display:none;max-height:200px;overflow-y:auto;
  font-size:12px;color:#e2e8f0;
}
#detail .title{font-size:15px;font-weight:800;margin-bottom:6px}
#detail .row{display:flex;gap:12px;margin:3px 0;font-size:11px}
#detail .tag{padding:1px 6px;border-radius:4px;font-size:9px;font-weight:600}
.bottom-nav{
  position:fixed;bottom:0;left:0;right:0;z-index:10;display:flex;
  padding:8px;background:rgba(2,0,16,0.9);backdrop-filter:blur(12px);
  border-top:1px solid rgba(139,92,246,0.1);
}
.nav-btn{
  flex:1;text-align:center;font-size:10px;color:#64748b;background:none;
  border:none;padding:8px 4px;cursor:pointer;font-family:inherit;
}
.nav-btn.active{color:#8b5cf6}
.nav-btn .icon{font-size:18px;display:block;margin-bottom:2px}
.loading{
  position:fixed;top:50%;left:50%;transform:translate(-50%,-50%);
  color:#64748b;font-size:14px;z-index:20;
}
</style>
</head>
<body>

<div id="galaxy"></div>

<div id="hud">
  <div class="hud-card"><div class="val" id="hud-whales">0</div><div class="lbl">Whales</div></div>
  <div class="hud-card"><div class="val" id="hud-clusters">0</div><div class="lbl">Clusters</div></div>
  <div class="hud-card"><div class="val" id="hud-tokens">0</div><div class="lbl">Tokens</div></div>
  <div class="hud-card"><div class="val" id="hud-alerts">0</div><div class="lbl">Alerts</div></div>
</div>

<div id="detail"></div>
<div id="loading" class="loading">◎ Loading galaxy...</div>

<div class="bottom-nav">
  <button class="nav-btn active" data-view="galaxy"><span class="icon">◎</span>Galaxy</button>
  <button class="nav-btn" data-view="stream"><span class="icon">◉</span>Stream</button>
  <button class="nav-btn" data-view="leaderboard"><span class="icon">◈</span>Board</button>
</div>

<script src="https://cdn.jsdelivr.net/npm/three@0.160.0/build/three.min.js"></script>
<script src="https://cdn.jsdelivr.net/npm/3d-force-graph/dist/3d-force-graph.min.js"></script>

<script>
// Wait for CDN scripts to load, with fallback
function waitForLibs(cb, retries) {
  retries = retries || 30;
  if (window.THREE && window.ForceGraph3D) {
    cb();
  } else if (retries > 0) {
    setTimeout(function() { waitForLibs(cb, retries-1); }, 500);
  } else {
    var el = document.getElementById('loading');
    el.innerHTML = '⚠️ CDN failed to load<br><small>Check internet connection</small>';
    el.style.color = '#f43f5e';
  }
}

var ForceGraph3D, THREE;
waitForLibs(function() {
  ForceGraph3D = window.ForceGraph3D;
  THREE = window.THREE;

const API = '/api/whales/galaxy';
const POLL_MS = 8000;
let graph = null;
let currentData = null;

// Tier colors
const TIER_COLORS = {
  1: '#f43f5e',  // Elite = rose/crimson
  2: '#f59e0b',  // Strong = amber
  3: '#8b5cf6',  // Good = violet
  4: '#64748b',  // Tracking = muted
  agent: '#ffffff',
  cluster: '#06b6d4',
  token: '#10b981',
  correlation: '#a855f7',
};

function recencyColor(base, lastActive) {
  if (!lastActive) return base;
  const hours = (Date.now()/1000 - lastActive) / 3600;
  const f = Math.max(0.1, 1 - hours / 72);
  const hex = base.replace('#', '');
  const r = parseInt(hex.substr(0,2), 16);
  const g = parseInt(hex.substr(2,2), 16);
  const b = parseInt(hex.substr(4,2), 16);
  const brighten = (v) => Math.round(v + (255 - v) * f * 0.4);
  return `#${[brighten(r), brighten(g), brighten(b)].map(v => v.toString(16).padStart(2,'0')).join('')}`;
}

function buildGraph(data) {
  const nodes = [], links = [];
  const center = 'loom:agent';
  nodes.push({ id: center, name: 'LOOM', val: 18, color: '#ffffff', kind: 'agent', rec: 1 });

  // Whale nodes
  const whales = data.whales || [];
  for (const w of whales) {
    const tier = w.tier || 4;
    const color = recencyColor(TIER_COLORS[tier] || TIER_COLORS[4], w.last_active);
    const val = 3 + (w.predictive_value || 0) * 12;
    nodes.push({
      id: w.address, name: w.label || w.address_short || w.address?.slice(0,8),
      val, color, kind: 'whale', tier, rec: Math.min(1, (w.predictive_value || 0)),
      pv: w.predictive_value, wr: w.win_rate, trades: w.total_trades,
      cluster: w.cluster_id, twitter: w.twitter_handle,
    });
    links.push({ source: center, target: w.address, color, width: 0.3 + (w.predictive_value || 0) * 0.5 });
  }

  // Cluster hubs
  const clusters = data.clusters || [];
  const clusterIds = new Set();
  for (const c of clusters) {
    const cid = c.id || c.cluster_id;
    if (!cid || clusterIds.has(cid)) continue;
    clusterIds.add(cid);
    const val = 6 + (c.members || 0) * 1.5;
    nodes.push({ id: `cluster:${cid}`, name: `Cluster ${cid.slice(0,6)}`, val, color: TIER_COLORS.cluster, kind: 'cluster', members: c.members, threat: c.threat_level });
    // Link cluster to its member whales
    for (const w of whales) {
      if (w.cluster_id === cid) {
        links.push({ source: `cluster:${cid}`, target: w.address, color: TIER_COLORS.cluster, width: 0.6 });
      }
    }
    links.push({ source: center, target: `cluster:${cid}`, color: TIER_COLORS.cluster, width: 1.0 });
  }

  // Token nodes
  const tokens = data.tokens || [];
  for (const t of tokens) {
    const val = 2 + Math.min(10, (t.volume_24h || 0) / 500000);
    nodes.push({
      id: t.address, name: t.symbol || t.address?.slice(0,8),
      val, color: TIER_COLORS.token, kind: 'token',
      change_24h: t.price_change_pct, conviction: t.conviction,
      volume: t.volume_24h, liquidity: t.liquidity,
    });
    links.push({ source: center, target: t.address, color: TIER_COLORS.token, width: 0.2 });
  }

  // Correlations between tokens
  const corrs = data.correlations || [];
  for (const c of corrs) {
    links.push({
      source: c.source, target: c.target,
      color: TIER_COLORS.correlation,
      width: (c.strength || 0.3) * 0.8,
    });
  }

  // Whale → token trade edges
  const trades = data.trades || [];
  for (const t of trades) {
    links.push({
      source: t.wallet, target: t.token,
      color: t.profit > 0 ? '#10b981' : '#f43f5e',
      width: 0.4 + Math.abs(t.profit || 0) * 2,
    });
  }

  return { nodes, links };
}

// Star texture
const cv = document.createElement('canvas'); cv.width = cv.height = 64;
const cx = cv.getContext('2d');
const grad = cx.createRadialGradient(32, 32, 0, 32, 32, 32);
grad.addColorStop(0, 'rgba(255,255,255,1)');
grad.addColorStop(0.2, 'rgba(255,255,255,0.7)');
grad.addColorStop(0.5, 'rgba(255,255,255,0.2)');
grad.addColorStop(1, 'rgba(255,255,255,0)');
cx.fillStyle = grad; cx.fillRect(0, 0, 64, 64);
const starTex = new THREE.CanvasTexture(cv);

async function initGalaxy() {
  const el = document.getElementById('galaxy');
  const loading = document.getElementById('loading');

  try {
    const resp = await fetch(API);
    currentData = await resp.json();
    loading.style.display = 'none';

    const { nodes, links } = buildGraph(currentData);

    document.getElementById('hud-whales').textContent = nodes.filter(n => n.kind === 'whale').length;
    document.getElementById('hud-clusters').textContent = nodes.filter(n => n.kind === 'cluster').length;
    document.getElementById('hud-tokens').textContent = nodes.filter(n => n.kind === 'token').length;

    graph = ForceGraph3D(el)
      .backgroundColor('#020010')
      .showNavInfo(false)
      .graphData({ nodes, links })
      .nodeLabel((n) => {
        let html = `<b>${n.name}</b>`;
        if (n.kind === 'whale') {
          html += `<br>Tier ${n.tier} · PV ${(n.pv||0).toFixed(2)} · WR ${n.wr||0}%`;
          if (n.twitter) html += `<br>@${n.twitter}`;
        }
        if (n.kind === 'cluster') html += `<br>${n.members||0} members · ${n.threat||'unknown'}`;
        if (n.kind === 'token' && n.conviction) html += `<br>Conv ${n.conviction.toFixed(1)} · Vol $${((n.volume||0)/1000).toFixed(1)}K`;
        return `<div style="font-family:monospace;font-size:11px;color:#dfe6ff;background:rgba(5,5,16,.9);padding:4px 8px;border-radius:6px;border:1px solid rgba(255,255,255,.12)">${html}</div>`;
      })
      .nodeThreeObject((n) => {
        const mat = new THREE.SpriteMaterial({
          map: starTex, color: n.color, transparent: true,
          opacity: n.kind === 'token' ? 0.5 : 0.85,
          depthWrite: false, blending: THREE.AdditiveBlending,
        });
        const sprite = new THREE.Sprite(mat);
        const s = 2.5 + n.val * 1.2;
        sprite.scale.set(s, s, 1);
        return sprite;
      })
      .linkColor((l) => l.color || '#334155')
      .linkOpacity(0.15)
      .linkWidth((l) => l.width || 0.3)
      .onNodeClick((n) => {
        const detail = document.getElementById('detail');
        if (n.kind === 'whale') {
          detail.style.display = 'block';
          detail.innerHTML = `<div class="title">🐋 ${n.name}</div>
            <div class="row"><span>Tier ${n.tier} · PV ${(n.pv||0).toFixed(3)} · WR ${n.wr||0}%</span></div>
            <div class="row"><span>${n.trades||0} trades</span>${n.twitter ? `<span>@${n.twitter}</span>` : ''}</div>
            <div class="row"><span class="tag" style="background:rgba(139,92,246,0.2);color:#a78bfa">${n.cluster ? 'In cluster' : 'Solo'}</span></div>`;
        } else if (n.kind === 'cluster') {
          detail.style.display = 'block';
          detail.innerHTML = `<div class="title">🪐 ${n.name}</div>
            <div class="row"><span>${n.members||0} wallets · ${n.threat||'unknown'} threat</span></div>`;
        } else if (n.kind === 'token') {
          detail.style.display = 'block';
          detail.innerHTML = `<div class="title">🪙 ${n.name}</div>
            <div class="row"><span>24h: ${(n.change_24h||0).toFixed(1)}% · Vol: $${((n.volume||0)/1000).toFixed(1)}K</span></div>`;
        } else {
          detail.style.display = 'none';
        }
      });

    // Orbit slowly
    graph.cameraPosition({ x: 60, y: 30, z: 80 }, { x: 0, y: 0, z: 0 });

  } catch(e) {
    loading.textContent = 'Connection lost — retrying...';
    console.error(e);
  }
}

// Poll for updates
initGalaxy();
setInterval(async () => {
  try {
    const resp = await fetch(API);
    currentData = await resp.json();
    if (graph) {
      const { nodes, links } = buildGraph(currentData);
      graph.graphData({ nodes, links });
      document.getElementById('hud-whales').textContent = nodes.filter(n => n.kind === 'whale').length;
      document.getElementById('hud-clusters').textContent = nodes.filter(n => n.kind === 'cluster').length;
      document.getElementById('hud-tokens').textContent = nodes.filter(n => n.kind === 'token').length;
    }
  } catch(e) {}
}, POLL_MS);

// Nav buttons
document.querySelectorAll('.nav-btn').forEach(btn => {
  btn.addEventListener('click', () => {
    document.querySelectorAll('.nav-btn').forEach(b => b.classList.remove('active'));
    btn.classList.add('active');
    if (btn.dataset.view === 'stream') window.location.href = '/';
    if (btn.dataset.view === 'leaderboard') window.location.href = '/leaderboard';
  });
});

});  // end waitForLibs
</script>
</body>
</html>"""
