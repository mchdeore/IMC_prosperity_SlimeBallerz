"""
Test 11 - Time-based aggression ramp for IPR
=============================================

Idea: instead of using slope=0.003 to force fair too high (brittle),
ramp the `min_take_edge` DOWN as the day progresses. Early in the day
require edge=1 (only take genuine mispricings); late in the day allow
edge=-2 (buy asks even a couple ticks above fair, banking on drift).

Config shape: time_edge_ramp = {"start_edge": S, "end_edge": E, "end_ts": T}
Ramp is linear from edge=S at t=0 to edge=E at t=end_ts.

Configs tested (IPR-only):
    baseline                 = None   (constant edge=1)
    ramp_slow                = {start: 1, end: -2, end_ts: 1000000}  gradual over full day
    ramp_half                = {start: 1, end: -2, end_ts: 500000}   reaches -2 by midday
    ramp_fast                = {start: 1, end: -3, end_ts: 300000}   aggressive by ~30% day
    extreme_end              = {start: 1, end: -5, end_ts: 1000000}  very aggressive late

5 configs x 3 days x 2 modes = 30 backtests.

Output: results/primo_exploration/test_11_time_aggression.csv + tables.
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
MODES = ["worse", "all"]

CONFIGS = {
    "baseline":    None,
    "ramp_slow":   {"start_edge": 1, "end_edge": -2, "end_ts": 1000000},
    "ramp_half":   {"start_edge": 1, "end_edge": -2, "end_ts": 500000},
    "ramp_fast":   {"start_edge": 1, "end_edge": -3, "end_ts": 300000},
    "extreme_end": {"start_edge": 1, "end_edge": -5, "end_ts": 1000000},
}


def main():
    tasks = []
    for label, ramp in CONFIGS.items():
        cfg = {"time_edge_ramp": ramp}
        for day in DAYS:
            for mode in MODES:
                tasks.append({
                    "day": day,
                    "ipr_a_cfg": cfg,
                    "global_cfg": {"active": "IPR"},
                    "match_trades": mode,
                    "ramp_label": label,
                    "mode": mode,
                })

    print(f"Running {len(tasks)} time-ramp backtests...")
    results = run_many(tasks, workers=6)
    df = pd.DataFrame(results)
    out_path = OUT / "test_11_time_aggression.csv"
    df.to_csv(out_path, index=False)
    print(f"\nSaved: {out_path}")

    for mode in MODES:
        sub = df[df["mode"] == mode]
        pv = sub.pivot_table(
            index="ramp_label", columns="day",
            values="ipr_pnl", aggfunc="mean"
        ).round(0)
        pv["sum_3d"] = pv.sum(axis=1).round(0)
        pv["mean"] = pv[DAYS].mean(axis=1).round(0)
        pv = pv.sort_values("sum_3d", ascending=False)
        print(f"\n==== IPR PnL by time_edge_ramp, --match-trades {mode} ====")
        print(pv.to_string())


if __name__ == "__main__":
    main()
