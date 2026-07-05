"""
LOOM Data Sources — pulls from VPS (Ares daemons) + CoinGecko.
All data stays local on the phone. VPS accessed via SSH.
"""

import json
import subprocess
import time
import urllib.request
import threading
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from fabric import fabric

VPS = "root@2.25.70.156"
SSH_KEY = "/data/data/com.termux/files/home/.ssh/id_ed25519"
SSH = ["ssh", "-i", SSH_KEY, "-o", "StrictHostKeyChecking=no", "-o", "ConnectTimeout=5", VPS]


def ssh_json(cmd: str) -> dict:
    try:
        r = subprocess.run(SSH + [cmd], capture_output=True, text=True, timeout=10)
        if r.returncode == 0 and r.stdout.strip():
            return json.loads(r.stdout)
    except Exception:
        pass
    return {}


def pull_ares_signals():
    raw = ssh_json("curl -s 'http://localhost:8001/api/intel/signals?limit=50' 2>/dev/null")
    signals = raw.get("signals", [])
    count = 0
    for s in signals:
        try:
            fabric.ingest_signal(s)
            count += 1
        except Exception:
            pass
    if count:
        print(f"  [signals] +{count}")
    return count


def pull_freqtrade():
    raw = ssh_json(
        "curl -s -u ares:aresbot2026 'http://127.0.0.1:9870/api/v1/trades?limit=10' 2>/dev/null"
    )
    trades = raw.get("trades", [])
    count = 0
    for t in trades:
        if t.get("close_date"):
            try:
                fabric.ingest_trade(t)
                count += 1
            except Exception:
                pass
    if count:
        print(f"  [trades] +{count}")
    return count


def pull_coingecko():
    try:
        url = (
            "https://api.coingecko.com/api/v3/coins/markets"
            "?vs_currency=usd&order=market_cap_desc&per_page=100&page=1"
            "&sparkline=false&price_change_percentage=1h,24h,7d"
        )
        req = urllib.request.Request(url, headers={"User-Agent": "LOOM/1.0"})
        with urllib.request.urlopen(req, timeout=15) as resp:
            coins = json.loads(resp.read())
    except Exception:
        return 0

    count = 0
    for coin in coins:
        try:
            fabric.ingest_price_move(coin)
            count += 1
        except Exception:
            pass
    if count:
        print(f"  [coingecko] +{count}")
    return count


def ingest_loop():
    print("[LOOM] ingestion started")
    last = {"signals": 0, "coingecko": 0, "trades": 0}
    interval = {"signals": 12, "coingecko": 30, "trades": 15}

    first = True
    while True:
        now = time.time()
        if first or now - last["signals"] >= interval["signals"]:
            pull_ares_signals()
            last["signals"] = now
        if first or now - last["coingecko"] >= interval["coingecko"]:
            pull_coingecko()
            last["coingecko"] = now
        if first or now - last["trades"] >= interval["trades"]:
            pull_freqtrade()
            last["trades"] = now
        first = False
        time.sleep(2)


def start_ingestion():
    t = threading.Thread(target=ingest_loop, daemon=True)
    t.start()
    return t
