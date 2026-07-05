#!/usr/bin/env python3
"""Ares Terminal — CoinMarketCap × DexScreener × BullX hybrid dashboard.
Serves on localhost:8880. Pulls live data from VPS + CoinGecko."""

import json
import subprocess
import time
import os
import urllib.request
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse

VPS = "root@2.25.70.156"
SSH_KEY = os.path.expanduser("~/.ssh/id_ed25519")
SSH = ["ssh", "-i", SSH_KEY, "-o", "StrictHostKeyChecking=no", "-o", "ConnectTimeout=5", VPS]
PORT = 8880
CACHE_TTL = 12  # seconds

_cache = {}
_cache_ts = {}

def ssh(cmd: str) -> dict:
    try:
        r = subprocess.run(SSH + [cmd], capture_output=True, text=True, timeout=10)
        if r.returncode == 0 and r.stdout.strip():
            return json.loads(r.stdout)
    except:
        pass
    return {}

def cached(key, fetcher, ttl=CACHE_TTL):
    now = time.time()
    if key in _cache and (now - _cache_ts.get(key, 0)) < ttl:
        return _cache[key]
    data = fetcher()
    _cache[key] = data
    _cache_ts[key] = now
    return data

# ── Data fetchers ──────────────────────────────────────────────

def fetch_coingecko():
    try:
        url = "https://api.coingecko.com/api/v3/coins/markets?vs_currency=usd&order=market_cap_desc&per_page=250&page=1&sparkline=true&price_change_percentage=1h,24h,7d"
        req = urllib.request.Request(url, headers={"User-Agent": "AresTerminal/2.0"})
        with urllib.request.urlopen(req, timeout=12) as resp:
            return json.loads(resp.read())
    except:
        return []

def fetch_signals():
    raw = ssh("curl -s 'http://localhost:8001/api/intel/signals?limit=75' 2>/dev/null")
    sigs = raw.get("signals", []) if isinstance(raw, dict) else []
    for s in sigs:
        c = s.get("conviction", 0)
        s["direction"] = "BUY" if c >= 4 else ("SELL" if c <= 2 else "NEUTRAL")
        s["conviction_pct"] = min(100, max(0, (c / 10) * 100))
        s["dex_url"] = s.get("url", f"https://dexscreener.com/search?q={s.get('symbol','')}")
    sigs.sort(key=lambda x: x.get("score", x.get("conviction", 0)), reverse=True)
    return sigs

def fetch_freqtrade():
    trades = ssh("curl -s -u ares:aresbot2026 'http://127.0.0.1:9870/api/v1/trades?limit=30' 2>/dev/null")
    profit = ssh("curl -s -u ares:aresbot2026 'http://127.0.0.1:9870/api/v1/profit' 2>/dev/null")
    status = ssh("curl -s -u ares:aresbot2026 'http://127.0.0.1:9870/api/v1/show_config' 2>/dev/null")
    return {"trades": trades, "profit": profit, "status": status}

def fetch_health():
    out = subprocess.run(SSH + ["ps aux | grep -cE 'freqtrade|ares_alpha|ares_radar|ares_rpc|ares_hyperliquid|ares_solana|ares_base|ares_sui|ares_polymarket|ares_autotrade|ares_mev|ares_stop|ares_watchdog|ares_signal|ares_copy|ares_trading' 2>/dev/null && uptime"],
                        capture_output=True, text=True, timeout=8)
    lines = out.stdout.strip().split('\n')
    return {"agents": lines[0].strip() if lines else "?", "uptime": lines[1].strip() if len(lines) > 1 else "?"}

def fetch_all():
    return {
        "market": cached("cg", fetch_coingecko, 30),
        "signals": cached("sig", fetch_signals),
        "freqtrade": cached("ft", fetch_freqtrade),
        "health": cached("hl", fetch_health),
    }

# ═══════════════════════════════════════════════════════════════
# DASHBOARD HTML
# ═══════════════════════════════════════════════════════════════

