#!/usr/bin/env python3
"""
Example analysis for CSV output from tools/sweep_round1.py.

Computes per (config_id, product): total PnL over 3 days, worst single-day PnL, and
prints a sorted leaderboard. Filter by product for ACO vs IPR optimization.

Usage:
  python tools/sweep_analysis_example.py results.csv --product ASH_COATED_OSMIUM
  python tools/sweep_analysis_example.py results.csv --product INTARIAN_PEPPER_ROOT --id-column aco_config_id
"""

from __future__ import annotations

import argparse
from pathlib import Path

import pandas as pd


def main() -> None:
    parser = argparse.ArgumentParser(description="Summarize sweep_round1 CSV for per-product optimization.")
    parser.add_argument("csv_path", type=Path, help="Output from sweep_round1.py")
    parser.add_argument(
        "--product",
        required=True,
        help="ASH_COATED_OSMIUM or INTARIAN_PEPPER_ROOT",
    )
    parser.add_argument(
        "--id-column",
        choices=("aco_config_id", "ipr_config_id"),
        default="aco_config_id",
        help="Which config index to group by (use ipr_config_id for IPR sweeps).",
    )
    parser.add_argument("--top", type=int, default=15, help="Rows to show per sort.")
    parser.add_argument(
        "--lodo",
        action="store_true",
        help="Leave-one-day-out: for each config, sum PnL on 2 of 3 days; report min/mean of those 3 sums.",
    )
    args = parser.parse_args()

    df = pd.read_csv(args.csv_path)
    sub = df[df["product"] == args.product].copy()
    if sub.empty:
        raise SystemExit(f"No rows for product {args.product!r}")

    g = sub.groupby(args.id_column, as_index=False).agg(
        total_pnl=("pnl", "sum"),
        worst_day_pnl=("pnl", "min"),
        best_day_pnl=("pnl", "max"),
        n_days=("day", "count"),
    )
    g["mean_day_pnl"] = g["total_pnl"] / g["n_days"]

    print(f"=== Leaderboard by total_pnl (top {args.top}) — {args.product} ===")
    print(g.sort_values("total_pnl", ascending=False).head(args.top).to_string(index=False))

    print(f"\n=== Robustness: highest worst_day_pnl (top {args.top}) ===")
    print(g.sort_values("worst_day_pnl", ascending=False).head(args.top).to_string(index=False))

    if args.lodo:
        days = sorted(sub["day"].unique())
        if len(days) < 2:
            raise SystemExit("--lodo needs at least 2 distinct days in CSV")
        lodo_rows = []
        for cid, grp in sub.groupby(args.id_column):
            by_day = grp.groupby("day")["pnl"].sum()
            folds = []
            for d in days:
                other = [x for x in days if x != d]
                folds.append(float(by_day.reindex(other).fillna(0).sum()))
            lodo_rows.append(
                {
                    args.id_column: cid,
                    "lodo_min": min(folds),
                    "lodo_mean": sum(folds) / len(folds),
                    "lodo_max": max(folds),
                    "total_pnl": float(by_day.sum()),
                }
            )
        lg = pd.DataFrame(lodo_rows)
        print(f"\n=== Leave-one-day-out fold sums (top {args.top} by lodo_min) — {args.product} ===")
        print(lg.sort_values("lodo_min", ascending=False).head(args.top).to_string(index=False))


if __name__ == "__main__":
    main()
