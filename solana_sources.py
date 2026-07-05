"""
LOOM Solana Sources — Monitors pump.fun, Jupiter, Birdeye for whale activity.
Feeds the whale engine with real wallet data.
"""

import json
import time
import urllib.request
import threading
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from whales import whales

# Public RPC endpoints (free, rate-limited but functional)
SOLANA_RPC = "https://api.mainnet-beta.solana.com"
JUPITER_API = "https://quote-api.jup.ag/v6"
BIRDEYE_API = "https://public-api.birdeye.com"

# Known pump.fun program addresses to monitor
PUMP_FUN_PROGRAM = "6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P"
PUMP_FUN_EVENTS = ["initialize", "create", "trade", "complete"]


def rpc_call(method: str, params: list = None) -> dict:
    """Make a JSON-RPC call to Solana."""
    try:
        body = json.dumps({
            "jsonrpc": "2.0",
            "id": 1,
            "method": method,
            "params": params or [],
        }).encode()
        req = urllib.request.Request(SOLANA_RPC, data=body, headers={
            "Content-Type": "application/json",
            "User-Agent": "LOOM/1.0"
        })
        with urllib.request.urlopen(req, timeout=10) as resp:
            result = json.loads(resp.read())
            return result.get("result", {})
    except Exception as e:
        return {"error": str(e)}


def get_token_holders(token_mint: str, limit: int = 20) -> list:
    """Get top token holders for a given mint address."""
    result = rpc_call("getProgramAccounts", [
        "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA",
        {
            "encoding": "jsonParsed",
            "filters": [
                {"dataSize": 165},
                {"memcmp": {"offset": 0, "bytes": token_mint}},
            ],
        },
    ])
    accounts = result if isinstance(result, list) else []
    holders = []
    for acc in accounts[:limit]:
        try:
            info = acc.get("account", {}).get("data", {}).get("parsed", {}).get("info", {})
            amount = int(info.get("tokenAmount", {}).get("amount", 0))
            decimals = int(info.get("tokenAmount", {}).get("decimals", 0))
            owner = info.get("owner", "")
            if amount > 0 and owner:
                holders.append({
                    "owner": owner,
                    "amount": amount / (10 ** decimals),
                })
        except Exception:
            pass
    holders.sort(key=lambda h: h["amount"], reverse=True)
    return holders


def get_recent_signatures(address: str, limit: int = 10) -> list:
    """Get recent transaction signatures for an address."""
    result = rpc_call("getSignaturesForAddress", [address, {"limit": limit}])
    return result if isinstance(result, list) else []


def get_transaction(signature: str) -> dict:
    """Get parsed transaction details."""
    result = rpc_call("getTransaction", [signature, {
        "encoding": "jsonParsed",
        "maxSupportedTransactionVersion": 0,
    }])
    return result if isinstance(result, dict) else {}


def scan_pump_fun_tokens(limit: int = 20) -> list:
    """Find recently created pump.fun tokens by scanning program signatures."""
    sigs = get_recent_signatures(PUMP_FUN_PROGRAM, limit=limit)
    tokens = []
    for sig_entry in sigs:
        sig = sig_entry.get("signature", "")
        if not sig:
            continue
        tx = get_transaction(sig)
        if not tx:
            continue

        # Extract token info from transaction logs
        try:
            meta = tx.get("meta", {})
            block_time = tx.get("blockTime", time.time())
            log_msgs = meta.get("logMessages", [])
            pre_balances = meta.get("preTokenBalances", [])
            post_balances = meta.get("postTokenBalances", [])

            # Look for token mint addresses
            mints_seen = set()
            for bal in post_balances:
                mint = bal.get("mint", "")
                if mint and mint not in mints_seen:
                    mints_seen.add(mint)
                    tokens.append({
                        "mint": mint,
                        "signature": sig,
                        "block_time": block_time,
                        "type": "pump_fun",
                    })
        except Exception:
            pass

    return tokens


def fetch_birdeye_trending(limit: int = 10) -> list:
    """Fetch trending tokens from Birdeye."""
    try:
        url = f"https://public-api.birdeye.com/defi/token_trending?sort_by=rank&sort_type=asc&limit={limit}"
        req = urllib.request.Request(url, headers={
            "User-Agent": "LOOM/1.0",
            "X-API-KEY": os.environ.get("BIRDEYE_API_KEY", ""),
            "x-chain": "solana",
        })
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read())
            return data.get("data", {}).get("tokens", [])
    except Exception:
        return []


def fetch_jupiter_quote(input_mint: str, output_mint: str, amount: int = 1000000) -> dict:
    """Get a Jupiter quote for price discovery."""
    try:
        url = f"{JUPITER_API}/quote?inputMint={input_mint}&outputMint={output_mint}&amount={amount}&slippageBps=50"
        req = urllib.request.Request(url, headers={"User-Agent": "LOOM/1.0"})
        with urllib.request.urlopen(req, timeout=8) as resp:
            return json.loads(resp.read())
    except Exception:
        return {}


# ── Integration with Whale Engine ──────────────────────────────

def process_token_for_whales(token_address: str, token_symbol: str = "?"):
    """Find whales holding or trading this token, feed into whale engine."""
    holders = get_token_holders(token_address, limit=15)

    for h in holders:
        address = h["owner"]
        amount = h["amount"]

        # Register in whale engine with estimated entry
        # (We don't have actual entry data without historical lookups,
        # so we estimate based on holder position)
        if amount > 100000:  # significant holding
            entry_sol_est = min(amount / 100000, 100)  # rough estimate
            whales.register_trade(
                address=address,
                token=token_symbol,
                token_address=token_address,
                entry_sol=entry_sol_est,
                platform="pump.fun",
            )

    # After registering all holders, detect clusters
    cluster = whales.detect_clusters(token_address, time_window_sec=120)
    if cluster:
        print(f"  [whales] cluster detected on {token_symbol}: {len(cluster.members)} members")

    return len(holders)


def whales_scan_loop():
    """Background loop: scan trending tokens for whale activity."""
    print("[LOOM] Whale scanner started")

    while True:
        try:
            # Check Birdeye trending
            trending = fetch_birdeye_trending(limit=10)
            for token in trending:
                addr = token.get("address", "")
                symbol = token.get("symbol", "?")
                if addr:
                    process_token_for_whales(addr, symbol)

            # Also check pump.fun for new tokens
            pump_tokens = scan_pump_fun_tokens(limit=10)
            for pt in pump_tokens:
                mint = pt["mint"]
                process_token_for_whales(mint, "PUMP")

        except Exception as e:
            print(f"  [whales] scan error: {e}")

        # Scan every 30 seconds
        time.sleep(30)


def start_whale_scanner():
    t = threading.Thread(target=whales_scan_loop, daemon=True)
    t.start()
    return t
