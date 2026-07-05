"""
LOOM Twitter Mapper — Links social accounts to wallet addresses.
Matches timing, behavior patterns, and content to build the identity graph.
"""

import json
import time
import re
import hashlib
from collections import defaultdict


class TwitterMapper:
    """Builds the wallet → Twitter identity graph."""

    def __init__(self):
        self.matches: dict = {}            # {wallet_address: {handle, confidence, evidence}}
        self.handle_index: dict = {}       # {handle: wallet_address}
        self.behavioral_fingerprints: dict = {}  # {handle: {pre_trade_phrases, post_trade_phrases, tweet_rhythm}}
        self._signal_queue: list = []      # pending signals to match

    def ingest_tweet(self, handle: str, text: str, timestamp: float,
                     mentioned_tokens: list = None, mentioned_addresses: list = None):
        """Ingest a tweet for matching."""

        # Track behavioral patterns
        if handle not in self.behavioral_fingerprints:
            self.behavioral_fingerprints[handle] = {
                "tweets": [],
                "pre_trade_phrases": [],
                "post_trade_phrases": [],
                "common_times": [],
                "mentioned_tokens": [],
            }

        fp = self.behavioral_fingerprints[handle]
        fp["tweets"].append({"text": text[:200], "ts": timestamp})

        if mentioned_tokens:
            fp["mentioned_tokens"].extend(mentioned_tokens)

        # Queue for matching against recent whale activity
        self._signal_queue.append({
            "handle": handle,
            "text": text,
            "ts": timestamp,
            "tokens": mentioned_tokens or [],
            "addresses": mentioned_addresses or [],
        })

        # Prune old signals
        now = time.time()
        self._signal_queue = [s for s in self._signal_queue if now - s["ts"] < 3600]

    def match_whale_activity(self, wallet_address: str, token: str,
                             entry_time: float, platform: str = "pump.fun"):
        """Check if any recent tweets match this whale activity."""

        for signal in self._signal_queue:
            ts = signal["ts"]
            # Tweet must be within 5 minutes of trade
            if abs(ts - entry_time) > 300:
                continue

            score = 0.0
            evidence = []

            # Token mentioned in tweet
            if token and token.lower() in signal["text"].lower():
                score += 0.3
                evidence.append(f"token_mentioned:{token}")

            # Token ticker mentioned
            ticker = token[:6] if token else ""
            if ticker and ticker.lower() in signal["text"].lower():
                score += 0.2
                evidence.append(f"ticker_match:{ticker}")

            # Timing correlation (tweet right before trade = accumulation signal)
            time_gap = abs(ts - entry_time)
            if time_gap < 60:
                score += 0.3
                evidence.append("tight_timing")
            elif time_gap < 180:
                score += 0.15
                evidence.append("close_timing")

            # Platform mention
            if platform and platform.lower() in signal["text"].lower():
                score += 0.1
                evidence.append(f"platform:{platform}")

            # Update match confidence
            if score > 0.3:
                handle = signal["handle"]
                if wallet_address in self.matches:
                    existing = self.matches[wallet_address]
                    # Merge evidence
                    existing["confidence"] = min(0.99, existing["confidence"] + score * 0.1)
                    existing["evidence"].extend(evidence)
                    existing["last_matched"] = time.time()
                else:
                    self.matches[wallet_address] = {
                        "handle": handle,
                        "confidence": min(0.7, score),
                        "evidence": evidence,
                        "first_matched": time.time(),
                        "last_matched": time.time(),
                    }
                    self.handle_index[handle] = wallet_address

    def get_handle(self, wallet_address: str) -> dict:
        """Get Twitter handle for a wallet, if known."""
        return self.matches.get(wallet_address, {})

    def get_wallet(self, handle: str) -> str:
        """Get wallet for a Twitter handle."""
        return self.handle_index.get(handle)

    def get_all_matches(self) -> dict:
        return self.matches

    def get_behavioral_fingerprint(self, handle: str) -> dict:
        """Get a Twitter user's trading-relevant behavior patterns."""
        fp = self.behavioral_fingerprints.get(handle, {})
        tweets = fp.get("tweets", [])

        # Detect pre-trade signals
        pre_trade_phrases = ["gm", "good morning", "mornin", "let's go", "sending", "incoming",
                            "loading", "soon", "eyes", "watching", "interesting"]
        post_trade_phrases = ["called", "print", "profit", "exit", "sold", "dump", "rekt",
                             "moon", "pump", "send it"]

        pre_count = sum(1 for t in tweets if any(p in t["text"].lower() for p in pre_trade_phrases))
        post_count = sum(1 for t in tweets if any(p in t["text"].lower() for p in post_trade_phrases))

        # Extract common tokens mentioned
        token_pattern = re.compile(r'\$([A-Za-z]{2,10})')
        all_tokens = []
        for t in tweets:
            all_tokens.extend(token_pattern.findall(t["text"]))

        token_freq = defaultdict(int)
        for tok in all_tokens:
            token_freq[tok.upper()] += 1

        return {
            "handle": handle,
            "total_tweets": len(tweets),
            "pre_trade_signals": pre_count,
            "post_trade_signals": post_count,
            "behavior": "accumulation_signaler" if pre_count > post_count else
                       "exit_pumper" if post_count > pre_count else "neutral",
            "favorite_tokens": sorted(token_freq.items(), key=lambda x: x[1], reverse=True)[:10],
            "matched_wallet": self.get_wallet(handle),
            "confidence": self.matches.get(self.get_wallet(handle), {}).get("confidence", 0)
                         if self.get_wallet(handle) else 0,
        }

    def get_signal_timeline(self, handle: str, hours: int = 24) -> list:
        """Recent tweets from a matched account that may signal trading."""
        cutoff = time.time() - (hours * 3600)
        fp = self.behavioral_fingerprints.get(handle, {})
        tweets = fp.get("tweets", [])

        signals = []
        signal_words = ["buy", "sell", "pump", "dump", "long", "short", "entry",
                       "exit", "target", "moon", "send", "gm", "loading",
                       "soon", "eyes", "print", "called"]

        for t in tweets:
            if t["ts"] < cutoff:
                continue
            if any(w in t["text"].lower() for w in signal_words):
                tokens = re.findall(r'\$([A-Za-z]{2,10})', t["text"])
                signals.append({
                    "ts": t["ts"],
                    "text": t["text"][:200],
                    "tokens": [tok.upper() for tok in tokens],
                    "type": "pre_trade" if any(p in t["text"].lower()
                            for p in ["gm", "loading", "soon", "eyes", "sending"])
                            else "post_trade" if any(p in t["text"].lower()
                            for p in ["called", "print", "profit", "moon"])
                            else "signal",
                })

        return sorted(signals, key=lambda s: s["ts"], reverse=True)[:20]


# Global instance
twitter_mapper = TwitterMapper()
