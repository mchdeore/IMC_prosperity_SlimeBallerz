#!/usr/bin/env python3
"""
Batch backtest strageties/sweep_submission.py on IMC Prosperity 4 round 1 (days -2, -1, 0).

Uses prosperity4bt in-process: sets SWEEP_* env vars, reloads the strategy module per run,
runs run_backtest per day, records per-product PnL at the last timestamp of each day.

Example:
  python tools/sweep_round1.py --active ACO --aco-range 0:7 --ipr-id 3 -o results.csv
  python tools/sweep_round1.py --active IPR --aco-id 3 --ipr-range 0:28 -o ipr.csv
  python tools/sweep_round1.py --active BOTH --aco-id 12 --ipr-id 15 -o both.csv
"""

from __future__ import annotations

import argparse
import csv
import importlib
import json
import os
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# Repo root (parent of tools/)
REPO_ROOT = Path(__file__).resolve().parent.parent

ROUND_1_DAYS: Tuple[int, ...] = (-2, -1, 0)


def _parse_range(spec: str, nmax: int, label: str) -> List[int]:
    spec = spec.strip()
    if ":" in spec:
        a_str, b_str = spec.split(":", 1)
        lo = int(a_str.strip())
        hi = int(b_str.strip())
    else:
        lo = hi = int(spec)
    if lo > hi:
        lo, hi = hi, lo
    if lo < 0 or hi >= nmax:
        raise SystemExit(f"{label} range [{lo}, {hi}] out of bounds for [0, {nmax - 1}]")
    return list(range(lo, hi + 1))


def _pnl_last_timestamp_by_product(result: Any) -> Dict[str, float]:
    """Mirror prosperity4bt print_day_summary: last timestamp rows only."""
    if not result.activity_logs:
        return {}
    last_ts = result.activity_logs[-1].timestamp
    out: Dict[str, float] = {}
    for row in reversed(result.activity_logs):
        if row.timestamp != last_ts:
            break
        product = row.columns[2]
        pnl = float(row.columns[-1])
        out[str(product)] = pnl
    return out


def _ensure_sweep_module():
    """Load sweep_submission with prosperity4bt datamodel shim (same as prosperity4btest)."""
    algo_path = REPO_ROOT / "strageties" / "sweep_submission.py"
    if not algo_path.is_file():
        raise SystemExit(f"Algorithm not found: {algo_path}")

    parent = str(algo_path.parent)
    if parent not in sys.path:
        sys.path.insert(0, parent)

    from prosperity4bt import datamodel as prosperity_datamodel

    sys.modules["datamodel"] = prosperity_datamodel

    import sweep_submission as ss

    return ss, algo_path


def _parse_match_trades(name: str) -> Any:
    from prosperity4bt.models import TradeMatchingMode

    m = {
        "all": TradeMatchingMode.all,
        "worse": TradeMatchingMode.worse,
        "none": TradeMatchingMode.none,
    }
    if name not in m:
        raise SystemExit(f"--match-trades must be one of {list(m.keys())}, got {name!r}")
    return m[name]


def _run_day(
    ss: Any,
    file_reader: Any,
    round_num: int,
    day_num: int,
    trade_matching_mode: Any,
) -> Any:
    from prosperity4bt.runner import run_backtest

    trader = ss.Trader()
    return run_backtest(
        trader,
        file_reader,
        round_num,
        day_num,
        print_output=False,
        trade_matching_mode=trade_matching_mode,
        no_names=True,
        show_progress_bar=False,
        limits_override=None,
    )


def _flatten_cfg(d: Dict[str, Any]) -> str:
    return json.dumps(d, sort_keys=True, separators=(",", ":"))


