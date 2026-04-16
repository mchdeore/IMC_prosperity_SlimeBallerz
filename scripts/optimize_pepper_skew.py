"""
Optimize pepper root (IPR) around a *calibrated* slope.
========================================================

The exhaustive sweep's slope=0.003 "winner" is a model-miscalibration artifact:
the per-day linear best-fit slope is ~0.001 (R^2 ≈ 0.22-0.32) and a wrong slope
only wins because it forces max long position via the take-positive phase.

Here we freeze `slope=0.001` (the honest estimate) and look for PnL from the
*right* levers instead:

   * explicit long bias via quote_bias_ticks (price-skew)
   * size asymmetry  via bid_frac / ask_frac
   * position_target (skew inventory pressure around a non-zero center)
   * long_take_edge  (be more aggressive on the ask side)
   * make_offset     (join vs improve, like ACO)
   * soft_cap / skew_strength / make_portion

Robustness: we score configs by BOTH 3-day total PnL and worst-day PnL, and
flag configs whose win depends on one good day.

Output
------
* results/optimize_pepper/ipr_opt.csv
* console tables: marginals per parameter, top-K by total, top-K by worst-day,
  and a Pareto slice.

Invoke
------
    python3 scripts/optimize_pepper_skew.py [--quick] [--full]
"""

from __future__ import annotations

import argparse
import itertools
import json
import os
import re
import subprocess
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
TRADER = ROOT / "strageties" / "exploration_trader.py"
OUT_DIR = ROOT / "results" / "optimize_pepper"
OUT_DIR.mkdir(parents=True, exist_ok=True)

DAYS = [-2, -1, 0]
PNL_RE = re.compile(r"^(?P<sym>[A-Z_]+):\s*([-+\d,]+)\s*$")
TOTAL_RE = re.compile(r"^Total profit:\s*([-+\d,]+)\s*$")

SLOPE_FIXED = 0.001  # calibrated best-fit; see scripts/validate_deep_dive.py


def _parse_pnl(output: str) -> dict[str, float]:
    out: dict[str, float] = {}
    for line in output.splitlines():
        line = line.strip()
        m = PNL_RE.match(line)
        if m:
            out[m.group("sym")] = float(m.group(2).replace(",", ""))
            continue
        m = TOTAL_RE.match(line)
        if m:
            out["__total__"] = float(m.group(1).replace(",", ""))
    return out


def _run_one(cfg: dict, day: int, match_trades: str = "all") -> dict:
    cfg = {**cfg, "slope": SLOPE_FIXED}
    env = os.environ.copy()
    env["EXPL_ACTIVE"] = "IPR"  # isolate IPR
    env["EXPL_ACO_CFG"] = "{}"
    env["EXPL_IPR_CFG"] = json.dumps(cfg)
    env["EXPL_VERBOSE"] = "0"
    env["PYTHONUNBUFFERED"] = "1"

    day_spec = f"1-{day}" if day >= 0 else f"1--{-day}"
    cmd = [
        "prosperity4btest", str(TRADER), day_spec,
        "--no-out", "--no-progress",
        "--match-trades", match_trades,
    ]
    res = subprocess.run(cmd, cwd=ROOT, env=env, capture_output=True, text=True, timeout=180)
    if res.returncode != 0:
        return {"day": day, "ok": False, "err": (res.stderr or res.stdout)[:400]}
    pnls = _parse_pnl(res.stdout)
    return {
        "day": day,
        "ok": True,
        "ipr_pnl": pnls.get("INTARIAN_PEPPER_ROOT", 0.0),
        "total_pnl": pnls.get("__total__", 0.0),
    }


# ------------------------------------------------------------------
# Config generation
# ------------------------------------------------------------------

BASE = {
    "maker_mode": "improve_1",   # legacy; overridden when make_offset is set
    "make_offset": 0,            # default: join (we will sweep)
    "min_take_edge": 1,
    "make_portion": 0.9,
    "soft_cap": 75,
    "skew_strength": 0,
    "bid_frac": 0.7,
    "ask_frac": 0.3,
    "pressure_mode": "long_bias",
    "quote_bias_ticks": 0,
    "size_haircut": 1.0,
    "spread_threshold": 3,
    "position_target": 0,
    "long_take_edge": None,
}


def _cfg(overrides: dict) -> dict:
    c = {**BASE, **overrides}
    # Keep bid_frac + ask_frac = 1.0 if only bid_frac is specified
    if "bid_frac" in overrides and "ask_frac" not in overrides:
        c["ask_frac"] = round(1.0 - overrides["bid_frac"], 3)
    return c


