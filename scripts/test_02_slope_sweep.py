"""
Test 02 - IPR slope fine-grain sweep
=====================================

Sweep IPR_A.slope over {0.0005, 0.0008, 0.001, 0.0012, 0.0015, 0.002,
0.0025, 0.003} across 3 days x 2 match modes (all, worse). IPR-only.

For slopes >= 0.0015 we also raise `bail_dev_threshold` to 9999 so the
bail does not misfire when the "cheat slope" legitimately makes fair
drift far from quotes. All other knobs stay at primo defaults (IPR-A).

Output: results/primo_exploration/test_02_slope_sweep.csv + pivoted table.
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
MODES = ["all", "worse"]
SLOPES = [0.0005, 0.0008, 0.001, 0.0012, 0.0015, 0.002, 0.0025, 0.003]


def main():
    tasks = []
    for slope in SLOPES:
        cfg = {"slope": slope}
        # Disable bail when slope is high and fair will legitimately
        # diverge from best quotes.
        if slope >= 0.0015:
            cfg["bail_dev_threshold"] = 9999
        for day in DAYS:
            for mode in MODES:
                tasks.append({
                    "day": day,
                    "ipr_a_cfg": cfg,
                    "global_cfg": {"active": "IPR"},
                    "match_trades": mode,
                    "slope": slope,
                    "mode": mode,
                })

    print(f"Running {len(tasks)} backtests...")
    results = run_many(tasks, workers=6)
    df = pd.DataFrame(results)
    out_path = OUT / "test_02_slope_sweep.csv"
    df.to_csv(out_path, index=False)
    print(f"\nSaved: {out_path}")

    # Pivot: IPR PnL per (day, slope) per mode
    for mode in MODES:
        sub = df[df["mode"] == mode]
        pv = sub.pivot_table(index="day", columns="slope",
                             values="ipr_pnl", aggfunc="mean").round(0)
        pv.loc["mean"] = pv.mean().round(0)
        pv.loc["worst_day"] = pv.iloc[:3].min().round(0)
        print(f"\n==== IPR PnL by slope, --match-trades {mode} ====")
        print(pv.to_string())

    # 3-day sum + worst-day ranking
    print("\n==== slope ranked by 3-day sum (under worse) ====")
    sub = df[df["mode"] == "worse"]
    agg = sub.groupby("slope")["ipr_pnl"].agg(
        sum_3d="sum", mean="mean", min_day="min")
    agg = agg.round(0).sort_values("sum_3d", ascending=False)
    print(agg.to_string())


if __name__ == "__main__":
    main()
