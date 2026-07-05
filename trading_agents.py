#!/usr/bin/env python3
"""
LOOM Trading Agents — 5 autonomous agents with distinct strategies.

Each agent:
  - Has its own wallet address
  - Has a unique risk/reward profile
  - Queries LOOM consensus for signals
  - Filters signals through its strategy lens
  - Tracks positions, P&L, win rate independently

All local. No API costs.

Strategies:
  1. FLIPPER   — 100% target, full risk. Memecoin degen.
  2. SCALPER   — 5% gain, 3% loss. Conservative hit-and-run.
  3. MODERATE  — 8% gain, 3% loss. Better risk/reward.
  4. DCA_MOON  — DCA out on signals, keep 10% moon bag.
  5. BOND_RIDER — Fresh pump.fun launches, sell 50% at 2x, ride rest to bond.
"""

import json
import time
import threading
import os
import sys
from dataclasses import dataclass, field
from typing import Optional, List, Dict, Tuple
from enum import Enum
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


# ═══════════════════════════════════════════════════════════════
# AGENT PROFILES
# ═══════════════════════════════════════════════════════════════

@dataclass
class AgentProfile:
    name: str
    wallet: str           # Solana wallet address
    strategy: str
    target_gain_pct: float    # Take profit at this %
    stop_loss_pct: float      # Stop loss at this %
    max_position_sol: float   # Max SOL per trade
    max_positions: int        # Max concurrent positions
    min_conviction: float     # Minimum signal conviction to enter
    cooldown_seconds: int     # Min time between trades
    description: str
    enabled: bool = True

    # Strategy-specific
    dca_out_pct: float = 0.0        # % to sell at each DCA step
    moon_bag_pct: float = 0.0       # % to keep as moon bag
    sell_half_at_2x: bool = False   # Sell 50% when position doubles
    pump_fun_only: bool = False     # Only trade pump.fun tokens
    require_cluster: bool = False   # Only trade if whale cluster detected


# ── Pre-defined agents ────────────────────────────────────────

AGENTS = [
    AgentProfile(
        name="FLIPPER",
        wallet="WALLET_1_HERE",
        strategy="100% flips — all or nothing",
        target_gain_pct=100.0,
        stop_loss_pct=-50.0,    # Wide stop, ride or die
        max_position_sol=10.0,
        max_positions=3,
        min_conviction=0.5,
        cooldown_seconds=600,   # 10 min between flips
        description="Degen flipper. Goes for doubles. Accepts 50% loss. High risk, high reward.",
    ),
    AgentProfile(
        name="SCALPER",
        wallet="WALLET_2_HERE",
        strategy="5% gain / 3% loss — conservative scalping",
        target_gain_pct=5.0,
        stop_loss_pct=-3.0,
        max_position_sol=5.0,
        max_positions=5,
        min_conviction=0.4,
        cooldown_seconds=120,
        description="Conservative scalper. Small gains, tight stops, high frequency.",
    ),
    AgentProfile(
        name="MODERATE",
        wallet="WALLET_3_HERE",
        strategy="8% gain / 3% loss — better risk/reward",
        target_gain_pct=8.0,
        stop_loss_pct=-3.0,
        max_position_sol=8.0,
        max_positions=4,
        min_conviction=0.5,
        cooldown_seconds=180,
        description="Balanced trader. 2.6:1 reward/risk. Moderate frequency.",
    ),
    AgentProfile(
        name="DCA_MOON",
        wallet="WALLET_4_HERE",
        strategy="DCA out on signals, keep moon bag",
        target_gain_pct=20.0,      # First DCA trigger
        stop_loss_pct=-10.0,
        max_position_sol=6.0,
        max_positions=3,
        min_conviction=0.55,
        cooldown_seconds=300,
        dca_out_pct=30.0,          # Sell 30% at each DCA step
        moon_bag_pct=10.0,         # Keep 10% forever
        description="Patient accumulator. Scales out on gains, keeps skin in the game.",
    ),
    AgentProfile(
        name="BOND_RIDER",
        wallet="WALLET_5_HERE",
        strategy="Fresh launches — sell 50% at 2x, ride rest to bond",
        target_gain_pct=100.0,     # For the sold half
        stop_loss_pct=-15.0,
        max_position_sol=3.0,      # Small entries on new launches
        max_positions=8,
        min_conviction=0.6,
        cooldown_seconds=60,       # Fast on new launches
        sell_half_at_2x=True,
        pump_fun_only=True,
        require_cluster=True,
        description="Launch sniper. Gets in first on pump.fun, recovers cost at 2x, rides moon bag to bond.",
    ),
]


