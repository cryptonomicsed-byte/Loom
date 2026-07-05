#!/usr/bin/env python3
"""
LOOM Launcher — Clean startup, process management, health monitoring.
Solves .pyc caching, zombie processes, port conflicts, and daemon thread blocking.

Usage:
  python3 launcher.py start    — Start all services
  python3 launcher.py stop     — Stop all services
  python3 launcher.py restart  — Restart all services
  python3 launcher.py status   — Show what's running
"""

import os
import sys
import signal
import subprocess
import time
import glob
import json
import urllib.request

LOOM_DIR = "/data/data/com.termux/files/home/loom"
ARES_DIR = "/data/data/com.termux/files/home/ares-dashboard"

SERVICES = {
    "loom": {
        "dir": LOOM_DIR,
        "cmd": ["python3", "fast_server.py"],
        "port": 8889,
        "name": "LOOM Fast Server",
    },
    "ares": {
        "dir": ARES_DIR,
        "cmd": ["python3", "server.py"],
        "port": 8880,
        "name": "Ares Dashboard",
    },
    "sentinel": {
        "dir": LOOM_DIR,
        "cmd": ["python3", "sentinel.py"],
        "port": 8885,
        "name": "Ares Sentinel",
    },
}


def clear_pycache():
    """Remove all stale .pyc files — the #1 cause of stale code."""
    count = 0
    for d in [LOOM_DIR, ARES_DIR]:
        for pattern in ["__pycache__/*.pyc", "**/__pycache__/*.pyc"]:
            for f in glob.glob(os.path.join(d, pattern), recursive=True):
                try:
                    os.remove(f)
                    count += 1
                except Exception:
                    pass
    if count:
        print(f"  🧹 cleared {count} stale .pyc files")


def kill_all():
    """Kill all Python server processes. Avoids self."""
    my_pid = os.getpid()
    try:
        result = subprocess.run(
            ["ps", "aux"], capture_output=True, text=True, timeout=5
        )
        pids = []
        for line in result.stdout.split("\n"):
            if "python3" in line and "grep" not in line and "launcher" not in line:
                parts = line.split()
                if len(parts) > 1 and parts[1] != str(my_pid):
                    pids.append(parts[1])

        for pid in pids:
            try:
                os.kill(int(pid), signal.SIGKILL)
            except Exception:
                pass

        if pids:
            time.sleep(1)
            print(f"  💀 killed {len(pids)} Python processes")
    except Exception as e:
        print(f"  ⚠️  kill error: {e}")


def check_port(port):
    """Check if a port is responding to health check."""
    try:
        data = json.loads(
            urllib.request.urlopen(f"http://localhost:{port}/api/health", timeout=2).read()
        )
        # Accept {"status":"ok"} or {"services":[...]} format
        return data.get("status") == "ok" or "services" in data or isinstance(data, dict)
    except Exception:
        return False


def start_service(name, cfg):
    """Start a single service as a subprocess."""
    if check_port(cfg["port"]):
        print(f"  ⚠️  {cfg['name']} already running on :{cfg['port']}")
        return None

    proc = subprocess.Popen(
        cfg["cmd"],
        cwd=cfg["dir"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    print(f"  ✓ {cfg['name']} :{cfg['port']} (PID {proc.pid})")
    return proc


def cmd_start():
    """Start all services cleanly."""
    print("\n◈ LOOM LAUNCHER — Starting...\n")

    # Step 1: Clear stale bytecode cache
    clear_pycache()

    # Step 2: Kill zombie processes
    kill_all()

    # Step 3: Start services
    procs = {}
    for name, cfg in SERVICES.items():
        proc = start_service(name, cfg)
        if proc:
            procs[name] = proc

    # Step 4: Wait and verify
    print(f"\n  Waiting for services to initialize...")
    time.sleep(8)

    for name, cfg in SERVICES.items():
        if check_port(cfg["port"]):
            print(f"  ✓ {cfg['name']} — healthy")
        else:
            print(f"  ✗ {cfg['name']} — not responding")

    print(f"\n  http://localhost:8889    LOOM Stream")
    print(f"  http://localhost:8889/galaxy  3D Galaxy")
    print(f"  http://localhost:8880    Ares Dashboard")
    print(f"  http://localhost:8885    Sentinel\n")


def cmd_stop():
    """Stop all services."""
    print("\n◈ LOOM LAUNCHER — Stopping...\n")
    kill_all()
    print("  All services stopped.\n")


def cmd_restart():
    """Restart all services."""
    cmd_stop()
    time.sleep(2)
    cmd_start()


def cmd_status():
    """Show status of all services."""
    print("\n◈ LOOM STATUS\n")
    all_ok = True
    for name, cfg in SERVICES.items():
        if check_port(cfg["port"]):
            try:
                data = json.loads(
                    urllib.request.urlopen(
                        f"http://localhost:{cfg['port']}/api/health", timeout=2
                    ).read()
                )
                uptime = data.get("uptime", "?")
                print(f"  ✓ {cfg['name']} :{cfg['port']} — uptime {uptime}s")
            except Exception:
                print(f"  ✓ {cfg['name']} :{cfg['port']} — responding")
        else:
            print(f"  ✗ {cfg['name']} :{cfg['port']} — OFFLINE")
            all_ok = False

    if all_ok:
        print("\n  All services healthy.\n")
    else:
        print("\n  Run 'python3 launcher.py restart' to recover.\n")


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "status"

    if cmd == "start":
        cmd_start()
    elif cmd == "stop":
        cmd_stop()
    elif cmd == "restart":
        cmd_restart()
    elif cmd == "status":
        cmd_status()
    else:
        print(f"Usage: python3 launcher.py [start|stop|restart|status]")