HTML = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1,user-scalable=no,viewport-fit=cover">
<title>Ares Terminal</title>
<style>
:root {
  --bg: #06060A;
  --surface: #0E0E14;
  --surface2: #161622;
  --border: #1E1E2E;
  --accent: #7C3AED;
  --accent2: #A78BFA;
  --accent3: #C4B5FD;
  --profit: #10B981;
  --loss: #EF4444;
  --warn: #F59E0B;
  --text: #F1F5F9;
  --muted: #94A3B8;
  --dim: #475569;
  --radius: 10px;
  --radius-sm: 6px;
  --font: -apple-system,BlinkMacSystemFont,'Segoe UI','Inter',sans-serif;
  --mono: 'SF Mono','JetBrains Mono','Fira Code',monospace;
}
*{margin:0;padding:0;box-sizing:border-box}
body{
  background:var(--bg);color:var(--text);font-family:var(--font);
  font-size:13px;line-height:1.4;-webkit-font-smoothing:antialiased;
  overflow-x:hidden;padding-bottom:60px;
}

/* ── Ticker Bar ───────────────────────────────────────────── */
.ticker-bar{
  position:sticky;top:0;z-index:20;background:var(--surface2);
  border-bottom:1px solid var(--border);padding:6px 0;
  overflow:hidden;white-space:nowrap;
}
.ticker-track{display:inline-flex;animation:ticker 40s linear infinite}
.ticker-item{display:inline-flex;align-items:center;gap:4px;padding:0 14px;font-size:11px;font-family:var(--mono);white-space:nowrap}
.ticker-item .sym{color:var(--accent2);font-weight:700;font-size:12px}
.ticker-item .price{color:var(--text)}
.ticker-item .chg{font-weight:600}
@keyframes ticker{0%{transform:translateX(0)}100%{transform:translateX(-50%)}}

/* ── Stats Row ────────────────────────────────────────────── */
.stats-row{
  display:grid;grid-template-columns:repeat(4,1fr);gap:6px;
  padding:8px 10px;background:var(--surface);border-bottom:1px solid var(--border);
}
.stat-item{text-align:center;padding:6px 4px}
.stat-item .val{font-size:16px;font-weight:800;font-family:var(--mono);letter-spacing:-0.5px}
.stat-item .lbl{font-size:9px;color:var(--muted);text-transform:uppercase;letter-spacing:0.5px;margin-top:1px}

/* ── Tabs ─────────────────────────────────────────────────── */
.tabs{
  display:flex;gap:0;background:var(--surface);border-bottom:1px solid var(--border);
  position:sticky;top:28px;z-index:15;
}
.tab{
  flex:1;text-align:center;padding:10px 4px;font-size:11px;font-weight:600;
  color:var(--muted);cursor:pointer;border-bottom:2px solid transparent;
  transition:all .15s;text-transform:uppercase;letter-spacing:0.5px;
}
.tab.active{color:var(--accent2);border-bottom-color:var(--accent)}

/* ── Panels ───────────────────────────────────────────────── */
.panel{display:none;padding:8px 10px}
.panel.active{display:block}