# ═══════════════════════════════════════════════════════════════
# POSITION TRACKER
# ═══════════════════════════════════════════════════════════════

@dataclass
class Position:
    token: str
    token_address: str
    entry_price_sol: float
    entry_amount_sol: float
    entry_time: float
    current_price_sol: float = 0.0
    status: str = "open"       # open, dca_out, moon_bag, closed
    sold_amount: float = 0.0
    sold_profit_sol: float = 0.0
    dca_steps: int = 0
    moon_bag_amount: float = 0.0
    target_hit: bool = False
    stop_hit: bool = False
    bond_complete: bool = False

    @property
    def current_value_sol(self) -> float:
        remaining = self.entry_amount_sol - self.sold_amount - self.moon_bag_amount
        if remaining <= 0:
            return 0
        if self.current_price_sol > 0 and self.entry_price_sol > 0:
            return remaining * (self.current_price_sol / self.entry_price_sol)
        return remaining

    @property
    def unrealized_pnl_sol(self) -> float:
        remaining = self.entry_amount_sol - self.sold_amount - self.moon_bag_amount
        if remaining <= 0:
            return 0
        if self.entry_price_sol > 0:
            return remaining * (self.current_price_sol / self.entry_price_sol - 1)
        return 0

    @property
    def total_pnl_sol(self) -> float:
        return self.sold_profit_sol + self.unrealized_pnl_sol


# ═══════════════════════════════════════════════════════════════
# TRADING AGENT
# ═══════════════════════════════════════════════════════════════

