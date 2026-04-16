"""
Test 13 - End-of-day position distribution
============================================

For each of:
    primo_default       (primo_explorer default)
    primo_longtake      (primo_explorer + long_take_edge=-2)
    176355_style        (primo_explorer + slope=0.003, bail off)
across 3 days, run a full backtest with order_log=True, then parse the
log to find the final tick's position for each product.

Interpretation:
    +80 at EOD  => saturated long (drift thesis fully exploited)
    +60 to +79  => accumulating, didn't quite max out
    -20 to +20  => drifting near flat (maker is churning without net bias)
    any short   => uh oh, strategy went wrong-way into drift

Output: results/primo_exploration/test_13_eod_position.csv + table.

Also tracks POSITION-OVER-TIME via parsing every tick's `pos=` from
the order log, so we can see whether the strategy gets to +80 early
(slope=0.003 cheat) or late (primo's slower accumulation).
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _backtest_helpers import day_to_arg, TRADER

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "results" / "primo_exploration"
OUT.mkdir(parents=True, exist_ok=True)
LOG_DIR = OUT / "logs"
LOG_DIR.mkdir(parents=True, exist_ok=True)

DAYS = [-2, -1, 0]

CANDIDATES = {
    "primo_default":  {"ipr_a_cfg": {}, "global_cfg": {}, "trader": TRADER},
    "primo_longtake": {"ipr_a_cfg": {"long_take_edge": -2}, "global_cfg": {}, "trader": TRADER},
    "176355_style":   {"ipr_a_cfg": {"slope": 0.003, "bail_dev_threshold": 9999,
                                      "quote_bias_ticks": 0, "bias_clamp_to_fair": False},
                       "global_cfg": {}, "trader": TRADER},
}

ORDER_LINE_RE = re.compile(
    r"\[ORDER\] t=(\d+) p=(\w+) phase=\w+ side=\w+ price=-?\d+ qty=\d+ fair=-?[\d.]+ pos=(-?\d+)"
)


def run_and_capture(name, cfg, day):
    out_log = LOG_DIR / f"{name}_day_{day}.log"
    env = os.environ.copy()
    if cfg.get("ipr_a_cfg"):
        env["EXPL_IPR_A_CFG"] = json.dumps(cfg["ipr_a_cfg"])
    global_cfg = {"order_log": True}
    global_cfg.update(cfg.get("global_cfg", {}))
    env["EXPL_GLOBAL"] = json.dumps(global_cfg)

    cmd = [
        "prosperity4btest", str(cfg["trader"]), day_to_arg(day),
        "--out", str(out_log),
        "--no-progress",
        "--match-trades", "worse",
    ]
    result = subprocess.run(
        cmd, cwd=str(ROOT), env=env,
        capture_output=True, text=True, timeout=180
    )
    return result.returncode == 0, out_log


def parse_log_positions(log_path):
    """
    Returns a DataFrame with columns [timestamp, product, pos] from
    the [ORDER] lines in the sandbox logs of a backtest output.
    Note: multiple orders per tick share the same `pos` value (which
    is position BEFORE this tick's orders execute), so we dedupe.
    """
    rows = []
    with open(log_path, "r") as fh:
        for line in fh:
            for m in ORDER_LINE_RE.finditer(line):
                ts, product, pos = m.groups()
                rows.append({
                    "timestamp": int(ts),
                    "product": product,
                    "pos": int(pos),
                })
    if not rows:
        return pd.DataFrame(columns=["timestamp", "product", "pos"])
    df = pd.DataFrame(rows)
    # One row per (timestamp, product)
    df = df.drop_duplicates(subset=["timestamp", "product"], keep="first")
    return df


def main():
    summary_rows = []
    trajectories = {}
    for name, cfg in CANDIDATES.items():
        for day in DAYS:
            print(f"  Running {name} on day {day}...")
            ok, log_path = run_and_capture(name, cfg, day)
            if not ok:
                print(f"    FAILED")
                continue
            traj = parse_log_positions(log_path)
            trajectories[(name, day)] = traj

            for product in ["ACO", "IPR"]:
                sub = traj[traj["product"] == product]
                if len(sub) == 0:
                    continue
                final_pos = int(sub.iloc[-1]["pos"])
                max_pos = int(sub["pos"].max())
                min_pos = int(sub["pos"].min())
                median_pos = float(sub["pos"].median())
                summary_rows.append({
                    "candidate": name,
                    "day": day,
                    "product": product,
                    "final_pos": final_pos,
                    "max_pos":   max_pos,
                    "min_pos":   min_pos,
                    "median_pos": round(median_pos, 1),
                    "n_ticks":    len(sub),
                    # Tick at which position first hits +80 (if ever)
                    "ts_first_max": int(sub[sub["pos"] >= 80]["timestamp"].min())
                        if (sub["pos"] >= 80).any() else -1,
                })

    df = pd.DataFrame(summary_rows)
    out_path = OUT / "test_13_eod_position.csv"
    df.to_csv(out_path, index=False)
    print(f"\nSaved: {out_path}")

    print("\n==== EOD and mid-day stats ====")
    for product in ["ACO", "IPR"]:
        sub = df[df["product"] == product]
        if sub.empty:
            continue
        pv = sub.pivot_table(
            index="candidate", columns="day",
            values="final_pos"
        )
        print(f"\nFinal position, product={product}:")
        print(pv.to_string())

        pv2 = sub.pivot_table(
            index="candidate", columns="day",
            values="ts_first_max"
        )
        print(f"\nFirst timestamp reaching |pos|>=80 (-1 = never), product={product}:")
        print(pv2.to_string())

    print("\n  Interpretation:")
    print("    IPR: reaching +80 early (ts < 300000) means strategy heavily")
    print("         front-loaded drift capture (like slope=0.003 trick).")
    print("    IPR: median_pos near +80 means strategy stayed saturated (good).")
    print("    ACO: final_pos near 0 means effective flatten.")


if __name__ == "__main__":
    main()
