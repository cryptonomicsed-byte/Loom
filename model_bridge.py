#!/usr/bin/env python3
"""
LOOM Multi-Model Bridge — Direct LLM access for trading agents.

Uses existing API keys (DeepSeek + OpenRouter) for free/cheap LLM calls.
No new signups. Auto-fallback between providers.

Models configured per agent purpose:
  - DeepSeek V3 — Fast, cheap reasoning (trade decisions)
  - OpenRouter free — Qwen, Mistral, Gemini Flash (analysis)
  - Template — Always available (no API needed)
"""

import json
import os
import urllib.request
import time
from typing import Optional

# ── Provider Config ──────────────────────────────────────────

PROVIDERS = {
    "deepseek": {
        "base_url": "https://api.deepseek.com/v1",
        "api_key": os.environ.get("DEEPSEEK_API_KEY", ""),
        "models": ["deepseek-chat", "deepseek-reasoner"],
        "default_model": "deepseek-chat",
        "cost": "cheap",
    },
    "openrouter": {
        "base_url": "https://openrouter.ai/api/v1",
        "api_key": os.environ.get("OPENROUTER_API_KEY", ""),
        "models": [
            "google/gemini-2.0-flash-001",         # Free on OpenRouter
            "qwen/qwen-2.5-7b-instruct",           # Free
            "mistralai/mistral-7b-instruct",        # Free
            "meta-llama/llama-3.2-3b-instruct",     # Free
        ],
        "default_model": "google/gemini-2.0-flash-001",
        "cost": "free",
    },
}

# ── Agent → Model mapping ─────────────────────────────────────

AGENT_MODELS = {
    "whale":       {"provider": "deepseek", "model": "deepseek-chat"},
    "technical":   {"provider": "openrouter", "model": "google/gemini-2.0-flash-001"},
    "risk":        {"provider": "openrouter", "model": "meta-llama/llama-3.2-3b-instruct"},
    "narrative":   {"provider": "deepseek", "model": "deepseek-chat"},
    "debate":      {"provider": "deepseek", "model": "deepseek-reasoner"},
}


class ModelBridge:
    """Multi-provider LLM bridge for LOOM agents."""

    def __init__(self):
        self.available = self._check_providers()
        self._last_call = 0
        self._call_count = 0

    def _check_providers(self) -> dict:
        available = {}
        for name, cfg in PROVIDERS.items():
            if cfg["api_key"]:
                available[name] = True
                print(f"  [bridge] {name} ✓ ({len(cfg['models'])} models)")
            else:
                available[name] = False
                print(f"  [bridge] {name} ✗ (no key)")
        return available

    def chat(self, system: str, prompt: str, agent_type: str = "narrative",
             max_tokens: int = 500, temperature: float = 0.3) -> str:
        """
        Send chat completion through the appropriate model for this agent.
        Falls back through providers automatically.
        """
        self._call_count += 1

        # Get agent-specific model
        agent_cfg = AGENT_MODELS.get(agent_type, AGENT_MODELS["narrative"])
        provider_name = agent_cfg["provider"]
        model = agent_cfg["model"]

        # Try primary provider first
        result = self._call_provider(provider_name, model, system, prompt, max_tokens, temperature)
        if result:
            return result

        # Fallback through other available providers
        for fallback_name in PROVIDERS:
            if fallback_name == provider_name:
                continue
            if not self.available.get(fallback_name):
                continue
            fb_model = PROVIDERS[fallback_name]["default_model"]
            result = self._call_provider(fallback_name, fb_model, system, prompt, max_tokens, temperature)
            if result:
                return result

        # Template fallback
        return self._template_fallback(system, prompt)

    def _call_provider(self, provider_name: str, model: str, system: str,
                       prompt: str, max_tokens: int, temperature: float) -> Optional[str]:
        """Make a single API call to a provider."""
        cfg = PROVIDERS.get(provider_name)
        if not cfg or not cfg["api_key"]:
            return None

        try:
            # Rate limit: max 1 call per 2 seconds
            now = time.time()
            if now - self._last_call < 2:
                time.sleep(2 - (now - self._last_call))
            self._last_call = time.time()

            body = json.dumps({
                "model": model,
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": prompt},
                ],
                "max_tokens": max_tokens,
                "temperature": temperature,
            }).encode()

            url = f"{cfg['base_url']}/chat/completions"
            req = urllib.request.Request(url, data=body, method="POST")
            req.add_header("Content-Type", "application/json")
            req.add_header("Authorization", f"Bearer {cfg['api_key']}")
            req.add_header("User-Agent", "LOOM-Agent/2.0")

            with urllib.request.urlopen(req, timeout=20) as resp:
                result = json.loads(resp.read())
                content = result["choices"][0]["message"]["content"]
                return content.strip()

        except Exception as e:
            print(f"  [bridge] {provider_name}/{model} failed: {e}")
            return None

    def _template_fallback(self, system: str, prompt: str) -> str:
        return f"[Template analysis] Unable to reach LLM providers."

    # ── LOOM-Specific Methods ──────────────────────────────

    def whale_reasoning(self, events: list, leaderboard: list) -> str:
        """Whale agent: analyze wallet patterns."""
        system = "You are a whale intelligence analyst. Analyze wallet activity patterns and predict the next likely move. Be specific about which wallets, what patterns, and expected timeframes. Keep under 150 words."
        prompt = f"Whale leaderboard: {json.dumps(leaderboard[:5])}\n\nRecent events: {json.dumps(events[-10:] if events else [])}\n\nAnalyze and predict."
        return self.chat(system, prompt, "whale", max_tokens=300)

    def technical_analysis(self, indicators: dict) -> str:
        """Technical agent: analyze chart patterns."""
        system = "You are a technical analyst. Given RSI, MACD, Bollinger Bands, and volume data, provide a concise BUY/SELL/WAIT call with reasoning. Be decisive. Keep under 100 words."
        prompt = f"Technical indicators: {json.dumps(indicators)}\n\nWhat is your call?"
        return self.chat(system, prompt, "technical", max_tokens=200)

    def risk_assessment(self, exposure: dict) -> str:
        """Risk agent: evaluate portfolio risk."""
        system = "You are a risk manager. Evaluate current exposure and flag any concerns. Output: ALLOW/BLOCK with reasoning. Keep under 80 words."
        prompt = f"Current exposure: {json.dumps(exposure)}\n\nAllow new positions?"
        return self.chat(system, prompt, "risk", max_tokens=150)

    def debate_resolve(self, whale_sig: dict, tech_sig: dict, risk_sig: dict) -> str:
        """Debate arbiter: resolve agent disagreement."""
        system = "You are a trading arbiter. Two agents disagree. Evaluate both arguments and make a final BUY/SELL/WAIT call with confidence 0-1. Output format: FINAL: [call] CONFIDENCE: [0.0-1.0] REASONING: [2 sentences]"
        prompt = f"WHALE: {json.dumps(whale_sig)}\nTECHNICAL: {json.dumps(tech_sig)}\nRISK: {json.dumps(risk_sig)}\n\nResolve."
        return self.chat(system, prompt, "debate", max_tokens=300)

    def synthesize_brief(self, data: dict) -> str:
        """Narrative agent: synthesize full intel brief."""
        system = "You are a market intelligence synthesizer. Produce a concise brief from structured data about whale activity, signals, and market conditions. Use bullet points. Prioritize urgency. Maximum 250 words."
        prompt = f"Market data: {json.dumps(data, indent=2, default=str)}\n\nSynthesize."
        return self.chat(system, prompt, "narrative", max_tokens=500)


# Global instance
bridge = ModelBridge()