def main(argv: Optional[List[str]] = None) -> None:
    parser = argparse.ArgumentParser(description="Sweep sweep_submission configs on round 1.")
    parser.add_argument(
        "--active",
        choices=("ACO", "IPR", "BOTH"),
        required=True,
        help="ACO / IPR isolation or BOTH combined.",
    )
    parser.add_argument(
        "--aco-range",
        metavar="LO:HI",
        help="Inclusive ACO_CONFIG_ID range (e.g. 0:7). Use single index as 3:3.",
    )
    parser.add_argument(
        "--ipr-range",
        metavar="LO:HI",
        help="Inclusive IPR_CONFIG_ID range (e.g. 0:28).",
    )
    parser.add_argument(
        "--aco-id",
        type=int,
        metavar="N",
        help="Single ACO_CONFIG_ID (ignored if --aco-range is set).",
    )
    parser.add_argument(
        "--ipr-id",
        type=int,
        metavar="N",
        help="Single IPR_CONFIG_ID (ignored if --ipr-range is set).",
    )
    parser.add_argument(
        "--data",
        type=Path,
        default=None,
        help="Optional data root (same as prosperity4btest --data). Default: packaged resources.",
    )
    parser.add_argument(
        "-o",
        "--output",
        type=Path,
        required=True,
        help="Output CSV path.",
    )
    parser.add_argument(
        "--days",
        type=int,
        nargs="*",
        default=None,
        metavar="DAY",
        help="Round 1 days to include (e.g. -2 -1 0). Default: all three.",
    )
    parser.add_argument(
        "--match-trades",
        choices=("all", "worse", "none"),
        default="all",
        help="Market trade matching mode (same as prosperity4btest --match-trades).",
    )
    args = parser.parse_args(argv)

    ss, _algo_path = _ensure_sweep_module()
    n_aco = len(ss.ACO_CONFIGS)
    n_ipr = len(ss.IPR_CONFIGS)

    if args.aco_range:
        aco_ids = _parse_range(args.aco_range, n_aco, "ACO")
    elif args.aco_id is not None:
        if not (0 <= args.aco_id < n_aco):
            raise SystemExit(f"--aco-id must be in [0, {n_aco - 1}]")
        aco_ids = [args.aco_id]
    else:
        aco_ids = [3]

    if args.ipr_range:
        ipr_ids = _parse_range(args.ipr_range, n_ipr, "IPR")
    elif args.ipr_id is not None:
        if not (0 <= args.ipr_id < n_ipr):
            raise SystemExit(f"--ipr-id must be in [0, {n_ipr - 1}]")
        ipr_ids = [args.ipr_id]
    else:
        ipr_ids = [3]

    if args.data is not None:
        from prosperity4bt.file_reader import FileSystemReader

        file_reader = FileSystemReader(args.data)
    else:
        from prosperity4bt.file_reader import PackageResourcesReader

        file_reader = PackageResourcesReader()

    days_run: Tuple[int, ...]
    if args.days is not None and len(args.days) > 0:
        days_run = tuple(args.days)
        for d in days_run:
            if d not in ROUND_1_DAYS:
                raise SystemExit(f"--days values must be subset of {ROUND_1_DAYS}, got {d}")
    else:
        days_run = ROUND_1_DAYS

    match_mode = _parse_match_trades(args.match_trades)

    out_path: Path = args.output
    out_path.parent.mkdir(parents=True, exist_ok=True)

    fieldnames = [
        "run_id",
        "active",
        "aco_config_id",
        "ipr_config_id",
        "round",
        "day",
        "product",
        "pnl",
        "aco_cfg_merged_json",
        "ipr_cfg_merged_json",
    ]

    run_id = 0
    with out_path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()

        for aco_i in aco_ids:
            for ipr_i in ipr_ids:
                os.environ["SWEEP_ACTIVE"] = args.active
                os.environ["SWEEP_ACO_CONFIG_ID"] = str(aco_i)
                os.environ["SWEEP_IPR_CONFIG_ID"] = str(ipr_i)

                importlib.reload(ss)

                aco_json = _flatten_cfg({**ss.ACO_BASELINE, **ss.ACO_CONFIGS[ss.ACO_CONFIG_ID]})
                ipr_json = _flatten_cfg({**ss.IPR_BASELINE, **ss.IPR_CONFIGS[ss.IPR_CONFIG_ID]})

                for day in days_run:
                    result = _run_day(ss, file_reader, 1, day, match_mode)
                    pnl_map = _pnl_last_timestamp_by_product(result)
                    for product, pnl in sorted(pnl_map.items()):
                        run_id += 1
                        w.writerow(
                            {
                                "run_id": run_id,
                                "active": args.active,
                                "aco_config_id": ss.ACO_CONFIG_ID,
                                "ipr_config_id": ss.IPR_CONFIG_ID,
                                "round": 1,
                                "day": day,
                                "product": product,
                                "pnl": pnl,
                                "aco_cfg_merged_json": aco_json,
                                "ipr_cfg_merged_json": ipr_json,
                            }
                        )

    print(f"Wrote {run_id} rows to {out_path}")


if __name__ == "__main__":
    main()
