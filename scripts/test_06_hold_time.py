"""
Test 06 - Position hold-time distribution
==========================================

For each product, pair buy fills with subsequent sell fills in FIFO
order. Hold time = timestamp of the sell that closes each unit. Longer
hold times on IPR confirm drift capture is working; short hold times
on ACO show healthy spread-capture churn.

Runs primo_explorer under default config, 3 days, match=worse, with
order_log=True. Parses the backtest log's Trade History section.

Output: results/primo_exploration/test_06_hold_time.csv + histograms.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _backtest_helpers import day_to_arg, TRADER
from _log_parser import split_sections, get_our_fills

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "results" / "primo_exploration"
LOG_DIR = OUT / "logs"
OUT.mkdir(parents=True, exist_ok=True)
LOG_DIR.mkdir(parents=True, exist_ok=True)

DAYS = [-2, -1, 0]


def run_and_get_log(day):
    out_log = LOG_DIR / f"holdtime_day_{day}.log"
    env = os.environ.copy()
    env["EXPL_GLOBAL"] = json.dumps({"order_log": True})
    cmd = [
        "prosperity4btest", str(TRADER), day_to_arg(day),
        "--out", str(out_log), "--no-progress",
        "--match-trades", "worse",
    ]
    subprocess.run(cmd, cwd=str(ROOT), env=env,
                   capture_output=True, text=True, timeout=180)
    return out_log


def fifo_pair(fills_df: pd.DataFrame):
    """
    Given a DataFrame of fills for one product (ordered by timestamp),
    each row has (timestamp, side, qty, price), returns a list of
    (open_ts, close_ts, hold_ticks, open_side, open_price, close_price, qty).

    Uses FIFO: the oldest open position on the opposite side closes first.
    If a long position is still open at EOD, it is left unpaired.
    """
    open_buys = []    # list of dicts: {ts, qty, price}
    open_sells = []
    paired = []

    for _, row in fills_df.iterrows():
        if row["side"] == "B":
            remaining = row["qty"]
            # Close opposing shorts first
            while remaining > 0 and open_sells:
                oldest = open_sells[0]
                take = min(remaining, oldest["qty"])
                paired.append({
                    "open_side": "S",
                    "open_ts":   oldest["ts"],
                    "open_price": oldest["price"],
                    "close_ts":  row["timestamp"],
                    "close_price": row["price"],
                    "qty":       take,
                    "hold_ticks": (row["timestamp"] - oldest["ts"]) // 100,
                })
                oldest["qty"] -= take
                remaining -= take
                if oldest["qty"] == 0:
                    open_sells.pop(0)
            if remaining > 0:
                open_buys.append({
                    "ts": row["timestamp"], "qty": remaining, "price": row["price"]
                })
        else:
            remaining = row["qty"]
            while remaining > 0 and open_buys:
                oldest = open_buys[0]
                take = min(remaining, oldest["qty"])
                paired.append({
                    "open_side": "B",
                    "open_ts":   oldest["ts"],
                    "open_price": oldest["price"],
                    "close_ts":  row["timestamp"],
                    "close_price": row["price"],
                    "qty":       take,
                    "hold_ticks": (row["timestamp"] - oldest["ts"]) // 100,
                })
                oldest["qty"] -= take
                remaining -= take
                if oldest["qty"] == 0:
                    open_buys.pop(0)
            if remaining > 0:
                open_sells.append({
                    "ts": row["timestamp"], "qty": remaining, "price": row["price"]
                })
    return paired, open_buys, open_sells


def main():
    all_pairs = []
    summary_rows = []

    for day in DAYS:
        print(f"  Running day {day}...")
        log_path = run_and_get_log(day)
        _, trade_text = split_sections(log_path)
        fills = get_our_fills(trade_text)
        if not fills:
            print(f"    No fills found in {log_path}")
            continue
        fills_df = pd.DataFrame(fills)

        for symbol, short_name in [("ASH_COATED_OSMIUM", "ACO"),
                                    ("INTARIAN_PEPPER_ROOT", "IPR")]:
            sub = fills_df[fills_df["symbol"] == symbol].sort_values("timestamp").reset_index(drop=True)
            paired, open_buys, open_sells = fifo_pair(sub)

            for p in paired:
                p["day"] = day
                p["product"] = short_name
                all_pairs.append(p)

            if paired:
                hold_ticks = [p["hold_ticks"] for p in paired for _ in range(p["qty"])]
                open_long_units = sum(b["qty"] for b in open_buys)
                open_short_units = sum(s["qty"] for s in open_sells)
                summary_rows.append({
                    "day":                    day,
                    "product":                short_name,
                    "n_closed_lots":          int(sum(p["qty"] for p in paired)),
                    "n_open_long_at_eod":     open_long_units,
                    "n_open_short_at_eod":    open_short_units,
                    "median_hold_ticks":      float(pd.Series(hold_ticks).median()),
                    "mean_hold_ticks":        round(pd.Series(hold_ticks).mean(), 1),
                    "p25_hold_ticks":         float(pd.Series(hold_ticks).quantile(0.25)),
                    "p75_hold_ticks":         float(pd.Series(hold_ticks).quantile(0.75)),
                    "max_hold_ticks":         max(hold_ticks),
                    "pct_held_1000plus":      round(100 * (pd.Series(hold_ticks) >= 1000).mean(), 1),
                })

    summary = pd.DataFrame(summary_rows)
    out_path = OUT / "test_06_hold_time.csv"
    summary.to_csv(out_path, index=False)
    print(f"\nSaved summary: {out_path}")

    pairs_path = OUT / "test_06_hold_time_pairs.csv"
    pd.DataFrame(all_pairs).to_csv(pairs_path, index=False)
    print(f"Saved raw pairs: {pairs_path}")

    print("\n==== Hold-time summary ====")
    print(summary.to_string(index=False))

    print("\n==== Aggregated per product (3-day means) ====")
    agg = summary.groupby("product").agg(
        lots_closed=("n_closed_lots", "sum"),
        open_long_eod=("n_open_long_at_eod", "sum"),
        open_short_eod=("n_open_short_at_eod", "sum"),
        median_hold=("median_hold_ticks", "mean"),
        mean_hold=("mean_hold_ticks", "mean"),
        pct_long_holds=("pct_held_1000plus", "mean"),
    ).round(1)
    print(agg.to_string())

    print("\nInterpretation:")
    print("  ACO: short holds (median < 100 ticks) = fast spread capture, healthy")
    print("  IPR: long holds (median > 500 ticks) + high %held_1000+ = drift capture")
    print("  High open_long_at_eod on IPR = positions pinned at max, couldn't unwind")


if __name__ == "__main__":
    main()