/* ── Signal Cards (BullX style) ────────────────────────────── */
.signal-card{
  background:var(--surface);border:1px solid var(--border);border-radius:var(--radius);
  padding:12px;margin-bottom:8px;position:relative;overflow:hidden;
}
.signal-card .glow{
  position:absolute;top:0;left:0;width:3px;height:100%;
  border-radius:3px 0 0 3px;
}
.glow-buy{background:var(--profit)}
.glow-sell{background:var(--loss)}
.glow-neutral{background:var(--dim)}
.signal-top{display:flex;align-items:center;gap:8px;margin-bottom:8px}
.signal-symbol{font-size:16px;font-weight:800;letter-spacing:-0.5px}
.signal-name{font-size:11px;color:var(--muted)}
.signal-badge{
  margin-left:auto;padding:2px 8px;border-radius:99px;font-size:9px;font-weight:700;
  text-transform:uppercase;letter-spacing:0.5px;
}
.badge-buy{background:rgba(16,185,129,0.15);color:var(--profit)}
.badge-sell{background:rgba(239,68,68,0.15);color:var(--loss)}
.badge-neutral{background:rgba(148,163,184,0.1);color:var(--muted)}
.signal-stats{
  display:grid;grid-template-columns:repeat(3,1fr);gap:8px;
  margin-bottom:8px;
}
.signal-stat .ss-val{font-size:13px;font-weight:700;font-family:var(--mono)}
.signal-stat .ss-lbl{font-size:9px;color:var(--dim);text-transform:uppercase;margin-top:1px}
.conviction-bar-wrap{height:4px;border-radius:2px;background:var(--border);overflow:hidden;margin-bottom:4px}
.conviction-bar-fill{height:100%;border-radius:2px;transition:width .4s ease}
.conviction-meta{display:flex;justify-content:space-between;font-size:10px;color:var(--dim)}
.signal-footer{display:flex;gap:6px;margin-top:8px}
.src-tag{
  padding:2px 6px;border-radius:4px;font-size:9px;font-weight:600;
  background:var(--surface2);color:var(--accent2);border:1px solid var(--border);
}
.age-tag{font-size:9px;color:var(--dim);margin-left:auto}
.dex-link{font-size:9px;color:var(--accent);text-decoration:none;margin-left:8px}

/* ── Market Table (CMC style) ──────────────────────────────── */
.mkt-table{width:100%;border-collapse:collapse}
.mkt-table th{
  text-align:left;font-size:9px;color:var(--dim);text-transform:uppercase;
  letter-spacing:0.5px;padding:8px 6px;border-bottom:1px solid var(--border);
  position:sticky;top:0;background:var(--bg);
}
.mkt-table td{padding:8px 6px;border-bottom:1px solid rgba(255,255,255,0.03);font-size:12px}
.mkt-table .rank{color:var(--dim);font-size:11px;width:24px}
.mkt-table .sym{font-weight:700;font-family:var(--mono)}
.mkt-table .name{color:var(--muted);font-size:10px}
.mkt-table .price{font-family:var(--mono);text-align:right}
.mkt-table .chg{text-align:right;font-weight:600;font-family:var(--mono)}
.mkt-table .vol{text-align:right;color:var(--muted);font-size:10px}
.mkt-table .spark{width:80px}
.sparkline{width:80px;height:24px}
.mkt-table tr{cursor:pointer;transition:background .1s}
.mkt-table tr:hover{background:var(--surface)}

/* ── Trades Feed ───────────────────────────────────────────── */
.trade-item{
  display:flex;align-items:center;gap:8px;padding:8px 10px;
  border-bottom:1px solid rgba(255,255,255,0.03);font-size:11px;
}
.trade-pair{font-weight:700;min-width:60px}
.trade-dir{font-weight:600;min-width:36px;font-size:10px}
.trade-dir.buy{color:var(--profit)}
.trade-dir.sell{color:var(--loss)}
.trade-time{color:var(--dim);font-size:9px;margin-left:auto}
.trade-pnl{font-weight:700;font-family:var(--mono);min-width:56px;text-align:right}

/* ── Health ────────────────────────────────────────────────── */
.health-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(130px,1fr));gap:6px}
.health-card{
  background:var(--surface);border:1px solid var(--border);border-radius:var(--radius-sm);
  padding:10px;display:flex;align-items:center;gap:8px;
}
.health-dot{width:8px;height:8px;border-radius:50%;flex-shrink:0}
.health-dot.on{background:var(--profit);box-shadow:0 0 6px rgba(16,185,129,0.4)}
.health-info{flex:1}
.health-name{font-size:11px;font-weight:600}
.health-status{font-size:9px;color:var(--dim)}

/* ── Empty / Error ─────────────────────────────────────────── */
.empty{text-align:center;padding:32px 16px;color:var(--dim);font-size:12px}
.empty-icon{font-size:32px;margin-bottom:8px;opacity:0.4}

