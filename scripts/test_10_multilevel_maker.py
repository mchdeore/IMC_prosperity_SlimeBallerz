"""
Test 10 - Multi-level maker quotes
===================================

primo's default maker posts ONE bid and ONE ask per tick. This test
evaluates whether layering quotes at multiple price levels captures
more volume (potentially useful when a big taker sweeps L1).

Configs tested on each product independently:
    baseline                    = [(1, 1.0)]                single-level, 100% at beat+1
    two_tier_60_40              = [(1, 0.6), (3, 0.4)]      60% at L1, 40% 3 ticks deeper
    three_tier_50_30_20         = [(1, 0.5), (2, 0.3), (4, 0.2)]
    deep_heavy_80_20            = [(1, 0.8), (5, 0.2)]      mostly L1, tiny deep tail

8 configs (4 per product) x 3 days x 1 match mode = 24 backtests.
Match = worse (realistic).

Output: results/primo_exploration/test_10_multilevel.csv + table.
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

CONFIGS = {
    "baseline":           None,   # single-level
    "two_tier_60_40":     [[1, 0.6], [3, 0.4]],
    "three_tier_50_30_20": [[1, 0.5], [2, 0.3], [4, 0.2]],
    "deep_heavy_80_20":   [[1, 0.8], [5, 0.2]],
}


def main():
    tasks = []
    for label, multi in CONFIGS.items():
        # Test against both products (ACO and IPR independently)
        for product in ["ACO", "IPR"]:
            for day in DAYS:
                if product == "ACO":
                    aco_cfg = {"multi_level": multi}
                    ipr_cfg = {}
                    active = "ACO"
                else:
                    aco_cfg = {}
                    ipr_cfg = {"multi_level": multi}
                    active = "IPR"
                tasks.append({
                    "day": day,
                    "aco_cfg": aco_cfg,
                    "ipr_a_cfg": ipr_cfg,
                    "global_cfg": {"active": active},
                    "match_trades": "worse",
                    "config_label": label,
                    "product": product,
                })

    print(f"Running {len(tasks)} multi-level backtests...")
    results = run_many(tasks, workers=6)
    df = pd.DataFrame(results)
    out_path = OUT / "test_10_multilevel.csv"
    df.to_csv(out_path, index=False)
    print(f"\nSaved: {out_path}")

    # Pivot per product
    for product in ["ACO", "IPR"]:
        sub = df[df["product"] == product]
        if sub.empty:
            continue
        pnl_col = "aco_pnl" if product == "ACO" else "ipr_pnl"
        pv = sub.pivot_table(
            index="config_label", columns="day",
            values=pnl_col, aggfunc="mean"
        ).round(0)
        pv["sum_3d"] = pv.sum(axis=1).round(0)
        pv["mean"] = pv[DAYS].mean(axis=1).round(0)
        pv = pv.sort_values("sum_3d", ascending=False)
        print(f"\n==== {product} PnL by multi-level config (--match-trades worse) ====")
        print(pv.to_string())


if __name__ == "__main__":
    main()
