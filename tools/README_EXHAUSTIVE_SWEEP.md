# Exhaustive parameter sweeps (full grid + Cartesian BOTH)

Use [`sweep_exhaustive.py`](sweep_exhaustive.py) to run **all** `ACO_CONFIGS` and **all** `IPR_CONFIGS` in a structured way.

## What gets run

| Preset | Contents | Approx. `run_backtest` calls | Approx. CSV rows |
|--------|-----------|------------------------------|------------------|
| `isolated` | ACO-only: every ACO index, IPR fixed at 3; IPR-only: every IPR index, ACO fixed at 3 | 30×3 + 39×3 = **207** | 180 + 234 = **414** |
| `both-full` | `ACTIVE=BOTH`: every **(aco_id, ipr_id)** pair | 30×39×3 = **3,510** | **7,020** |
| `all` | `isolated` + `both-full` | **3,717** | **7,434** |

(Rows assume round 1 days **-2, -1, 0** and two products per day line in the CSV.)

## Commands

Full optimization bundle (long runtime — hours depending on CPU):

```bash
mkdir -p results/exhaustive_full
python tools/sweep_exhaustive.py --preset all --out-dir results/exhaustive_full
```

Only isolated (per-product tuning without cross-effects):

```bash
python tools/sweep_exhaustive.py --preset isolated --out-dir results/exhaustive_iso
```

Only the BOTH Cartesian (joint tuning):

```bash
python tools/sweep_exhaustive.py --preset both-full --out-dir results/exhaustive_both
```

Dry-run (print subprocess commands + `summary.json` without backtesting):

```bash
python tools/sweep_exhaustive.py --preset all --out-dir results/demo --dry-run
```

## Splitting the BOTH matrix (parallel machines / resume)

Run chunks of the ACO axis (or IPR axis) into separate files, then analyze each or concatenate CSVs:

```bash
python tools/sweep_exhaustive.py --preset both-full --aco-chunk 0:14 --out-dir results/both_c0
python tools/sweep_exhaustive.py --preset both-full --aco-chunk 15:29 --out-dir results/both_c1
```

Optional: merge with pandas:

```python
import pandas as pd
pd.concat([pd.read_csv("results/both_c0/sweep_both_cartesian.csv"),
           pd.read_csv("results/both_c1/sweep_both_cartesian.csv")]).to_csv("merged.csv", index=False)
```

## Analysis

- Per-product totals: [`sweep_analysis_example.py`](sweep_analysis_example.py) with `--product` and `--id-column`.
- For BOTH CSVs, group by `(aco_config_id, ipr_config_id)` and sum `pnl` for **portfolio** PnL, or filter by product for each good.
- `summary.json` in the output directory records dimensions and estimates.

## Notes

- **Interpretation:** Isolated sweeps do **not** include interaction between ACO and IPR; the **both-full** run is required for joint optimum search.
- **Combinatorics:** A full Cartesian run is large; start with [`README_REVEALING_TESTS.md`](README_REVEALING_TESTS.md) style zooms if runtime is too high.
- Config counts follow `strageties/sweep_submission.py` (`ACO_CONFIGS`, `IPR_CONFIGS`).