/* ── Bottom Nav ────────────────────────────────────────────── */
.bottom-nav{
  position:fixed;bottom:0;left:0;right:0;background:var(--surface2);
  border-top:1px solid var(--border);display:flex;z-index:25;
  padding:4px 0 env(safe-area-inset-bottom,4px);
}
.nav-item{
  flex:1;text-align:center;padding:6px 4px;font-size:9px;color:var(--dim);
  cursor:pointer;transition:color .15s;
}
.nav-item.active{color:var(--accent2)}
.nav-item .nav-icon{font-size:18px;display:block;margin-bottom:1px}

/* ── Chart Panel ───────────────────────────────────────────── */
.chart-container{background:var(--surface);border-radius:var(--radius);overflow:hidden;margin-bottom:8px}
.chart-header{display:flex;align-items:center;gap:8px;padding:10px 12px;border-bottom:1px solid var(--border)}
.chart-pair{font-weight:800;font-size:14px}
.chart-price{font-family:var(--mono);font-size:14px;font-weight:700;margin-left:auto}

/* ── Misc ──────────────────────────────────────────────────── */
.up{color:var(--profit)}.down{color:var(--loss)}.warn{color:var(--warn)}
.green{color:var(--profit)}.red{color:var(--loss)}
.txt-muted{color:var(--muted);font-size:10px}
.txt-dim{color:var(--dim);font-size:9px}
.flex-between{display:flex;justify-content:space-between;align-items:center}
.mt4{margin-top:4px}.mt8{margin-top:8px}.mb8{margin-bottom:8px}
@media(min-width:768px){
  body{max-width:480px;margin:0 auto;border-left:1px solid var(--border);border-right:1px solid var(--border)}
  .ticker-bar,.tabs,.bottom-nav{max-width:480px;left:50%;transform:translateX(-50%);width:100%}
}
</style>
</head>
<body>

<!-- Ticker Bar -->
<div class="ticker-bar"><div class="ticker-track" id="ticker"></div></div>

<!-- Stats Row -->
<div class="stats-row" id="stats"></div>

<!-- Tabs -->
<div class="tabs" id="tabs-nav">
  <div class="tab active" data-panel="signals">Signals</div>
  <div class="tab" data-panel="market">Market</div>
  <div class="tab" data-panel="trades">Trades</div>
  <div class="tab" data-panel="health">Health</div>
</div>

<!-- Panels -->
<div class="panel active" id="panel-signals"></div>
<div class="panel" id="panel-market"></div>
<div class="panel" id="panel-trades"></div>
<div class="panel" id="panel-health"></div>

<!-- Bottom Nav -->
<div class="bottom-nav">
  <div class="nav-item active" data-panel="signals"><span class="nav-icon">⚡</span>Signals</div>
  <div class="nav-item" data-panel="market"><span class="nav-icon">📊</span>Market</div>
  <div class="nav-item" data-panel="trades"><span class="nav-icon">💹</span>Trades</div>
  <div class="nav-item" data-panel="health"><span class="nav-icon">🛡️</span>Health</div>
</div>

<script>
// ── State ────────────────────────────────────────────────────
let lastData = null;
let selectedPair = null;
const API = '/api/data';

// ── Navigation ───────────────────────────────────────────────
document.querySelectorAll('.tab,.nav-item').forEach(el => {
  el.addEventListener('click', () => {
    const panel = el.dataset.panel;
    document.querySelectorAll('.tab,.nav-item').forEach(e => e.classList.remove('active'));
    document.querySelectorAll(`[data-panel="${panel}"]`).forEach(e => e.classList.add('active'));
    document.querySelectorAll('.panel').forEach(p => p.classList.remove('active'));
    document.getElementById('panel-' + panel).classList.add('active');
  });
});

// ── Data Fetch ───────────────────────────────────────────────
async function fetchData() {
  try {
    const r = await fetch(API);
    lastData = await r.json();
    render(lastData);
    document.title = 'Ares Terminal ● Live';
  } catch(e) {
    document.title = 'Ares Terminal ○ Offline';
  }
}

// ── Render ───────────────────────────────────────────────────
function render(d) {
  renderTicker(d);
  renderStats(d);
  renderSignals(d);
  renderMarket(d);
  renderTrades(d);
  renderHealth(d);
}

