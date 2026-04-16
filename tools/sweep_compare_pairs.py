#!/usr/bin/env python3
"""
Compare Stage-A-style pairs from sweep_round1 CSV (join vs improve_1 at same min_take_edge).

Each sweep index merges overrides onto baseline; Stage A pairs (0,1), (2,3), (4,5), (6,7) differ
only by maker_mode at a fixed min_take_edge — use this to read delta PnL for that A/B.

Usage:
  python tools/sweep_compare_pairs.py results.csv --product ASH_COATED_OSMIUM \\
      --id-column aco_config_id --pairs 0:1,2:3,4:5,6:7
"""

from __future__ import annotations

import argparse
from pathlib import Path

import pandas as pd


def main() -> None:
    parser = argparse.ArgumentParser(description="Delta PnL for index pairs (A vs B) from sweep CSV.")
    parser.add_argument("csv_path", type=Path)
    parser.add_argument("--product", required=True)
    parser.add_argument(
        "--id-column",
        choices=("aco_config_id", "ipr_config_id"),
        default="aco_config_id",
    )
    parser.add_argument(
        "--pairs",
        required=True,
        help="Comma-separated LO:HI pairs, e.g. 0:1,2:3,4:5",
    )
    args = parser.parse_args()

    df = pd.read_csv(args.csv_path)
    sub = df[df["product"] == args.product].copy()
    if sub.empty:
        raise SystemExit(f"No rows for product {args.product!r}")

    by_id = sub.groupby(args.id_column)["pnl"].sum()

    rows = []
    for part in args.pairs.split(","):
        part = part.strip()
        if ":" not in part:
            raise SystemExit(f"Bad pair {part!r}, expected LO:HI")
        lo_s, hi_s = part.split(":", 1)
        a, b = int(lo_s.strip()), int(hi_s.strip())
        pa = float(by_id.get(a, float("nan")))
        pb = float(by_id.get(b, float("nan")))
        rows.append(
            {
                "a": a,
                "b": b,
                "total_pnl_a": pa,
                "total_pnl_b": pb,
                "delta_b_minus_a": pb - pa if pd.notna(pa) and pd.notna(pb) else float("nan"),
            }
        )

    out = pd.DataFrame(rows)
    print(f"=== Pair deltas (B minus A) — {args.product} — {args.id_column} ===")
    print(out.to_string(index=False))
    print("\nInterpretation: For Stage A, pairs (0,1),(2,3),... are join vs improve_1 at same min_take_edge.")


if __name__ == "__main__":
    main()
