"""
Test 09 - IPR long_take_edge sweep
===================================

The exploration_trader had a `long_take_edge` knob - an ask-side-only
take edge override. Smaller (or negative) values mean "more aggressive
buying" (take asks even when they're near/above fair).

We sweep:
    long_take_edge    in {None, 1, 0, -1, -2, -3, -5}
    quote_bias_ticks  in {0, 1, 2, 3}
    bias_clamp_to_fair in {True, False}

= 56 configs x 3 days x 2 modes = 336 backtests. IPR-only, default
otherwise. Match-trades = worse and all (so we see how aggressive takes
behave under both fill models).

Output: results/primo_exploration/test_09_long_take_edge.csv + tables.
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
LONG_TAKES = [None, 1, 0, -1, -2, -3, -5]
BIASES = [0, 1, 2, 3]
CLAMPS = [True, False]
MODES = ["worse", "all"]


def main():
    tasks = []
    for lte in LONG_TAKES:
        for bias in BIASES:
            for clamp in CLAMPS:
                cfg = {
                    "long_take_edge":     lte,
                    "quote_bias_ticks":   bias,
                    "bias_clamp_to_fair": clamp,
                }
                for day in DAYS:
                    for mode in MODES:
                        tasks.append({
                            "day": day,
                            "ipr_a_cfg": cfg,
                            "global_cfg": {"active": "IPR"},
                            "match_trades": mode,
                            "long_take_edge": ("none" if lte is None else lte),
                            "quote_bias_ticks": bias,
                            "bias_clamp_to_fair": clamp,
                            "mode": mode,
                        })

    print(f"Running {len(tasks)} backtests...")
    results = run_many(tasks, workers=8, progress_every=40)
    df = pd.DataFrame(results)
    out_path = OUT / "test_09_long_take_edge.csv"
    df.to_csv(out_path, index=False)
    print(f"\nSaved: {out_path}")

    # Marginal: mean IPR PnL per long_take_edge under worse mode
    print("\n==== Mean IPR PnL per day by long_take_edge (--match-trades worse) ====")
    worse = df[df["mode"] == "worse"]
    pv = worse.pivot_table(
        index="quote_bias_ticks",
        columns="long_take_edge",
        values="ipr_pnl",
        aggfunc="mean"
    ).round(0)
    print("\nquote_bias_ticks (rows) x long_take_edge (cols), bias_clamp=True:")
    t = worse[worse["bias_clamp_to_fair"]]
    pv_t = t.pivot_table(
        index="quote_bias_ticks", columns="long_take_edge",
        values="ipr_pnl", aggfunc="mean").round(0)
    print(pv_t.to_string())

    print("\nquote_bias_ticks (rows) x long_take_edge (cols), bias_clamp=False:")
    f = worse[~worse["bias_clamp_to_fair"]]
    pv_f = f.pivot_table(
        index="quote_bias_ticks", columns="long_take_edge",
        values="ipr_pnl", aggfunc="mean").round(0)
    print(pv_f.to_string())

    # Top 10 configs
    agg = (df.groupby(["long_take_edge", "quote_bias_ticks", "bias_clamp_to_fair", "mode"])
             .agg(sum_3d=("ipr_pnl", "sum"),
                  mean=("ipr_pnl", "mean"),
                  worst=("ipr_pnl", "min"))
             .reset_index()
             .sort_values("sum_3d", ascending=False))
    print("\n==== Top 10 configs by 3-day sum ====")
    print(agg.head(10).round(0).to_string(index=False))

    print("\n==== Top 10 configs (match-trades=worse only) ====")
    agg_w = agg[agg["mode"] == "worse"].sort_values("sum_3d", ascending=False)
    print(agg_w.head(10).round(0).to_string(index=False))


if __name__ == "__main__":
    main()
