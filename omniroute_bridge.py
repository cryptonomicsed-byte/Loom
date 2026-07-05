#!/usr/bin/env python3
"""
OmniRoute Bridge — Free LLM access for LOOM agents.

Routes LOOM's agent reasoning through OmniRoute's free providers.
No API keys. No costs. Auto-fallback between providers.

OmniRoute runs locally at :20128 (OpenAI-compatible API).
"""

import json
import urllib.request
import time
import threading
from typing import Optional

OMNIROUTE_URL = "http://localhost:20128/v1"
DEFAULT_MODEL = "if/kimi-k2"  # Free, unlimited via Qoder
FALLBACK_MODELS = ["if/qwen3-coder-plus", "if/deepseek-v3.2"]


class OmniRouteBridge:
    """Thin bridge to OmniRoute's free LLM gateway."""

    def __init__(self, base_url: str = OMNIROUTE_URL, model: str = DEFAULT_MODEL):
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.fallbacks = FALLBACK_MODELS
        self.available = False
        self._check_health()

    def _check_health(self) -> bool:
        try:
            req = urllib.request.Request(f"{self.base_url}/models", headers={
                "User-Agent": "LOOM-OmniRoute/1.0",
            })
            with urllib.request.urlopen(req, timeout=3) as resp:
                data = json.loads(resp.read())
                self.available = "data" in data or "object" in data
                if self.available:
                    print(f"  [omniroute] connected — {len(data.get('data',[]))} models available")
                return self.available
        except Exception as e:
            print(f"  [omniroute] not available: {e}")
            self.available = False
            return False

    def chat(self, system: str, prompt: str, model: str = None,
             max_tokens: int = 500, temperature: float = 0.3) -> str:
        """Send a chat completion request.

        Returns the LLM response text, or falls back to a simple template if
        OmniRoute is unavailable.
        """
        model = model or self.model

        if not self.available:
            return self._template_fallback(system, prompt)

        for attempt_model in [model] + self.fallbacks:
            try:
                body = json.dumps({
                    "model": attempt_model,
                    "messages": [
                        {"role": "system", "content": system},
                        {"role": "user", "content": prompt},
                    ],
                    "max_tokens": max_tokens,
                    "temperature": temperature,
                }).encode()

                req = urllib.request.Request(
                    f"{self.base_url}/chat/completions",
                    data=body,
                    headers={
                        "Content-Type": "application/json",
                        "User-Agent": "LOOM-OmniRoute/1.0",
                    },
                    method="POST",
                )
                with urllib.request.urlopen(req, timeout=15) as resp:
                    result = json.loads(resp.read())
                    content = result["choices"][0]["message"]["content"]
                    return content.strip()

            except Exception as e:
                if attempt_model == self.fallbacks[-1]:
                    print(f"  [omniroute] all models failed: {e}")
                    return self._template_fallback(system, prompt)
                print(f"  [omniroute] {attempt_model} failed, trying next...")

        return self._template_fallback(system, prompt)

    def _template_fallback(self, system: str, prompt: str) -> str:
        """Template-based fallback when OmniRoute is unavailable."""
        return f"[Offline analysis] No LLM available. System: {system[:80]}... Prompt: {prompt[:80]}..."

    # ── LOOM-Specific Methods ──────────────────────────────

    def analyze_whale_activity(self, leaderboard: list, events: list) -> str:
        """Generate natural language analysis of current whale activity."""
        system = """You are a whale intelligence analyst. Analyze wallet activity and 
        provide a concise, actionable summary. Focus on: which whales are active, 
        what patterns are forming, and what the data suggests will happen next. 
        Be specific — mention wallet addresses, conviction levels, and predicted timeframes.
        Keep under 200 words."""

        prompt = f"""Current whale leaderboard:
{json.dumps(leaderboard[:5], indent=2)}

Recent events ({len(events)} total):
{json.dumps(events[-5:] if events else [], indent=2)}

Provide a brief intelligence analysis."""

        return self.chat(system, prompt, max_tokens=400)

    def synthesize_narrative(self, data: dict) -> str:
        """Synthesize all LOOM data layers into a narrative brief."""
        system = """You are a market narrative synthesizer. Given structured data 
        about whale activity, liquidity flows, market signals, and anomalies,
        produce a concise intelligence brief. Use bullet points. Prioritize by urgency.
        Include: TOP ALERT (if any), WHALE MOVES, LIQUIDITY, SIGNALS, and OUTLOOK.
        Be specific with numbers."""

        prompt = f"""Structured market data:
{json.dumps(data, indent=2, default=str)}

Synthesize into an intelligence brief. Maximum 300 words."""

        return self.chat(system, prompt, max_tokens=600)

    def debate_signal(self, whale_signal: dict, tech_signal: dict,
                      risk_signal: dict) -> str:
        """Resolve agent disagreement through LLM debate."""
        system = """You are a trading arbiter. Two agents disagree on a trading signal.
        Evaluate both arguments, consider the risk assessment, and produce a final 
        recommendation. Be decisive but explain your reasoning. Output format:
        FINAL: [BUY/SELL/WAIT]
        CONFIDENCE: [0.0-1.0]
        REASONING: [2-3 sentences]"""

        prompt = f"""WHALE AGENT: {json.dumps(whale_signal)}
TECHNICAL AGENT: {json.dumps(tech_signal)}
RISK AGENT: {json.dumps(risk_signal)}

Resolve this debate and recommend action."""

        return self.chat(system, prompt, max_tokens=300)


# Global instance
bridge = OmniRouteBridge()
