"""
Test 03 - ACO parameter sweep
==============================

Cartesian sweep over:
    soft_cap      in {65, 70, 75, 80}       (4 values)
    ema_alpha_new in {0.05, 0.10, 0.15, 0.25}  (4 values)
    fair_levels   in {[1], [1,2], [2,3], [1,2,3], [1,2,3,4]}  (5 values)

= 80 configs x 3 days = 240 ACO-only backtests.

Uses --match-trades worse (realistic fill model).

Output: results/primo_exploration/test_03_aco_sweep.csv + marginal tables.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _backtest_helpers import run_many

import pandas as pd

OUT = Path(__file__).resolve().parents[1] / "results" / "primo_exploration"
OUT.mkdir(parents=True, exist_ok=True)

DAYS = [-2, -1, 0]
SOFT_CAPS = [65, 70, 75, 80]
EMA_ALPHAS = [0.05, 0.10, 0.15, 0.25]
FAIR_LEVELS = [[1], [1, 2], [2, 3], [1, 2, 3], [1, 2, 3, 4]]


def _levels_label(levels):
    return "L" + ",".join(str(x) for x in levels)


def main():
    tasks = []
    for soft_cap in SOFT_CAPS:
        for alpha in EMA_ALPHAS:
            for levels in FAIR_LEVELS:
                cfg = {
                    "soft_cap":      soft_cap,
                    "ema_alpha_new": alpha,
                    "fair_levels":   levels,
                }
                for day in DAYS:
                    tasks.append({
                        "day": day,
                        "aco_cfg": cfg,
                        "global_cfg": {"active": "ACO"},
                        "match_trades": "worse",
                        "soft_cap": soft_cap,
                        "ema_alpha_new": alpha,
                        "fair_levels": _levels_label(levels),
                    })

    print(f"Running {len(tasks)} backtests...")
    results = run_many(tasks, workers=8, progress_every=40)
    df = pd.DataFrame(results)
    out_path = OUT / "test_03_aco_sweep.csv"
    df.to_csv(out_path, index=False)
    print(f"\nSaved: {out_path}")

    # Aggregate 3-day sum per config
    agg = (df.groupby(["soft_cap", "ema_alpha_new", "fair_levels"])
             ["aco_pnl"].sum().reset_index()
             .rename(columns={"aco_pnl": "sum_3d"}))
    worst = (df.groupby(["soft_cap", "ema_alpha_new", "fair_levels"])
               ["aco_pnl"].min().reset_index()
               .rename(columns={"aco_pnl": "worst_day"}))
    merged = agg.merge(worst, on=["soft_cap", "ema_alpha_new", "fair_levels"])
    merged = merged.sort_values("sum_3d", ascending=False)

    print("\n==== Top 10 configs by 3-day sum ====")
    print(merged.head(10).round(0).to_string(index=False))

    print("\n==== Bottom 5 configs by 3-day sum ====")
    print(merged.tail(5).round(0).to_string(index=False))

    # Marginal: mean PnL by each knob (averaged over others)
    print("\n==== Marginals (mean 3-day sum, averaging over other knobs) ====")
    for col in ["soft_cap", "ema_alpha_new", "fair_levels"]:
        marg = merged.groupby(col)["sum_3d"].mean().round(0).sort_values(ascending=False)
        print(f"\nBy {col}:")
        print(marg.to_string())


if __name__ == "__main__":
    main()
