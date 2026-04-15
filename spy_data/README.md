# Spy Data — Prosperity Simulator Runs

Reconnaissance bots ("spies") deployed on the Prosperity Round 1 simulator to
capture market microstructure data that the platform CSVs don't expose.
Each subdirectory contains:

- **`spy_*.py`** — the Trader algo that was uploaded to the platform.
- **`run_<id>.json`** — the raw results JSON downloaded after the run finished.

All runs target **Round 1** products: `ASH_COATED_OSMIUM` and `INTARIAN_PEPPER_ROOT`.

---

## Directory Index

| Directory | Upload ID | Spy Algo | Purpose | Profit | Log Prefix |
|---|---|---|---|---|---|
| `observations/` | 166532 | `spy_observations.py` | Capture `plainValueObservations` and `conversionObservations` every tick; track deltas between ticks; probe conversion costs | 0.0 | `SPY_OBS` |
| `orderbook/` | 166756 | `spy_orderbook.py` | Snapshot full orderbook (all levels, not just top-3 from CSV); probe hidden depth on thin sides | 0.0 | `SPY_BOOK` |
| `signals/` | 166877 | `spy_signals.py` | Compute derived signals (mid, VWMP, spread, imbalance, microprice, book_pressure) and rolling stats (EMA returns, realized vol, lag-1 autocorrelation, regime classification) | 0.0 | `SPY_SIG` |
| `trades/` | 166949 | `spy_trades.py` | Record every market trade with full detail; place probe orders at varying offsets from mid to build a fill-probability curve | 124.38 | `SPY_TRADE` |
| `experiment_ipr/` | 167373 | `spy_ipr.py` | Systematic experiment runner for `INTARIAN_PEPPER_ROOT`: edge sweep, volume sweep, take strategies, skew tests on a 30-tick cycle | 327.41 | `SPY_IPR` |
| `experiment_aco/` | 167459 | `spy_aco.py` | Systematic experiment runner for `ASH_COATED_OSMIUM`: edge sweep, volume sweep, take strategies, skew tests on a 30-tick cycle (anchored at 10,000) | 267.47 | `SPY_ACO` |

---

## JSON Result Structure

Each `run_<id>.json` has the same schema from the Prosperity platform:

```
{
  "round": "1",
  "status": "FINISHED",
  "profit": <float>,
  "activitiesLog": "<semicolon-delimited CSV string>",
  "graphLog": "<timestamp;value CSV string>",
  "positions": [{"symbol": "XIRECS", "quantity": 0}]
}
```

### `activitiesLog` columns
```
day; timestamp; product;
bid_price_1; bid_volume_1; bid_price_2; bid_volume_2; bid_price_3; bid_volume_3;
ask_price_1; ask_volume_1; ask_price_2; ask_volume_2; ask_price_3; ask_volume_3;
mid_price; profit_and_loss
```

- 2001 rows per run (1000 timestamps × 2 products + header)
- Products: `ASH_COATED_OSMIUM`, `INTARIAN_PEPPER_ROOT`

### `graphLog`
PnL curve at each timestamp: `timestamp;value`

---

## What Each Spy Captures (beyond the CSV)

### 1. `observations/` — Environment Data
- `plainValueObservations`: raw scalar observations (humidity, sunlight, etc.)
- `conversionObservations`: bid/ask prices, transport fees, tariffs for conversion products
- Tick-over-tick deltas for all observation fields
- Conversion probe results (cost to actually convert)

### 2. `orderbook/` — Full Depth
- All bid/ask levels with volumes (CSV only shows top 3)
- Bid/ask level counts, total volume per side
- Probes thin book sides by placing orders 1 tick beyond deepest visible level
- Tracks whether depth probes get filled (detecting hidden liquidity)

### 3. `signals/` — Derived Microstructure Signals
- `mid`, `vwmp` (volume-weighted mid), `spread`, `imbalance`
- `wall_mid` (average of highest-volume bid/ask prices)
- `microprice` (size-weighted fair value tilted toward thinner side)
- `book_pressure` (top-2 bid vol / top-2 ask vol)
- Rolling: EMA returns, realized volatility, lag-1 autocorrelation
- Regime classification: `MR` (mean-reverting), `TR` (trending), `RW` (random walk)

### 4. `trades/` — Trade Flow & Fill Probability
- Every market trade with price, quantity, buyer, seller, timestamp
- Per-product VWAP and volume stats
- Probe orders at offsets [-3, -2, -1, 0, +1, +2, +3] from mid
- Cumulative fill-rate tracker per offset level

### 5. `experiment_aco/` — ASH_COATED_OSMIUM Bot Probing
30-tick experiment cycle:
- **Edge sweep** (12 ticks): quote offsets 1–15 from wall_mid
- **Volume sweep** (7 ticks): lot sizes 1–30 at fixed edge=5
- **Take strategy** (4 ticks): passive / aggressive / overbid / wallmatch
- **Skew test** (7 ticks): bid/ask volume ratios from 100/0 to 0/100
- Anchored at 10,000; tracks distance from anchor

### 6. `experiment_ipr/` — INTARIAN_PEPPER_ROOT Bot Probing
Same 30-tick cycle as ACO but IPR-specific:
- No anchor (IPR drifts ~1000/day)
- Tracks tick-over-tick returns for momentum correlation with fills
- Tighter baseline_edge=3 (vs ACO's edge=5)
- Correlates fill results with momentum direction

---

## Parsing Tips

The spy algos print structured JSON to stdout with prefixes:
- `SPY_OBS|{...}` — observations data
- `SPY_BOOK|{...}` — orderbook snapshots
- `SPY_SIG|{...}` — derived signals
- `SPY_TRADE|{...}` — trade records
- `SPY_ACO|{...}` — ACO experiment results
- `SPY_IPR|{...}` — IPR experiment results

These can be extracted from the platform's sandbox log (not included in the
JSON results — must be captured separately via `log_parser.py`).

The `activitiesLog` and `graphLog` in each JSON are the standard platform
outputs and can be parsed directly with pandas:

```python
import json, pandas as pd, io

with open("spy_data/trades/run_166949.json") as f:
    data = json.load(f)

activities = pd.read_csv(io.StringIO(data["activitiesLog"]), sep=";")
pnl_curve = pd.read_csv(io.StringIO(data["graphLog"]), sep=";")
```
