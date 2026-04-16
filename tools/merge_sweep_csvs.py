#!/usr/bin/env python3
"""Concatenate sweep_round1 CSVs (e.g. BOTH chunks) into one file. Skips duplicate headers."""

from __future__ import annotations

import argparse
from pathlib import Path

import pandas as pd


def main() -> None:
    p = argparse.ArgumentParser(description="Merge sweep CSVs vertically.")
    p.add_argument("inputs", nargs="+", type=Path, help="Input CSV paths")
    p.add_argument("-o", "--output", type=Path, required=True)
    args = p.parse_args()
    dfs = [pd.read_csv(f) for f in args.inputs]
    out = pd.concat(dfs, ignore_index=True)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    out.to_csv(args.output, index=False)
    print(f"Wrote {len(out)} rows to {args.output}")


if __name__ == "__main__":
    main()
