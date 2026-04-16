"""
Test 01 - Match-mode robustness sweep
======================================

Run primo_explorer with its default config across:
    * 3 match-trade modes: all (optimistic), worse (realistic), none (taker floor)
    * 3 days: -2, -1, 0
    * 3 active-product settings: ACO-only, IPR-only, BOTH

27 backtests total.

Interpretation:
    `none` mode is pure take_positive (no maker fills ever). The floor it
    gives is "strategy PnL if we ignored maker fills entirely." Gap between
    `none` and `worse` is our realistic maker edge. Gap between `worse` and
    `all` is the "backtester artifact" credit primo currently leans on.

Output: results/primo_exploration/test_01_match_mode.csv + marginal tables.
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
MODES = ["all", "worse", "none"]
ACTIVES = ["ACO", "IPR", "BOTH"]


def main():
    tasks = []
    for day in DAYS:
        for mode in MODES:
            for active in ACTIVES:
                tasks.append({
                    "day": day,
                    "global_cfg": {"active": active},
                    "match_trades": mode,
                    # Label fields for the results CSV:
                    "mode": mode,
                    "active": active,
                })

    print(f"Running {len(tasks)} backtests...")
    results = run_many(tasks, workers=6)
    df = pd.DataFrame(results)
    out_path = OUT / "test_01_match_mode.csv"
    df.to_csv(out_path, index=False)
    print(f"\nSaved: {out_path}")

    # Pivot: mean PnL per (product, mode, active) across days
    print("\n" + "=" * 72)
    print("Mean PnL per day by (active, mode):")
    print("=" * 72)
    for active in ACTIVES:
        sub = df[df["active"] == active]
        if sub.empty:
            continue
        pv = sub.pivot_table(index="day", columns="mode", values="total",
                             aggfunc="mean").round(0)
        pv["mean"] = pv.mean(axis=1).round(0)
        print(f"\n  active={active} (total PnL):")
        print(pv.to_string())

    # Taker floor insight: compare `none` to `worse` and `all`
    print("\n" + "=" * 72)
    print("ACO PnL per mode (ACO-only runs):")
    print("=" * 72)
    sub = df[df["active"] == "ACO"]
    pv = sub.pivot_table(index="day", columns="mode", values="aco_pnl").round(0)
    pv["mean"] = pv.mean(axis=1).round(0)
    print(pv.to_string())

    print("\n" + "=" * 72)
    print("IPR PnL per mode (IPR-only runs):")
    print("=" * 72)
    sub = df[df["active"] == "IPR"]
    pv = sub.pivot_table(index="day", columns="mode", values="ipr_pnl").round(0)
    pv["mean"] = pv.mean(axis=1).round(0)
    print(pv.to_string())

    print("\n  Reading guide:")
    print("    `none`  = pure taker floor (no passive fills ever)")
    print("    `worse` = realistic live (passive only fills when trade strictly inside)")
    print("    `all`   = optimistic (passive shares prints at our own level)")


if __name__ == "__main__":
    main()