def build_configs(quick: bool, full: bool) -> list[dict]:
    configs: list[dict] = []

    # 1) Baseline anchors — confirm slope=0.001 is sane
    configs.append(_cfg({}))

    # 2) make_offset study (join vs improve vs back-off)
    for mo in [-1, 0, 1, 2, 3]:
        configs.append(_cfg({"make_offset": mo}))

    # 3) quote_bias_ticks study (price-lean long)
    for qb in [-1, 0, 1, 2, 3]:
        configs.append(_cfg({"quote_bias_ticks": qb}))

    # 4) bid_frac (size-lean long)
    for bf in [0.5, 0.6, 0.7, 0.8, 0.9]:
        configs.append(_cfg({"bid_frac": bf}))

    # 5) skew_strength study (inventory-based price-skew)
    for ss in [0, 1, 2]:
        configs.append(_cfg({"skew_strength": ss}))

    # 6) soft_cap study
    for sc in [45, 55, 65, 75]:
        configs.append(_cfg({"soft_cap": sc}))

    # 7) min_take_edge study (low edge monetizes drift via taker path)
    for e in [0, 1, 2]:
        configs.append(_cfg({"min_take_edge": e}))

    # 8) NEW: long_take_edge — override only on the ask side (more aggressive long)
    for lte in [0, -1, -2, -4]:
        configs.append(_cfg({"long_take_edge": lte}))

    # 9) NEW: position_target — skew inventory center
    for pt in [20, 40, 60]:
        configs.append(_cfg({"position_target": pt}))

    # 10) make_portion
    for mp in [0.7, 0.9, 1.0]:
        configs.append(_cfg({"make_portion": mp}))

    if quick:
        seen = set(); uniq = []
        for c in configs:
            k = tuple(sorted(c.items()))
            if k in seen:
                continue
            seen.add(k); uniq.append(c)
        return uniq

    # 11) JOINT: long-bias knobs — size × price skew combined
    for qb, bf in itertools.product([0, 1, 2], [0.6, 0.7, 0.8]):
        configs.append(_cfg({"quote_bias_ticks": qb, "bid_frac": bf}))

    # 12) JOINT: long_take_edge × quote_bias (combine aggressive take with quote lean)
    for lte, qb in itertools.product([-2, -1, 0], [0, 1, 2]):
        configs.append(_cfg({"long_take_edge": lte, "quote_bias_ticks": qb}))

    # 13) JOINT: position_target × skew_strength (skew back toward a long target)
    for pt, ss in itertools.product([0, 20, 40], [0, 1]):
        configs.append(_cfg({"position_target": pt, "skew_strength": ss}))

    # 14) make_offset × quote_bias
    for mo, qb in itertools.product([0, 1], [0, 1, 2]):
        configs.append(_cfg({"make_offset": mo, "quote_bias_ticks": qb}))

    if not full:
        seen = set(); uniq = []
        for c in configs:
            k = tuple(sorted(c.items()))
            if k in seen:
                continue
            seen.add(k); uniq.append(c)
        return uniq

    # 15) Broader joint grid (only with --full)
    for qb, bf, lte in itertools.product([0, 1, 2], [0.6, 0.7, 0.8], [None, -1, 0]):
        configs.append(_cfg({
            "quote_bias_ticks": qb, "bid_frac": bf, "long_take_edge": lte,
        }))
    for qb, mp, soft_cap in itertools.product([1, 2], [0.9, 1.0], [65, 75]):
        configs.append(_cfg({
            "quote_bias_ticks": qb, "make_portion": mp, "soft_cap": soft_cap,
        }))

    seen = set(); uniq = []
    for c in configs:
        k = tuple(sorted(c.items()))
        if k in seen:
            continue
        seen.add(k); uniq.append(c)
    return uniq


# ------------------------------------------------------------------
# Runner
# ------------------------------------------------------------------

def run_sweep(configs: list[dict], match_trades: str, max_workers: int) -> pd.DataFrame:
    rows = []
    total = len(configs) * len(DAYS)
    t0 = time.time()

    with ThreadPoolExecutor(max_workers=max_workers) as ex:
        futures = {}
        for cfg_id, cfg in enumerate(configs):
            for day in DAYS:
                futures[ex.submit(_run_one, cfg, day, match_trades)] = (cfg_id, cfg, day)

        done = 0
        for fut in as_completed(futures):
            cfg_id, cfg, day = futures[fut]
            out = fut.result()
            done += 1
            row = {
                "cfg_id": cfg_id,
                "day": day,
                "ok": out["ok"],
                "ipr_pnl": out.get("ipr_pnl"),
                "total_pnl": out.get("total_pnl"),
                "cfg_json": json.dumps(cfg, sort_keys=True),
                **cfg,
            }
            rows.append(row)
            if done % 25 == 0 or done == total:
                elapsed = time.time() - t0
                eta = elapsed * (total - done) / max(done, 1)
                print(f"  [{done}/{total}] elapsed={elapsed:5.1f}s  eta={eta:5.1f}s")

    return pd.DataFrame(rows)