// ── Ticker ───────────────────────────────────────────────────
function renderTicker(d) {
  const coins = (d.market||[]).slice(0, 50);
  if (!coins.length) return;
  let html = '', html2 = '';
  for (const c of coins) {
    const chg = c.price_change_percentage_24h || 0;
    const cls = chg >= 0 ? 'up' : 'down';
    const sym = (c.symbol||'').toUpperCase();
    const price = c.current_price < 0.01 ? '$' + c.current_price.toFixed(6) : '$' + c.current_price.toLocaleString();
    const item = `<span class="ticker-item"><span class="sym">${sym}</span><span class="price">${price}</span><span class="chg ${cls}">${chg>=0?'+':''}${chg.toFixed(1)}%</span></span>`;
    html += item; html2 += item;
  }
  document.getElementById('ticker').innerHTML = html + html2;
}

// ── Stats ────────────────────────────────────────────────────
function renderStats(d) {
  const sigs = d.signals || [];
  const ft = d.freqtrade || {};
  const profit = ft.profit || {};
  const health = d.health || {};
  const buys = sigs.filter(s => s.direction === 'BUY').length;
  const avgConv = sigs.length ? (sigs.reduce((a,s) => a + (s.conviction||0), 0) / sigs.length).toFixed(1) : '0';

  document.getElementById('stats').innerHTML = `
    <div class="stat-item"><div class="val" style="color:var(--accent2)">${sigs.length}</div><div class="lbl">Signals</div></div>
    <div class="stat-item"><div class="val" style="color:var(--profit)">${buys}</div><div class="lbl">Bullish</div></div>
    <div class="stat-item"><div class="val" style="color:var(--warn)">${avgConv}</div><div class="lbl">Avg Conv</div></div>
    <div class="stat-item"><div class="val" style="color:var(--accent3)">${health.agents||'?'}</div><div class="lbl">Agents</div></div>
  `;
}

// ── Signals (BullX style) ────────────────────────────────────
function renderSignals(d) {
  const sigs = d.signals || [];
  const el = document.getElementById('panel-signals');
  if (!sigs.length) { el.innerHTML = '<div class="empty"><div class="empty-icon">🔍</div>No signals detected</div>'; return; }

  let html = '';
  for (const s of sigs.slice(0, 50)) {
    const conv = s.conviction || 0;
    const cpct = s.conviction_pct || 0;
    const dir = s.direction || 'NEUTRAL';
    const glow = dir === 'BUY' ? 'glow-buy' : dir === 'SELL' ? 'glow-sell' : 'glow-neutral';
    const badge = dir === 'BUY' ? 'badge-buy' : dir === 'SELL' ? 'badge-sell' : 'badge-neutral';
    const price = s.price ? '$' + Number(s.price).toFixed(8) : '—';
    const vol = s.volume_24h ? '$' + Number(s.volume_24h).toLocaleString(undefined,{maximumFractionDigits:0}) : '—';
    const liq = s.liquidity ? '$' + Number(s.liquidity).toLocaleString(undefined,{maximumFractionDigits:0}) : '—';
    const chg6h = s.change_6h;
    const chgCls = chg6h >= 0 ? 'up' : 'down';
    const age = s.age_hours ? (s.age_hours < 24 ? Math.round(s.age_hours)+'h' : Math.round(s.age_hours/24)+'d') : '';
    const score = s.score || conv;
    const barColor = dir === 'BUY' ? 'var(--profit)' : dir === 'SELL' ? 'var(--loss)' : 'var(--accent)';

    html += `<div class="signal-card">
      <div class="glow ${glow}"></div>
      <div class="signal-top">
        <span class="signal-symbol">${s.symbol||'?'}</span>
        <span class="signal-name">${s.name||''}</span>
        <span class="signal-badge ${badge}">${dir}</span>
      </div>
      <div class="signal-stats">
        <div class="signal-stat"><div class="ss-val">${price}</div><div class="ss-lbl">Price</div></div>
        <div class="signal-stat"><div class="ss-val">${vol}</div><div class="ss-lbl">24h Vol</div></div>
        <div class="signal-stat"><div class="ss-val">${liq}</div><div class="ss-lbl">Liquidity</div></div>
      </div>
      <div class="conviction-bar-wrap"><div class="conviction-bar-fill" style="width:${cpct}%;background:${barColor}"></div></div>
      <div class="conviction-meta">
        <span>Conviction ${conv.toFixed(1)}/10 · Score ${score.toFixed(1)}</span>
        ${chg6h !== undefined ? `<span class="${chgCls}">${chg6h>=0?'+':''}${chg6h.toFixed(1)}% 6h</span>` : ''}
      </div>
      <div class="signal-footer">
        <span class="src-tag">${s.source||'radar'}</span>
        ${s.type ? `<span class="src-tag">${s.type}</span>` : ''}
        <span class="age-tag">${age}</span>
        <a class="dex-link" href="${s.dex_url||'#'}" target="_blank">DEX ↗</a>
      </div>
    </div>`;
  }
  el.innerHTML = html;
}

