#!/usr/bin/env python3
"""
Julia Poller — Bridges VPS Julia engine alerts into LOOM.
Polls :8895 every 15s. Injects anomalies into the fabric event stream.
"""

import json
import time
import threading
import urllib.request

JULIA_API = "http://2.25.70.156:8895"


class JuliaPoller:
    """Polls Julia graph engine for alerts and state."""

    def __init__(self, api_url=JULIA_API):
        self.api = api_url.rstrip("/")
        self.latest_alerts = []
        self.latest_state = {}
        self.connected = False
        self._check()

    def _check(self):
        try:
            r = self._get("/api/health")
            self.connected = r.get("status") == "ok"
            if self.connected:
                print(f"  [julia] connected to VPS graph engine")
        except Exception as e:
            print(f"  [julia] VPS unreachable: {e}")
            self.connected = False

    def _get(self, path):
        req = urllib.request.Request(f"{self.api}{path}",
            headers={"User-Agent": "LOOM-JuliaPoller/1.0"})
        with urllib.request.urlopen(req, timeout=5) as resp:
            return json.loads(resp.read())

    def fetch_alerts(self) -> list:
        """Fetch latest anomalies from Julia engine."""
        if not self.connected:
            return []
        try:
            alerts = self._get("/api/alerts")
            self.latest_alerts = alerts
            return alerts
        except Exception:
            return []

    def fetch_state(self) -> dict:
        """Fetch graph engine state."""
        if not self.connected:
            return {}
        try:
            self.latest_state = self._get("/api/state")
            return self.latest_state
        except Exception:
            return {}

    def fetch_subgraph(self) -> dict:
        """Fetch sub-graph for 3D visualization."""
        try:
            return self._get("/api/subgraph")
        except Exception:
            return {"nodes": [], "links": []}

    def poll_loop(self, fabric=None, whales=None, interval=15):
        """Continuous polling loop — injects alerts into LOOM fabric."""
        while True:
            try:
                alerts = self.fetch_alerts()
                state = self.fetch_state()

                # Inject Julia anomalies as fabric events
                if fabric and alerts:
                    for alert in alerts:
                        fabric.ingest(
                            event_type="julia_anomaly",
                            entity=alert.get("token", alert.get("address", "?")),
                            magnitude=alert.get("tagged_wallets", 1) / 10.0,
                            source="julia_engine",
                            metadata={
                                "alert_type": alert.get("type"),
                                "severity": alert.get("severity"),
                                "message": alert.get("message"),
                            },
                        )

            except Exception as e:
                print(f"  [julia] poll error: {e}")

            time.sleep(interval)


# Global instance
julia = JuliaPoller()
