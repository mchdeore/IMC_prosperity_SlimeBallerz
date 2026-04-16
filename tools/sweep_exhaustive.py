#!/usr/bin/env python3
"""
Run large sweep batches for strageties/sweep_submission.py (round 1).

Presets:
  isolated   — full ACO grid (partner IPR fixed at 3) + full IPR grid (partner ACO fixed at 3)
  both-full  — Cartesian product of every ACO index × every IPR index with ACTIVE=BOTH
  all        — isolated + both-full (three CSV outputs)

Combines:
  n_aco = len(ACO_CONFIGS), n_ipr = len(IPR_CONFIGS)
  both-full => n_aco * n_ipr configs × len(days) × 2 products rows

Examples:
  python tools/sweep_exhaustive.py --preset isolated --out-dir results/big_sweep_1
  python tools/sweep_exhaustive.py --preset both-full --out-dir results/big_sweep_1
  python tools/sweep_exhaustive.py --preset both-full --aco-chunk 0:14 --out-dir results/chunk_a
  python tools/sweep_exhaustive.py --preset all --out-dir results/full_opt --dry-run
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SWEEP_ROUND1 = REPO_ROOT / "tools" / "sweep_round1.py"


def _load_counts() -> tuple[int, int]:
    """Load sweep_submission to read config list lengths (uses repo datamodel)."""
    import importlib.util

    if str(REPO_ROOT) not in sys.path:
        sys.path.insert(0, str(REPO_ROOT))
    spec = importlib.util.spec_from_file_location(
        "sweep_submission_exhaustive",
        REPO_ROOT / "strageties" / "sweep_submission.py",
    )
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return len(mod.ACO_CONFIGS), len(mod.IPR_CONFIGS)


def _run_sweep_round1(args: list[str]) -> None:
    cmd = [sys.executable, str(SWEEP_ROUND1)] + args
    print("+", " ".join(cmd), flush=True)
    subprocess.check_call(cmd, cwd=str(REPO_ROOT))


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Exhaustive / large-batch sweeps (delegates to sweep_round1.py).",
    )
    parser.add_argument(
        "--preset",
        choices=("isolated", "both-full", "all"),
        required=True,
        help="isolated: ACO-only full + IPR-only full. both-full: full Cartesian BOTH. all: both.",
    )
    parser.add_argument(
        "--out-dir",
        type=Path,
        required=True,
        help="Directory for CSV outputs and summary.json",
    )
    parser.add_argument(
        "--aco-chunk",
        metavar="LO:HI",
        help="For both-full only: inclusive ACO index range (split large jobs). Default: full range.",
    )
    parser.add_argument(
        "--ipr-chunk",
        metavar="LO:HI",
        help="For both-full only: inclusive IPR index range. Default: full range.",
    )
    parser.add_argument(
        "--ipr-fixed-isolated",
        type=int,
        default=3,
        metavar="N",
        help="IPR_CONFIG_ID while sweeping ACO in isolated preset (default 3).",
    )
    parser.add_argument(
        "--aco-fixed-isolated",
        type=int,
        default=3,
        metavar="N",
        help="ACO_CONFIG_ID while sweeping IPR in isolated preset (default 3).",
    )
    parser.add_argument(
        "--match-trades",
        choices=("all", "worse", "none"),
        default="all",
        help="Passed through to sweep_round1.",
    )
    parser.add_argument(
        "--data",
        type=Path,
        default=None,
        help="Optional data root (passed to sweep_round1).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print planned commands and summary only.",
    )
    args = parser.parse_args()

    n_aco, n_ipr = _load_counts()
    out_dir: Path = args.out_dir
    out_dir.mkdir(parents=True, exist_ok=True)

    def aco_range_full() -> str:
        return f"0:{n_aco - 1}"

    def ipr_range_full() -> str:
        return f"0:{n_ipr - 1}"

    aco_spec = args.aco_chunk.strip() if args.aco_chunk else aco_range_full()
    ipr_spec = args.ipr_chunk.strip() if args.ipr_chunk else ipr_range_full()

    n_aco_chunk = _parse_chunk_len(aco_spec, n_aco, "ACO")
    n_ipr_chunk = _parse_chunk_len(ipr_spec, n_ipr, "IPR")
    both_combos = n_aco_chunk * n_ipr_chunk
    days_default = 3
    rows_both = both_combos * days_default * 2
    backtests_both = both_combos * days_default
    rows_iso = (n_aco + n_ipr) * days_default * 2
    backtests_iso = (n_aco + n_ipr) * days_default

    summary = {
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "preset": args.preset,
        "n_aco_configs": n_aco,
        "n_ipr_configs": n_ipr,
        "both_chunk": {"aco_range": aco_spec, "ipr_range": ipr_spec},
        "both_combinations_in_run": both_combos if args.preset in ("both-full", "all") else 0,
        "estimated_csv_rows_both_only": rows_both if args.preset in ("both-full", "all") else 0,
        "estimated_run_backtest_calls_both_only": backtests_both if args.preset in ("both-full", "all") else 0,
        "estimated_csv_rows_isolated_only": rows_iso if args.preset in ("isolated", "all") else 0,
        "estimated_run_backtest_calls_isolated_only": backtests_iso if args.preset in ("isolated", "all") else 0,
        "notes": "Rows = configs × days × 2 products. BOTH runs one backtest per (config, day).",
    }

    jobs: list[tuple[str, list[str]]] = []

    if args.preset in ("isolated", "all"):
        jobs.append(
            (
                "sweep_aco_isolated.csv",
                [
                    "--active",
                    "ACO",
                    "--aco-range",
                    aco_range_full(),
                    "--ipr-id",
                    str(args.ipr_fixed_isolated),
                    "-o",
                    str(out_dir / "sweep_aco_isolated.csv"),
                    "--match-trades",
                    args.match_trades,
                ],
            )
        )
        jobs.append(
            (
                "sweep_ipr_isolated.csv",
                [
                    "--active",
                    "IPR",
                    "--aco-id",
                    str(args.aco_fixed_isolated),
                    "--ipr-range",
                    ipr_range_full(),
                    "-o",
                    str(out_dir / "sweep_ipr_isolated.csv"),
                    "--match-trades",
                    args.match_trades,
                ],
            )
        )

    if args.preset in ("both-full", "all"):
        both_args = [
            "--active",
            "BOTH",
            "--aco-range",
            aco_spec,
            "--ipr-range",
            ipr_spec,
            "-o",
            str(out_dir / "sweep_both_cartesian.csv"),
            "--match-trades",
            args.match_trades,
        ]
        jobs.append(("sweep_both_cartesian.csv", both_args))

    if args.data is not None:
        for _name, ja in jobs:
            ja.extend(["--data", str(args.data)])

    with (out_dir / "summary.json").open("w", encoding="utf-8") as sf:
        json.dump(summary, sf, indent=2)
        sf.write("\n")

    print(json.dumps(summary, indent=2))
    if args.dry_run:
        for name, ja in jobs:
            print(f"\n# {name}")
            print("+", sys.executable, str(SWEEP_ROUND1), " ".join(ja))
        return

    for name, ja in jobs:
        print(f"\n=== Running {name} ===", flush=True)
        _run_sweep_round1(ja)

    print(f"\nDone. Outputs under {out_dir}", flush=True)


def _parse_chunk_len(spec: str, nmax: int, label: str) -> int:
    spec = spec.strip()
    if ":" not in spec:
        return 1
    lo_s, hi_s = spec.split(":", 1)
    lo, hi = int(lo_s.strip()), int(hi_s.strip())
    if lo > hi:
        lo, hi = hi, lo
    if lo < 0 or hi >= nmax:
        raise SystemExit(f"{label} chunk [{lo}, {hi}] out of [0, {nmax - 1}]")
    return hi - lo + 1


if __name__ == "__main__":
    main()