class TradingAgent:
    """Autonomous agent that trades according to its profile."""

    def __init__(self, profile: AgentProfile):
        self.profile = profile
        self.positions: List[Position] = []
        self.closed_positions: List[Position] = []
        self.last_trade_time: float = 0
        self.total_trades: int = 0
        self.winning_trades: int = 0
        self.total_pnl_sol: float = 0.0
        self._lock = threading.Lock()

    # ── Core Logic ────────────────────────────────────────────

    def should_enter(self, signal: dict) -> Tuple[bool, str]:
        """Decide whether to enter based on signal and profile."""
        now = time.time()

        # Cooldown check
        if now - self.last_trade_time < self.profile.cooldown_seconds:
            return False, "cooldown"

        # Position limit
        open_positions = [p for p in self.positions if p.status == "open"]
        if len(open_positions) >= self.profile.max_positions:
            return False, f"max_positions ({len(open_positions)}/{self.profile.max_positions})"

        # Conviction check
        consensus = signal.get("consensus", {})
        conviction = consensus.get("conviction", 0)
        if conviction < self.profile.min_conviction:
            return False, f"low_conviction ({conviction:.2f} < {self.profile.min_conviction})"

        # Veto check
        if consensus.get("vetoed"):
            return False, "vetoed"

        # Direction check
        direction = consensus.get("direction", "WAIT")
        if direction not in ("BUY",):
            return False, f"direction={direction}"

        # Pump.fun requirement
        if self.profile.pump_fun_only:
            token = signal.get("symbol", "")
            # Check if token looks like a pump.fun address pattern
            if not any(x in str(token).lower() for x in ["pump", "raydium"]):
                return False, "not_pumpfun"

        # Cluster requirement (BOND_RIDER)
        if self.profile.require_cluster:
            agents = signal.get("agents", {})
            whale = agents.get("whale", {})
            if whale.get("conviction", 0) < 0.6:
                return False, "no_whale_cluster"

        return True, "enter"

    def should_exit(self, position: Position, signal: dict = None) -> Tuple[bool, str]:
        """Check if position should be exited based on profile rules."""
        if position.status != "open":
            return False, "already closed"

        if position.current_price_sol <= 0 or position.entry_price_sol <= 0:
            return False, "no price data"

        pnl_pct = ((position.current_price_sol - position.entry_price_sol)
                    / position.entry_price_sol * 100)

        # === BOND_RIDER: Sell half at 2x ===
        if self.profile.sell_half_at_2x and pnl_pct >= 100 and position.sold_amount == 0:
            return True, "sell_half_at_2x"

        # === DCA_MOON: Step out at target gains ===
        if self.profile.dca_out_pct > 0:
            dca_target = self.profile.target_gain_pct * (position.dca_steps + 1)
            if position.dca_steps < 3 and pnl_pct >= dca_target:
                return True, f"dca_step_{position.dca_steps + 1}"

        # === Stop loss ===
        if pnl_pct <= self.profile.stop_loss_pct:
            return True, f"stop_loss ({pnl_pct:.1f}%)"

        # === Take profit (non-DCA agents) ===
        if self.profile.dca_out_pct == 0 and not self.profile.sell_half_at_2x:
            if pnl_pct >= self.profile.target_gain_pct:
                return True, f"take_profit ({pnl_pct:.1f}%)"

        # === Signal-based exit ===
        if signal:
            consensus = signal.get("consensus", {})
            if consensus.get("direction") == "SELL" and consensus.get("conviction", 0) > 0.5:
                return True, "signal_sell"

        return False, "hold"

    def enter_position(self, token: str, token_address: str,
                       entry_sol: float, entry_price_sol: float = 0) -> Position:
        """Open a new position."""
        with self._lock:
            amount = min(entry_sol, self.profile.max_position_sol)
            pos = Position(
                token=token,
                token_address=token_address,
                entry_price_sol=entry_price_sol or amount,  # 1:1 if no price data
                entry_amount_sol=amount,
                entry_time=time.time(),
                status="open",
            )
            self.positions.append(pos)
            self.last_trade_time = time.time()
            self.total_trades += 1
            return pos

    def exit_position(self, position: Position, reason: str,
                      exit_amount_sol: float = None):
        """Close or partially close a position."""
        with self._lock:
            if position.status == "closed":
                return

            pnl_pct = ((position.current_price_sol - position.entry_price_sol)
                        / position.entry_price_sol * 100) if position.entry_price_sol > 0 else 0

            # BOND_RIDER partial exit
            if reason == "sell_half_at_2x" and self.profile.sell_half_at_2x:
                half = position.entry_amount_sol / 2
                profit = half * (position.current_price_sol / position.entry_price_sol - 1)
                position.sold_amount = half
                position.sold_profit_sol = half + profit  # Recovered cost + profit
                position.target_hit = True
                # Remaining becomes moon bag
                position.moon_bag_amount = position.entry_amount_sol - half
                position.status = "moon_bag"
                self.total_pnl_sol += profit
                if profit > 0:
                    self.winning_trades += 1

            # DCA out
            elif reason.startswith("dca_step"):
                dca_amount = position.entry_amount_sol * (self.profile.dca_out_pct / 100)
                remaining = position.entry_amount_sol - position.sold_amount - position.moon_bag_amount
                if remaining <= 0:
                    return
                sell_amount = min(dca_amount, remaining)
                profit = sell_amount * (position.current_price_sol / position.entry_price_sol - 1)
                position.sold_amount += sell_amount
                position.sold_profit_sol += profit
                position.dca_steps += 1
                self.total_pnl_sol += profit

                # Last DCA step = moon bag time
                remaining_after = position.entry_amount_sol - position.sold_amount
                if remaining_after <= position.entry_amount_sol * (self.profile.moon_bag_pct / 100):
                    position.moon_bag_amount = remaining_after
                    position.status = "moon_bag"
                    position.sold_amount += remaining_after

            # Full exit
            else:
                remaining = position.entry_amount_sol - position.sold_amount
                if remaining > 0 and position.entry_price_sol > 0:
                    final_value = remaining * (position.current_price_sol / position.entry_price_sol)
                    profit = final_value - remaining
                    position.sold_profit_sol += profit
                    self.total_pnl_sol += profit

                position.status = "closed"
                if reason.startswith("stop_loss"):
                    position.stop_hit = True
                if reason.startswith("take_profit"):
                    position.target_hit = True

                if position.total_pnl_sol > 0:
                    self.winning_trades += 1
                self.closed_positions.append(position)

    def update_positions(self, token_prices: Dict[str, float]):
        """Update current prices for all open positions."""
        for pos in self.positions:
            if pos.status in ("open", "moon_bag"):
                if pos.token in token_prices:
                    pos.current_price_sol = token_prices[pos.token]
                elif pos.token_address in token_prices:
                    pos.current_price_sol = token_prices[pos.token_address]

    def get_stats(self) -> dict:
        """Get agent performance stats."""
        open_pos = [p for p in self.positions if p.status == "open"]
        moon_bags = [p for p in self.positions if p.status == "moon_bag"]
        closed = self.closed_positions
        wr = (self.winning_trades / max(1, len(closed))) * 100 if closed else 0

        return {
            "agent": self.profile.name,
            "strategy": self.profile.strategy,
            "wallet": self.profile.wallet[:8] + "...",
            "total_trades": self.total_trades,
            "open_positions": len(open_pos),
            "moon_bags": len(moon_bags),
            "closed_positions": len(closed),
            "win_rate": round(wr, 1),
            "total_pnl_sol": round(self.total_pnl_sol, 4),
            "unrealized_pnl": round(sum(p.unrealized_pnl_sol for p in self.positions), 4),
            "cooldown_remaining": max(0, int(self.profile.cooldown_seconds -
                                       (time.time() - self.last_trade_time))),
        }


