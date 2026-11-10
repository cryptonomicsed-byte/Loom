# AUDIT — TransformerSignalStrategy.py (2026-08-19)

Panel: SIGNAL STRATEGIST. Scope: `/opt/ares/Loom/TransformerSignalStrategy.py`
(+ its live data path through `fast_server.py` `/api/whales/galaxy`).

## Summary

The strategy plumbing (freqtrade interface, indicator stack, risk params) is
sound, but the **transformer signal leg is effectively inert in production**
and several hard-risk features (bundler, honeypot, liquidity depth, age) are
missing entirely. The backtest in this repo (`backtester.py`) does not touch
real OHLCV — it simulates price paths with GBM, so none of the numbers it
produces are market-calibrated.

## Critical findings

### F1 — Transformer signal is inert in production (dead leg)
`_get_transformer_prediction()` pulls `/api/whales/galaxy` from the LOOM fast
server and computes `avg_pv` over `tier <= 2` whale nodes. But
`fast_server.py` serves a **static seeded galaxy** whose whale nodes have no
`pv` key, and the cache never refreshes with live data (the background loader
never populates galaxy nodes with `pv`). Consequences:

- `avg_pv = 0` → `conviction = 0`, `direction = "WAIT"` — every call.
- Entry condition `t_conviction >= min_conviction * 0.7` (0.5 * 0.7 = 0.35)
  can never pass with conviction 0 → **the strategy emits zero entries** in
  production as wired today.
- Confirmed in `fast_server.py`: galaxy nodes `{"id","name","val","color","kind","tier"}` — no `pv`.

### F2 — Global, not per-pair, prediction cache
`_prediction_cache` is a single dict keyed by nothing. All pairs receive the
*identical* transformer direction/conviction on every candle. A whale cluster
on BONK therefore gates entries on SOL. The cache also ignores `metadata`
(pair symbol), so pair-aware conditioning is impossible.

### F3 — SELL leg never fires
The heuristic only ever sets `direction` to `"BUY"` (if `avg_pv > 0.6`) or
leaves `"WAIT"`. `"SELL"` is never emitted, so the entire
`transformer_sell` path in `populate_exit_trend` is dead code. `p_sell` from
the transformer is never consumed by the strategy.

### F4 — No hard-risk filters (the big gap)
Nothing in `populate_entry_trend` checks:
- **Bundler risk** — no holder concentration, no single-dex dominance, no
  launch-window volume fingerprint.
- **Honeypot flags** — no buy/sell tax proxy, no sell-side liquidity check,
  no "price up / sells blocked" pattern.
- **Liquidity depth** — no minimum USD liquidity gate; a $3k pool is
  tradable.
- **Token age** — no min-age filter; day-0 launches are eligible.
- **Volume quality** — `volume_ratio > 0.6` is extremely lax (a 40% drop vs
  its own 20-bar average still passes); no wash-trade/turnover sanity.
- **Market cap floor** — no FDV/mcap bounds (rug-bait microcaps eligible).

### F5 — Numerical/robustness issues
- RSI: `loss` can be 0 → `rs = gain/0 = inf` → RSI = 100; if both gain and
  loss are 0, `rs = NaN` → RSI = NaN. NaN comparisons are False, silently
  killing that leg. Needs an epsilon.
- `startup_candle_count = 50` is fine for RSI(14)/BB(20), but there is no
  warm-up guard if a feed returns fewer bars.
- `_gather_events()` hits `/api/state`; if the API is down it silently
  returns `[]`, and `predict([])` returns a WAIT/0 dict — no logging, no
  degradation signal.
- `LOOM_API` is hardcoded to localhost:8889 with no env override.
- DecimalParameter/IntParameter defaults are sane but the `min_conviction`
  gate interacts with F1 to block everything.

### F6 — Backtester does not use market data
`backtester.py` `simulate_price_path()` generates GBM paths from the
prediction itself (`drift = 0.01 * magnitude`), so `correct` is ~always true
for BUY (positive drift) and the win-rate/profit-factor numbers are
self-confirming artifacts. No fees, no slippage, no OHLCV, no market
calibration. Item 2 of this panel's brief addresses this directly.

## Concrete improvements (implemented in this branch)

1. **`ohlcv_feed.py`** — paced GeckoTerminal OHLCV (≈30 req/min respected,
   jittered, retried) + DexScreener pair metadata (liquidity, volume, txns,
   pair age, price windows) with an on-disk cache. Makes the backtest
   market-grounded.
2. **`signal_composites.py`** — `MarketQualityEngine`: liquidity-depth gate,
   age gate, volume/turnover sanity, honeypot flags (illiquid pump, sell-side
   vacuum, buy/sell txn imbalance), bundler-risk proxy (single-pool volume
   dominance, launch-window price fingerprint, FDV/liquidity mismatch).
   `SmartMoneyComposite`: per-token conviction fusing whale-engine
   predictive value, momentum (ROC/volume surge), market quality, and
   technical confirmation. Wired into `engines.py` (NarrativeBrief section)
   and `fabric.py` (new `smart_money_momentum` event + ingest path).
3. **`backtester.py`** — new `OHLCVBacktester`: walk-forward simulation on
   real bars with fees + slippage + stop/take-profit/max-hold, plus a
   calibration report (signal hit-rate vs conviction buckets, Brier score,
   horizon decay). Legacy `WhaleBacktester` kept intact.
4. **`run_calibration.py`** — 30-day before/after run on a 10-token Solana
   universe; results in `reports/`.

## Recommended next enhancements (beyond this branch)

1. Feed live `pv`/`tier` into the galaxy cache (or replace the galaxy
   heuristic with the whale engine's `get_active_whales()`) so the strategy
   leg emits real conviction — then key the cache per pair.
2. Add on-chain holder-concentration (top-10 holders %) via Solana RPC
   `getTokenLargestAccounts` as a true bundler detector; treat >30% top-10
   as hard reject.
3. Calibrate the conviction threshold on live signal history (the Brier/ROC
   plumbing in this branch is the harness for it) and add a
   volume-participation filter (buy volume / total volume) to kill
   wash-driven signals.