// ── Market Table (CMC style) ─────────────────────────────────
function renderMarket(d) {
  const coins = (d.market||[]).slice(0, 250);
  const el = document.getElementById('panel-market');
  if (!coins.length) { el.innerHTML = '<div class="empty"><div class="empty-icon">📊</div>Loading market data...</div>'; return; }

  let html = `<table class="mkt-table"><thead><tr>
    <th>#</th><th>Token</th><th>Price</th><th>24h</th><th>Volume</th><th>7d</th>
  </tr></thead><tbody>`;

  for (let i = 0; i < coins.length; i++) {
    const c = coins[i];
    const chg24 = c.price_change_percentage_24h || 0;
    const chg7d = c.price_change_percentage_7d_in_currency || 0;
    const vol = c.total_volume ? '$' + (c.total_volume/1e6).toFixed(1) + 'M' : '—';
    const price = c.current_price < 1 ? '$' + c.current_price.toFixed(6) : '$' + c.current_price.toLocaleString();
    const spark = c.sparkline_in_7d?.price || [];
    const sparkSvg = spark.length > 1 ? renderSparkline(spark, chg7d >= 0) : '';

    html += `<tr onclick="window.open('https://dexscreener.com/search?q=${c.symbol}','_blank')">
      <td class="rank">${i+1}</td>
      <td><span class="sym">${(c.symbol||'').toUpperCase()}</span><br><span class="name">${c.name||''}</span></td>
      <td class="price">${price}</td>
      <td class="chg ${chg24>=0?'up':'down'}">${chg24>=0?'+':''}${chg24.toFixed(1)}%</td>
      <td class="vol">${vol}</td>
      <td class="spark">${sparkSvg}</td>
    </tr>`;
  }
  html += '</tbody></table>';
  el.innerHTML = html;
}

function renderSparkline(prices, up) {
  const w = 80, h = 24, pad = 2;
  const min = Math.min(...prices), max = Math.max(...prices);
  const range = max - min || 1;
  let path = '';
  const step = w / (prices.length - 1);
  for (let i = 0; i < prices.length; i++) {
    const x = i * step;
    const y = h - pad - ((prices[i] - min) / range) * (h - pad * 2);
    path += (i === 0 ? 'M' : 'L') + x + ',' + y;
  }
  const color = up ? 'var(--profit)' : 'var(--loss)';
  return `<svg class="sparkline" viewBox="0 0 ${w} ${h}"><path d="${path}" fill="none" stroke="${color}" stroke-width="1.5" stroke-linecap="round"/></svg>`;
}

