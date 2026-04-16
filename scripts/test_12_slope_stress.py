"""
Test 12 - Slope-sensitivity stress test
========================================

We can't actually change the real drift in the training data, but we
CAN run each candidate strategy with a MISCALIBRATED slope against the
true data, which simulates "live drift is weaker/stronger than the
strategy assumed."

If the strategy thinks slope is 0.002 and real drift is 0.001, fair
climbs too fast and take_positive fires on ~fair-priced asks (phantom
mispricing), causing aggressive overbuying that only pays off if drift
actually keeps up.

Candidate strategies (IPR-only, --match-trades worse):
    A) primo default (slope=0.001, quote_bias_ticks=3, clamp=True)
    B) primo + long_take_edge=-2 (aggressive ask-side)
    C) 176355-style (slope=0.003, no bias, bail off)

For each candidate, run with strategy_slope in {0.0005, 0.00075, 0.001,
0.00125, 0.0015}. The REAL drift is ~0.001 in training; lower
strategy_slope understates drift, higher overstates.

Actually since we can only run on the real data (which has real drift
~0.001), we are ONLY varying what the strategy THINKS the slope is,
not the actual market drift. This reveals: which strategy is LEAST
sensitive to its own slope assumption.

Output: results/primo_exploration/test_12_slope_stress.csv + PnL curves.
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
STRATEGY_SLOPES = [0.0005, 0.00075, 0.001, 0.00125, 0.0015]

CANDIDATES = {
    "primo_default": {
        # Matches primo_v3 IPR_A baseline except slope is per-run
        "quote_bias_ticks":   3,
        "bias_clamp_to_fair": True,
        "pressure_mode":      "long_bias",
        "bid_frac":           0.70,
        "ask_frac":           0.30,
        "bail_dev_threshold": 9999,   # disable bail for stress; we're
                                      # testing the strategy itself
    },
    "primo_longtake": {
        "quote_bias_ticks":   3,
        "bias_clamp_to_fair": True,
        "pressure_mode":      "long_bias",
        "bid_frac":           0.70,
        "ask_frac":           0.30,
        "long_take_edge":     -2,
        "bail_dev_threshold": 9999,
    },
    "176355_style": {
        # slope=0.003 aggressive; we override slope per-run below
        "quote_bias_ticks":   0,
        "bias_clamp_to_fair": False,
        "pressure_mode":      "long_bias",
        "bid_frac":           0.70,
        "ask_frac":           0.30,
        "bail_dev_threshold": 9999,
    },
}


def main():
    tasks = []
    for cand_name, base_cfg in CANDIDATES.items():
        for strat_slope in STRATEGY_SLOPES:
            cfg = dict(base_cfg)
            cfg["slope"] = strat_slope
            for day in DAYS:
                tasks.append({
                    "day": day,
                    "ipr_a_cfg": cfg,
                    "global_cfg": {"active": "IPR"},
                    "match_trades": "worse",
                    "candidate": cand_name,
                    "strat_slope": strat_slope,
                })

    print(f"Running {len(tasks)} stress backtests...")
    results = run_many(tasks, workers=6)
    df = pd.DataFrame(results)
    out_path = OUT / "test_12_slope_stress.csv"
    df.to_csv(out_path, index=False)
    print(f"\nSaved: {out_path}")

    # Pivot: IPR PnL mean across days per (candidate, strat_slope)
    pv = df.pivot_table(
        index="candidate", columns="strat_slope",
        values="ipr_pnl", aggfunc="mean"
    ).round(0)
    print("\n==== Mean IPR PnL per day per strategy_slope ====")
    print(pv.to_string())

    worst = df.pivot_table(
        index="candidate", columns="strat_slope",
        values="ipr_pnl", aggfunc="min"
    ).round(0)
    print("\n==== Worst-day IPR PnL (across 3 days) ====")
    print(worst.to_string())

    # Robustness = ratio of worst to best across slope assumptions
    print("\n==== Robustness summary ====")
    for cand in CANDIDATES:
        series = pv.loc[cand]
        best, worst_s = series.max(), series.min()
        dropoff = best - worst_s
        print(f"  {cand:20s} best={best:>7.0f}  worst={worst_s:>7.0f}  swing={dropoff:>6.0f}")

    print("\nInterpretation:")
    print("  Smaller `swing` = strategy is less sensitive to slope miscalibration.")
    print("  A candidate that scores high at slope=0.0015 but dies at slope=0.0005")
    print("  is fragile: if live drift is weaker than training, it loses big.")


if __name__ == "__main__":
    main()
