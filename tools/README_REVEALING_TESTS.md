# Revealing parameter tests (follow-on to full-grid sweeps)

This complements [README_SWEEP.md](README_SWEEP.md). Full index sweeps (e.g. `aco-range 0:29`) confound stages; use these patterns to isolate effects.

## 1. Paired Stage A: `join` vs `improve_1` (same `min_take_edge`)

Indices **(0,1), (2,3), (4,5), (6,7)** differ only by `maker_mode`. After running a sweep CSV, compare deltas:

```bash
python tools/sweep_compare_pairs.py results/sweep_aco_r1.csv \
  --product ASH_COATED_OSMIUM --id-column aco_config_id \
  --pairs 0:1,2:3,4:5,6:7
```

Same for IPR with `--id-column ipr_config_id`.

## 2. Leave-one-day-out (LODO) and worst-day robustness

```bash
python tools/sweep_analysis_example.py results.csv --product ASH_COATED_OSMIUM --lodo
```

Uses `--lodo` to add **lodo_min** / **lodo_mean** / **lodo_max** (three folds, each dropping one day). Prefer configs with high **lodo_min** if you fear one bad day.

## 3. Stage zoom (one stage at a time)

`sweep_submission` merges **one** override dict per index onto baseline. Indices **8+** change later stages while **Stage A reverts to baseline** (`min_take_edge` 1, `improve_1`) unless that index also overrides Stage A.

Example: sweep **only** `make_portion` (ACO indices 8–12):

```bash
python tools/sweep_round1.py --active ACO --aco-range 8:12 --ipr-id 3 -o results/zoom_aco_make.csv
```

IPR Stage D (bid/ask size skew): `--ipr-range 22:25`. IPR Stage C: `--ipr-range 13:21`.

## 4. BOTH matrix (small Cartesian)

Pick a few ACO and IPR IDs from isolated leaderboards, then:

```bash
python tools/sweep_round1.py --active BOTH --aco-range 2:4 --ipr-range 0:2 -o results/both_matrix.csv
```

Compare per-product PnL to isolated runs; watch for one product collapsing when both trade.

## 5. Match-trades sensitivity (sanity)

```bash
python tools/sweep_round1.py --active ACO --aco-id 3 --ipr-id 3 --match-trades worse -o results/sanity_worse.csv
python tools/sweep_round1.py --active ACO --aco-id 3 --ipr-id 3 --match-trades none -o results/sanity_none.csv
```

Re-run the same configs with `all` (default) vs `worse` vs `none`. Rankings that flip wildly are less trustworthy.

## 6. Subset of days (faster iteration)

```bash
python tools/sweep_round1.py --active IPR --ipr-range 29:33 --aco-id 3 --days -2 -1 -o results/ipr_slope.csv
```

`--days` defaults to all round-1 days (-2, -1, 0).

## 7. IPR slope and quote bias (new config indices)

`IPR_CONFIGS` now includes:

- **29–33:** `slope` only (fair drift rate in `_ipr_compute_fair`).
- **34–38:** `quote_bias_ticks` only (shifts maker bid/ask after fair logic; positive = quote higher).

Example slope sweep:

```bash
python tools/sweep_round1.py --active IPR --aco-id 3 --ipr-range 29:33 -o results/ipr_slope_only.csv
```

## Composite “winner + Stage B” configs

Because each index is a **single** override block, there is **no** index that means “Stage A = index 2 **and** Stage B = index 10” without adding a **combined** row to `ACO_CONFIGS` / `IPR_CONFIGS` in [strageties/sweep_submission.py](../strageties/sweep_submission.py). For that experiment, add one dict that merges both override sets.
