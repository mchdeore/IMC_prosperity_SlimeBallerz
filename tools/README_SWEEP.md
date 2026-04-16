# Round 1 parameter sweep (Prosperity 4)

## Prerequisites

- Install dependencies: `pip install -r requirements.txt` (includes `prosperity4btest`).
- Run commands from the repository root, or use `python tools/...` with the project venv activated.

## Workflow

1. **Tune ASH_COATED_OSMIUM (ACO)** — isolate the ACO parameter grid while IPR is fixed (baseline index `3`):

   ```bash
   python tools/sweep_round1.py --active ACO --aco-range 0:29 --ipr-id 3 -o results_aco.csv
   ```

2. **Tune INTARIAN_PEPPER_ROOT (IPR)** — isolate IPR while ACO stays at baseline:

   ```bash
   python tools/sweep_round1.py --active IPR --aco-id 3 --ipr-range 0:38 -o results_ipr.csv
   ```

3. **Validate combined** — set both indices to your chosen values:

   ```bash
   python tools/sweep_round1.py --active BOTH --aco-id 12 --ipr-id 15 -o results_both.csv
   ```

## Ranges and indices

- `--aco-range LO:HI` / `--ipr-range LO:HI` are **inclusive** bounds on `ACO_CONFIG_ID` / `IPR_CONFIG_ID` (see `strageties/sweep_submission.py` for what each index means).
- Omit ranges to use single IDs: `--aco-id N` / `--ipr-id N`. Defaults match the file baselines (`3` for both) if you omit range and single ID.

## Analysis

Per-product totals and worst-day robustness (see `tools/sweep_analysis_example.py`):

```bash
python tools/sweep_analysis_example.py results_aco.csv --product ASH_COATED_OSMIUM --id-column aco_config_id
python tools/sweep_analysis_example.py results_ipr.csv --product INTARIAN_PEPPER_ROOT --id-column ipr_config_id
```

**Deeper exploration** (`tools/sweep_deep_analysis.py`): stage winners (best index within each sweep band, e.g. IPR slope 29–33), marginal “best value” per merged parameter, Spearman vs total PnL, and suggested follow-on grids. Example:

```bash
python tools/sweep_deep_analysis.py results_ipr.csv --product INTARIAN_PEPPER_ROOT --focus IPR
```

Indices **F** (slope) and **G** (`quote_bias_ticks`) are not factorial in `sweep_submission.py`; combining their best values requires a custom slope×bias grid or a BOTH matrix search.

## Environment variables (advanced)

`strageties/sweep_submission.py` reads optional **`SWEEP_ACTIVE`**, **`SWEEP_ACO_CONFIG_ID`**, **`SWEEP_IPR_CONFIG_ID`** at import time. The sweep tool sets these automatically; you only need them if you call `prosperity4btest` yourself.

## Data

Round 1 includes three days: **-2, -1, 0**. By default the sweep runs all three; pass **`--days -2`** (or any subset) to shorten runs. Use **`--data`** for a custom resources tree (same layout as `prosperity4bt/resources`). Use **`--match-trades`** (`all` / `worse` / `none`) to match `prosperity4btest` behavior.

## Follow-on experiments

See [README_REVEALING_TESTS.md](README_REVEALING_TESTS.md) for paired Stage-A comparisons, LODO scoring, BOTH matrices, slope/quote-bias indices (IPR **29–38**), and match-mode sanity checks.

## Full-grid / exhaustive sweeps

See [README_EXHAUSTIVE_SWEEP.md](README_EXHAUSTIVE_SWEEP.md) for **`sweep_exhaustive.py`**: all ACO indices, all IPR indices, and the full **BOTH** Cartesian product in one orchestrated batch (with optional chunking).

## Frozen “optimized” submission

[`strageties/optimized_submission.py`](../strageties/optimized_submission.py) hardcodes **ACO sweep index 2** + **IPR sweep index 33** (same logic as `sweep_submission.py`). Backtest with: `prosperity4btest strageties/optimized_submission.py 1`.