# ═══════════════════════════════════════════════════════════════
# AGENT MANAGER
# ═══════════════════════════════════════════════════════════════

class AgentManager:
    """Runs all trading agents concurrently, feeding them LOOM signals."""

    def __init__(self, loom_url: str = "http://localhost:8889"):
        self.loom_url = loom_url.rstrip("/")
        self.agents: Dict[str, TradingAgent] = {}
        self.running = False
        self._thread: Optional[threading.Thread] = None
        self.signal_history: list = []

        # Initialize agents
        for profile in AGENTS:
            self.agents[profile.name] = TradingAgent(profile)

    def get_signal(self, symbol: str = "SOL/USDC") -> dict:
        """Fetch latest multi-agent consensus from LOOM."""
        try:
            import urllib.request
            url = f"{self.loom_url}/api/decide?symbol={symbol}"
            req = urllib.request.Request(url, headers={"User-Agent": "LOOM-AgentManager/1.0"})
            with urllib.request.urlopen(req, timeout=5) as resp:
                return json.loads(resp.read())
        except Exception:
            return {"consensus": {"direction": "WAIT", "conviction": 0, "vetoed": False}}

    def run_cycle(self):
        """One cycle: get signal → each agent decides → execute."""
        signal = self.get_signal()
        if not signal:
            return

        self.signal_history.append(signal)
        if len(self.signal_history) > 100:
            self.signal_history = self.signal_history[-50:]

        consensus = signal.get("consensus", {})
        direction = consensus.get("direction", "WAIT")
        conviction = consensus.get("conviction", 0)

        for name, agent in self.agents.items():
            if not agent.profile.enabled:
                continue

            profile = agent.profile
            symbol = signal.get("symbol", "UNKNOWN")

            # === Check entries ===
            should_enter, reason = agent.should_enter(signal)
            if should_enter:
                pos = agent.enter_position(
                    token=symbol,
                    token_address=symbol,
                    entry_sol=profile.max_position_sol,
                )
                print(f"  [{name}] ENTER {symbol} ({profile.max_position_sol} SOL) — {reason}")

            # === Check exits ===
            for pos in list(agent.positions):
                if pos.status not in ("open",):
                    continue
                should_exit, exit_reason = agent.should_exit(pos, signal)
                if should_exit:
                    agent.exit_position(pos, exit_reason)
                    print(f"  [{name}] EXIT {pos.token} — {exit_reason}")

    def start(self, interval: int = 60):
        """Start autonomous trading loop."""
        self.running = True
        print(f"[AgentManager] Starting {len(self.agents)} agents, cycle every {interval}s")

        def loop():
            while self.running:
                try:
                    self.run_cycle()
                except Exception as e:
                    print(f"  [AgentManager] error: {e}")
                time.sleep(interval)

        self._thread = threading.Thread(target=loop, daemon=True)
        self._thread.start()

    def stop(self):
        self.running = False

    def get_all_stats(self) -> dict:
        """Get stats for all agents."""
        return {
            "agents": {name: agent.get_stats() for name, agent in self.agents.items()},
            "signal": self.signal_history[-1] if self.signal_history else None,
        }


# ═══════════════════════════════════════════════════════════════
# Quick test
# ═══════════════════════════════════════════════════════════════

if __name__ == "__main__":
    mgr = AgentManager()

    print("=" * 55)
    print("  LOOM TRADING AGENTS")
    print("=" * 55)
    for name, agent in mgr.agents.items():
        p = agent.profile
        print(f"\n  [{name}]")
        print(f"    Strategy:  {p.strategy}")
        print(f"    Target:    +{p.target_gain_pct}% / {p.stop_loss_pct}%")
        print(f"    Max Size:  {p.max_position_sol} SOL")
        print(f"    Min Conv:  {p.min_conviction}")
        extra = []
        if p.dca_out_pct: extra.append(f"DCA {p.dca_out_pct}%")
        if p.moon_bag_pct: extra.append(f"Moon bag {p.moon_bag_pct}%")
        if p.sell_half_at_2x: extra.append("Sell half @ 2x")
        if p.pump_fun_only: extra.append("Pump.fun only")
        if p.require_cluster: extra.append("Requires cluster")
        if extra:
            print(f"    Special:   {', '.join(extra)}")

    print(f"\n  Ready. Replace WALLET_X_HERE with real addresses.")
    print(f"  Start with: mgr.start(interval=60)")