// ── Trades ───────────────────────────────────────────────────
function renderTrades(d) {
  const ft = d.freqtrade || {};
  const trades = (ft.trades||{}).trades || [];
  const profit = ft.profit || {};
  const el = document.getElementById('panel-trades');

  let html = `<div class="stats-row" style="margin-bottom:8px">
    <div class="stat-item"><div class="val" style="color:var(--text)">${profit.trade_count||0}</div><div class="lbl">Total Trades</div></div>
    <div class="stat-item"><div class="val" style="color:var(--profit)">${profit.winning_trades||0}</div><div class="lbl">Wins</div></div>
    <div class="stat-item"><div class="val" style="color:var(--loss)">${profit.losing_trades||0}</div><div class="lbl">Losses</div></div>
    <div class="stat-item"><div class="val" style="color:var(--warn)">${profit.max_drawdown_percent?Number(profit.max_drawdown_percent).toFixed(1)+'%':'—'}</div><div class="lbl">Max DD</div></div>
  </div>`;

  if (!trades.length) {
    html += '<div class="empty"><div class="empty-icon">💹</div>No trades executed yet<br><span class="txt-muted">Waiting for entry signals — sentiment is bearish</span></div>';
  } else {
    for (const t of trades.slice(0, 30)) {
      const pnl = t.close_profit || t.close_profit_abs || 0;
      const pnlPct = typeof pnl === 'number' && pnl < 1 ? (pnl*100).toFixed(2)+'%' : (typeof pnl === 'number' ? pnl.toFixed(4) : '—');
      const cls = (t.close_profit||0) >= 0 ? 'buy' : 'sell';
      html += `<div class="trade-item">
        <span class="trade-pair">${t.pair||'?'}</span>
        <span class="trade-dir ${cls}">${t.is_short?'SHORT':'LONG'}</span>
        <span style="color:var(--muted);font-size:10px">${t.open_date||''}</span>
        <span class="trade-time">${t.close_date||'open'}</span>
        <span class="trade-pnl ${(t.close_profit||0)>=0?'up':'down'}">${pnlPct}</span>
      </div>`;
    }
  }
  el.innerHTML = html;
}

// ── Health ───────────────────────────────────────────────────
function renderHealth(d) {
  const h = d.health || {};
  const el = document.getElementById('panel-health');
  const agents = [
    'freqtrade','alpha_feed','radar','rpc_proxy','hyperliquid','solana','base','sui',
    'polymarket','autotrade','mev_shield','stop_loss','watchdog','signal_agg','copy_trader'
  ];
  const count = parseInt(h.agents) || 0;

  let html = `<div class="stat-item" style="text-align:center;margin-bottom:10px">
    <div class="val" style="font-size:28px;color:var(--accent2)">${h.agents||'?'}</div>
    <div class="lbl">agents running</div>
  </div>`;

  html += '<div class="health-grid">';
  for (const a of agents) {
    const on = count > 10; // rough: if >10 total, assume individual agents up
    html += `<div class="health-card">
      <div class="health-dot ${on?'on':''}"></div>
      <div class="health-info">
        <div class="health-name">${a.replace(/_/g,' ')}</div>
        <div class="health-status">${on?'running':'—'}</div>
      </div>
    </div>`;
  }
  html += '</div>';

  html += `<div class="txt-dim mt8" style="text-align:center">VPS: ${h.uptime||'?'}</div>`;
  el.innerHTML = html;
}

// ── Init ─────────────────────────────────────────────────────
fetchData();
setInterval(fetchData, 12000);
</script>
</body>
</html>"""

# ═══════════════════════════════════════════════════════════════
# HTTP Server
# ═══════════════════════════════════════════════════════════════

class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        path = urlparse(self.path).path
        if path == "/" or path == "/index.html":
            self._serve(HTML, "text/html; charset=utf-8")
        elif path == "/api/data":
            self._serve(json.dumps(fetch_all(), default=str), "application/json")
        elif path == "/api/health":
            self._serve('{"status":"ok"}', "application/json")
        else:
            self.send_response(404); self.end_headers()

    def _serve(self, content, mime):
        self.send_response(200)
        self.send_header("Content-Type", mime)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(content.encode() if isinstance(content, str) else content)

    def log_message(self, format, *args):
        pass

if __name__ == "__main__":
    print(f"\n  ⚡ ARES TERMINAL — http://localhost:{PORT}\n")
    HTTPServer(("0.0.0.0", PORT), Handler).serve_forever()