# ------------------------------------------------------------------
# Analysis
# ------------------------------------------------------------------

DISPLAY_COLS = [
    "make_offset", "min_take_edge", "quote_bias_ticks", "bid_frac",
    "skew_strength", "soft_cap", "position_target", "long_take_edge",
    "make_portion",
]


def _by_cfg(df: pd.DataFrame) -> pd.DataFrame:
    agg = {
        **{c: (c, "first") for c in DISPLAY_COLS},
        "total_3d": ("ipr_pnl", "sum"),
        "mean": ("ipr_pnl", "mean"),
        "worst": ("ipr_pnl", "min"),
        "std":   ("ipr_pnl", "std"),
    }
    return df.groupby("cfg_id").agg(**agg)


def _show(df: pd.DataFrame, n: int = 15) -> None:
    print(df.head(n).round(1).to_string())


def analyze(df: pd.DataFrame) -> None:
    ok = df[df["ok"]].copy()
    if len(ok) == 0:
        print("No successful runs.")
        return

    by = _by_cfg(ok).sort_values("total_3d", ascending=False)

    print("\n" + "=" * 110)
    print(f"CALIBRATED IPR — slope={SLOPE_FIXED} fixed | TOP 15 by 3-day total")
    print("=" * 110)
    _show(by, 15)
    print("\nBOTTOM 5:")
    _show(by.tail(5), 5)

    print("\n" + "=" * 110)
    print("TOP 15 by worst-day PnL (robustness)")
    print("=" * 110)
    _show(by.sort_values("worst", ascending=False), 15)

    print("\n" + "=" * 110)
    print("TOP 10 by (worst / mean) consistency ratio — best_worst normalized")
    print("=" * 110)
    by_rank = by.assign(consistency=lambda d: d["worst"] / d["mean"].replace(0, 1))
    print(by_rank.sort_values(["worst", "consistency"], ascending=[False, False])
            .head(10).round(2).to_string())

    # Marginal analyses
    print("\n" + "=" * 110)
    print("MARGINAL — mean IPR PnL/day by each parameter (over all sampled combos)")
    print("=" * 110)
    for key in ["make_offset", "quote_bias_ticks", "bid_frac", "skew_strength",
                "soft_cap", "min_take_edge", "long_take_edge",
                "position_target", "make_portion"]:
        g = ok.copy()
        g[key] = g[key].astype(object).fillna("None")
        m = g.groupby(key)["ipr_pnl"].agg(["mean", "std", "count"]).round(1)
        print(f"\n  {key}:")
        print(m.to_string())

    print("\n" + "=" * 110)
    print("POSITION TARGET × QUOTE BIAS (mean IPR PnL/day)")
    print("=" * 110)
    slice_ = ok[(ok["make_offset"] == 0) & (ok["skew_strength"] == 0)]
    if len(slice_):
        pv = slice_.pivot_table(index="position_target", columns="quote_bias_ticks",
                                 values="ipr_pnl", aggfunc="mean").round(0)
        print(pv.to_string())

    print("\n" + "=" * 110)
    print("QUOTE_BIAS × BID_FRAC (mean IPR PnL/day, at defaults)")
    print("=" * 110)
    pv = ok.pivot_table(index="quote_bias_ticks", columns="bid_frac",
                        values="ipr_pnl", aggfunc="mean").round(0)
    print(pv.to_string())


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--quick", action="store_true")
    ap.add_argument("--full", action="store_true")
    ap.add_argument("--match-trades", default="all", choices=["all", "worse", "none"])
    ap.add_argument("--workers", type=int, default=6)
    ap.add_argument("--no-run", action="store_true")
    args = ap.parse_args()

    suffix = "_quick" if args.quick else ("_full" if args.full else "")
    csv_path = OUT_DIR / f"ipr_opt{suffix}.csv"

    if not args.no_run:
        configs = build_configs(args.quick, args.full)
        print(f"Running {len(configs)} configs × {len(DAYS)} days "
              f"= {len(configs) * len(DAYS)} backtests with "
              f"slope={SLOPE_FIXED} fixed, --match-trades {args.match_trades} "
              f"(workers={args.workers})")
        df = run_sweep(configs, args.match_trades, args.workers)
        df.to_csv(csv_path, index=False)
        print(f"\nSaved: {csv_path}")
    else:
        df = pd.read_csv(csv_path)

    analyze(df)


if __name__ == "__main__":
    main()
