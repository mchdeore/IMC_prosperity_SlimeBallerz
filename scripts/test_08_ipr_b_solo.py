"""
Test 08 - IPR-B (momentum) solo performance
============================================

Force `GLOBAL['force_mode'] = 'B'` so the IPR dispatcher always runs
the momentum strategy, never IPR-A. This isolates IPR-B's performance
to validate our fallback.

Sweep over:
    roc_window        in {10, 20, 50, 100}   (4 values)
    skew_per_roc_unit in {500, 1000, 2000, 5000}   (4 values)
    max_skew_ticks    in {1, 2, 3, 5}   (4 values)

= 64 configs x 3 days = 192 backtests. Match-trades=worse (realistic).

Interpretation:
    IPR-A typically scores ~77k/day. If best IPR-B config >= 40k/day,
    the fallback is solid (a graceful degradation). If it tanks below
    20k/day, we should either fix IPR-B or treat it as emergency-stop
    logic rather than continuing to trade.

Output: results/primo_exploration/test_08_ipr_b_solo.csv + top configs.
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
ROC_WINDOWS = [10, 20, 50, 100]
SKEW_UNITS = [500, 1000, 2000, 5000]
MAX_SKEWS = [1, 2, 3, 5]


def main():
    tasks = []
    for roc_window in ROC_WINDOWS:
        for skew_unit in SKEW_UNITS:
            for max_skew in MAX_SKEWS:
                cfg_b = {
                    "roc_window":        roc_window,
                    "skew_per_roc_unit": skew_unit,
                    "max_skew_ticks":    max_skew,
                }
                for day in DAYS:
                    tasks.append({
                        "day": day,
                        "ipr_b_cfg": cfg_b,
                        "global_cfg": {
                            "active": "IPR",
                            "force_mode": "B",
                        },
                        "match_trades": "worse",
                        "roc_window":        roc_window,
                        "skew_per_roc_unit": skew_unit,
                        "max_skew_ticks":    max_skew,
                    })

    print(f"Running {len(tasks)} IPR-B-solo backtests...")
    results = run_many(tasks, workers=8, progress_every=40)
    df = pd.DataFrame(results)
    out_path = OUT / "test_08_ipr_b_solo.csv"
    df.to_csv(out_path, index=False)
    print(f"\nSaved: {out_path}")

    agg = (df.groupby(["roc_window", "skew_per_roc_unit", "max_skew_ticks"])
             .agg(sum_3d=("ipr_pnl", "sum"),
                  mean=("ipr_pnl", "mean"),
                  worst=("ipr_pnl", "min"))
             .reset_index()
             .sort_values("sum_3d", ascending=False))

    print("\n==== Top 10 IPR-B configs ====")
    print(agg.head(10).round(0).to_string(index=False))
    print("\n==== Bottom 5 IPR-B configs ====")
    print(agg.tail(5).round(0).to_string(index=False))

    # Overall IPR-B-solo mean/median
    all_pnls = df["ipr_pnl"]
    print("\n==== Summary across all configs ====")
    print(f"  median 3-day-sum: {agg['sum_3d'].median():.0f}")
    print(f"  mean   3-day-sum: {agg['sum_3d'].mean():.0f}")
    print(f"  best   3-day-sum: {agg['sum_3d'].max():.0f}")
    print(f"  worst  3-day-sum: {agg['sum_3d'].min():.0f}")
    print("\nFor comparison, IPR-A-solo under --match-trades worse earns ~232k/3d.")
    print("IPR-B good = >=120k/3d; ok = >=60k; broken = <30k or negative.")


if __name__ == "__main__":
    main()
