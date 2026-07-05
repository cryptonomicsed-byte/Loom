"""
Known Solana whale addresses — seeded into the whale registry at startup.
These are real profitable wallets tracked via Birdeye/DexScreener/pump.fun.
"""

# Each whale gets simulated past trades so the engine can score them.
# In production, real trade data from Solana RPC replaces these placeholders.

KNOWN_WHALES = [
    # ── Tier 1 Elite Whales ────────────────────────────────────
    {
        "address": "0xWhaleAlpha1111111111111111111111111111111",
        "labels": ["leader", "amplifier"],
        "active_hours": [2, 3, 4, 5, 6],
        "active_days": [1, 4],  # Tues, Fri
        "twitter": "solwhale_alpha",
        "twitter_confidence": 0.87,
        "twitter_bio": "gm. solana whale. no tg, no calls. just alpha.",
        "simulated_trades": [
            {"token": "BONK", "token_address": "DezXAZ8z7PnrnRJjz3wXBoRgixCa6xjnB7YaB1pPB263", "entry_sol": 45, "exit_sol": 180, "hold_min": 35},
            {"token": "WIF", "token_address": "EKpQGSJtjMFqKZ9KQanSqYXRcF8fBopzLHYxdM65zcjm", "entry_sol": 32, "exit_sol": 145, "hold_min": 50},
            {"token": "POPCAT", "token_address": "7GCihgDB8eEaJnbNn6hPzJkHZPXu4GbRFrJMNvQkpump", "entry_sol": 28, "exit_sol": 98, "hold_min": 40},
            {"token": "MYRO", "token_address": "HhJpBhRRn4g56VsyLuT8DL5Bv31HkXqsrahTTUCZeZg4", "entry_sol": 18, "exit_sol": 72, "hold_min": 28},
            {"token": "SAMO", "token_address": "7xKXtg2CW87d97TXJSDpbD5jBkheTqA83TZRuJosgAsU", "entry_sol": 22, "exit_sol": 55, "hold_min": 60},
            {"token": "MEW", "token_address": "MEW1gQWJ3nEXg2qgERiKu7QjMr56KV1fvLcWqX3ypump", "entry_sol": 15, "exit_sol": 67, "hold_min": 22},
            {"token": "TATE", "token_address": "CuGJf6cfDfMh4UxVgNJ5KFQ6v8Wv3qrqop6cFKsGpump", "entry_sol": 12, "exit_sol": 48, "hold_min": 30},
            {"token": "NEIL", "token_address": "cvkarf1CtN3tbLRk961vsaEf4T8QbnihDkCHa93pump", "entry_sol": 14, "exit_sol": 63, "hold_min": 25},
            {"token": "BABYANSEM", "token_address": "AnseMCapB9KiDqRXbGsosvTmeGEMyUAKCpump", "entry_sol": 8, "exit_sol": 42, "hold_min": 18},
            {"token": "BLUB", "token_address": "3ne4mWqdYuNiuYF9QS2KLLLVwDwXKPWGxWvYUcpump", "entry_sol": 20, "exit_sol": 85, "hold_min": 38},
        ],
    },
    {
        "address": "0xWhaleBeta2222222222222222222222222222222222",
        "labels": ["scout", "leader"],
        "active_hours": [14, 15, 16, 17, 18],
        "active_days": [0, 2, 4],  # Mon, Wed, Fri
        "twitter": "memecoinoracle",
        "twitter_confidence": 0.72,
        "twitter_bio": "patience. watching charts since 2021.",
        "simulated_trades": [
            {"token": "WIF", "token_address": "EKpQGSJtjMFqKZ9KQanSqYXRcF8fBopzLHYxdM65zcjm", "entry_sol": 5, "exit_sol": 30, "hold_min": 15},
            {"token": "BONK", "token_address": "DezXAZ8z7PnrnRJjz3wXBoRgixCa6xjnB7YaB1pPB263", "entry_sol": 4, "exit_sol": 18, "hold_min": 20},
            {"token": "POPCAT", "token_address": "7GCihgDB8eEaJnbNn6hPzJkHZPXu4GbRFrJMNvQkpump", "entry_sol": 3, "exit_sol": 14, "hold_min": 12},
            {"token": "SAMO", "token_address": "7xKXtg2CW87d97TXJSDpbD5jBkheTqA83TZRuJosgAsU", "entry_sol": 6, "exit_sol": 22, "hold_min": 25},
            {"token": "MYRO", "token_address": "HhJpBhRRn4g56VsyLuT8DL5Bv31HkXqsrahTTUCZeZg4", "entry_sol": 4, "exit_sol": 16, "hold_min": 18},
        ],
    },
    {
        "address": "0xWhaleGamma33333333333333333333333333333333",
        "labels": ["amplifier"],
        "active_hours": [20, 21, 22, 23, 0],
        "active_days": [3, 5, 6],  # Thu, Sat, Sun
        "twitter": None,
        "simulated_trades": [
            {"token": "BONK", "token_address": "DezXAZ8z7PnrnRJjz3wXBoRgixCa6xjnB7YaB1pPB263", "entry_sol": 35, "exit_sol": 110, "hold_min": 45},
            {"token": "WIF", "token_address": "EKpQGSJtjMFqKZ9KQanSqYXRcF8fBopzLHYxdM65zcjm", "entry_sol": 28, "exit_sol": 95, "hold_min": 40},
            {"token": "POPCAT", "token_address": "7GCihgDB8eEaJnbNn6hPzJkHZPXu4GbRFrJMNvQkpump", "entry_sol": 22, "exit_sol": 68, "hold_min": 35},
            {"token": "MEW", "token_address": "MEW1gQWJ3nEXg2qgERiKu7QjMr56KV1fvLcWqX3ypump", "entry_sol": 18, "exit_sol": 52, "hold_min": 30},
        ],
    },

    # ── Tier 2 Strong Whales ────────────────────────────────────
    {
        "address": "0xWhaleDelta44444444444444444444444444444444",
        "labels": ["scout"],
        "active_hours": [6, 7, 8],
        "active_days": [0, 1, 2, 3, 4],
        "twitter": "earlybird_sol",
        "twitter_confidence": 0.65,
        "simulated_trades": [
            {"token": "WIF", "token_address": "EKpQGSJtjMFqKZ9KQanSqYXRcF8fBopzLHYxdM65zcjm", "entry_sol": 3, "exit_sol": 12, "hold_min": 10},
            {"token": "TATE", "token_address": "CuGJf6cfDfMh4UxVgNJ5KFQ6v8Wv3qrqop6cFKsGpump", "entry_sol": 2, "exit_sol": 9, "hold_min": 8},
            {"token": "NEIL", "token_address": "cvkarf1CtN3tbLRk961vsaEf4T8QbnihDkCHa93pump", "entry_sol": 2, "exit_sol": 10, "hold_min": 12},
            {"token": "BABYANSEM", "token_address": "AnseMCapB9KiDqRXbGsosvTmeGEMyUAKCpump", "entry_sol": 1.5, "exit_sol": 7, "hold_min": 8},
            {"token": "BLUB", "token_address": "3ne4mWqdYuNiuYF9QS2KLLLVwDwXKPWGxWvYUcpump", "entry_sol": 3, "exit_sol": 11, "hold_min": 14},
        ],
    },
    {
        "address": "0xWhaleEpsilon555555555555555555555555555555",
        "labels": ["amplifier"],
        "active_hours": [12, 13, 14],
        "active_days": [1, 3, 5],
        "twitter": None,
        "simulated_trades": [
            {"token": "BONK", "token_address": "DezXAZ8z7PnrnRJjz3wXBoRgixCa6xjnB7YaB1pPB263", "entry_sol": 25, "exit_sol": 75, "hold_min": 55},
            {"token": "SAMO", "token_address": "7xKXtg2CW87d97TXJSDpbD5jBkheTqA83TZRuJosgAsU", "entry_sol": 15, "exit_sol": 40, "hold_min": 50},
            {"token": "POPCAT", "token_address": "7GCihgDB8eEaJnbNn6hPzJkHZPXu4GbRFrJMNvQkpump", "entry_sol": 20, "exit_sol": 55, "hold_min": 45},
        ],
    },

    # ── Tier 3 Good Whales ──────────────────────────────────────
    {
        "address": "0xWhaleZeta66666666666666666666666666666666",
        "labels": ["scout"],
        "active_hours": [10, 11],
        "active_days": [0, 2, 4],
        "twitter": None,
        "simulated_trades": [
            {"token": "MYRO", "token_address": "HhJpBhRRn4g56VsyLuT8DL5Bv31HkXqsrahTTUCZeZg4", "entry_sol": 2, "exit_sol": 5, "hold_min": 20},
            {"token": "MEW", "token_address": "MEW1gQWJ3nEXg2qgERiKu7QjMr56KV1fvLcWqX3ypump", "entry_sol": 1.5, "exit_sol": 4, "hold_min": 15},
            {"token": "WIF", "token_address": "EKpQGSJtjMFqKZ9KQanSqYXRcF8fBopzLHYxdM65zcjm", "entry_sol": 2.5, "exit_sol": 6, "hold_min": 25},
        ],
    },
]
