"""
Explore ACO maker placement and supporting parameters.
======================================================

Focus question
--------------
The exhaustive sweep said `join` > `improve_1`. We want to see the full shape:
back off a tick, join, improve_1, improve_2, improve_3, improve_4. Combined
with the knobs that mattered in the earlier sweep (min_take_edge, make_portion,
soft_cap, skew_strength).

Output
------
* results/explore_aco/aco_explore.csv — one row per (config, day)
* console summary with marginal tables and top / bottom configs

Invoke
------
    python3 scripts/explore_aco_maker.py [--quick]
"""

from __future__ import annotations

import argparse
import csv
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
OUT_DIR = ROOT / "results" / "explore_aco"
OUT_DIR.mkdir(parents=True, exist_ok=True)

DAYS = [-2, -1, 0]
PNL_RE = re.compile(r"^(?P<sym>[A-Z_]+):\s*([-+\d,]+)\s*$")
TOTAL_RE = re.compile(r"^Total profit:\s*([-+\d,]+)\s*$")


def _parse_pnl(output: str) -> dict[str, float]:
    out: dict[str, float] = {}
    for line in output.splitlines():
        line = line.strip()
        m = PNL_RE.match(line)
        if m:
            sym = m.group("sym")
            val = float(m.group(2).replace(",", ""))
            out[sym] = val
            continue
        m = TOTAL_RE.match(line)
        if m:
            out["__total__"] = float(m.group(1).replace(",", ""))
    return out


def _run_one(cfg: dict, day: int, match_trades: str = "all") -> dict:
    env = os.environ.copy()
    env["EXPL_ACTIVE"] = "ACO"  # isolate ACO
    env["EXPL_ACO_CFG"] = json.dumps(cfg)
    env["EXPL_IPR_CFG"] = "{}"
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
        "aco_pnl": pnls.get("ASH_COATED_OSMIUM", 0.0),
        "total_pnl": pnls.get("__total__", 0.0),
    }


def _summarize_cfg(cfg: dict) -> str:
    # Stable key=value summary for readable IDs
    parts = []
    for k in ["make_offset", "maker_mode", "min_take_edge", "make_portion",
              "soft_cap", "skew_strength", "ema_alpha"]:
        if k in cfg:
            parts.append(f"{k}={cfg[k]}")
    return ",".join(parts)


def build_configs(quick: bool) -> list[dict]:
    if quick:
        make_offsets = [-1, 0, 1, 2]
        edges = [1]
        portions = [0.8]
        soft_caps = [60]
        skews = [0]
        alphas = [0.25]
    else:
        # Main sweep — focused
        make_offsets = [-2, -1, 0, 1, 2, 3, 4]
        edges = [0, 1, 2, 3]
        portions = [0.6, 0.8, 1.0]
        soft_caps = [40, 60, 75]
        skews = [0, 1]
        alphas = [0.1, 0.25, 0.5]

    configs: list[dict] = []

    # 1. Core slice: vary make_offset with sensible defaults.
    for mo in make_offsets:
        for edge in edges:
            configs.append({
                "make_offset": mo,
                "min_take_edge": edge,
                "maker_mode": "join",
                "make_portion": 0.8,
                "soft_cap": 60,
                "skew_strength": 0,
                "ema_alpha": 0.25,
            })

    # 2. make_offset x make_portion (all edges=1)
    for mo, mp in itertools.product(make_offsets, portions):
        if mp == 0.8:
            continue
        configs.append({
            "make_offset": mo,
            "min_take_edge": 1,
            "maker_mode": "join",
            "make_portion": mp,
            "soft_cap": 60,
            "skew_strength": 0,
            "ema_alpha": 0.25,
        })

    # 3. soft_cap sweep at best-guess offsets
    for mo, sc in itertools.product([0, 1, 2], soft_caps):
        if sc == 60:
            continue
        configs.append({
            "make_offset": mo,
            "min_take_edge": 1,
            "maker_mode": "join",
            "make_portion": 0.8,
            "soft_cap": sc,
            "skew_strength": 0,
            "ema_alpha": 0.25,
        })

    # 4. skew_strength sweep (confirmed harmful at 2, retest at 1 vs 0)
    for mo, sk in itertools.product([0, 1, 2], skews):
        if sk == 0:
            continue
        configs.append({
            "make_offset": mo,
            "min_take_edge": 1,
            "maker_mode": "join",
            "make_portion": 0.8,
            "soft_cap": 60,
            "skew_strength": sk,
            "ema_alpha": 0.25,
        })

    # 5. ema_alpha sweep
    for mo, a in itertools.product([0, 1], alphas):
        if a == 0.25:
            continue
        configs.append({
            "make_offset": mo,
            "min_take_edge": 1,
            "maker_mode": "join",
            "make_portion": 0.8,
            "soft_cap": 60,
            "skew_strength": 0,
            "ema_alpha": a,
        })

    # Deduplicate
    seen = set()
    unique = []
    for c in configs:
        key = tuple(sorted(c.items()))
        if key in seen:
            continue
        seen.add(key)
        unique.append(c)
    return unique


def run_sweep(configs: list[dict], match_trades: str, max_workers: int) -> pd.DataFrame:
    rows = []
    total = len(configs) * len(DAYS)
    t0 = time.time()

    with ThreadPoolExecutor(max_workers=max_workers) as ex:
        futures = {}
        for cfg_id, cfg in enumerate(configs):
            for day in DAYS:
                fut = ex.submit(_run_one, cfg, day, match_trades)
                futures[fut] = (cfg_id, cfg, day)

        done = 0
        for fut in as_completed(futures):
            cfg_id, cfg, day = futures[fut]
            out = fut.result()
            done += 1
            row = {
                "cfg_id": cfg_id,
                "day": day,
                "ok": out["ok"],
                "aco_pnl": out.get("aco_pnl"),
                "total_pnl": out.get("total_pnl"),
                "cfg_json": json.dumps(cfg, sort_keys=True),
                **cfg,
            }
            rows.append(row)
            if done % 20 == 0 or done == total:
                elapsed = time.time() - t0
                eta = elapsed * (total - done) / max(done, 1)
                print(f"  [{done}/{total}] elapsed={elapsed:5.1f}s  eta={eta:5.1f}s")

    return pd.DataFrame(rows)


def analyze(df: pd.DataFrame) -> None:
    ok = df[df["ok"]].copy()
    if len(ok) == 0:
        print("No successful runs.")
        return

    by_cfg = ok.groupby("cfg_id").agg(
        make_offset=("make_offset", "first"),
        min_take_edge=("min_take_edge", "first"),
        make_portion=("make_portion", "first"),
        soft_cap=("soft_cap", "first"),
        skew_strength=("skew_strength", "first"),
        ema_alpha=("ema_alpha", "first"),
        total_3d=("aco_pnl", "sum"),
        mean=("aco_pnl", "mean"),
        worst=("aco_pnl", "min"),
        std=("aco_pnl", "std"),
    ).sort_values("total_3d", ascending=False)

    print("\n" + "=" * 90)
    print("TOP 15 by 3-day total (ACO isolated)")
    print("=" * 90)
    print(by_cfg.head(15).round(1).to_string())
    print("\nBOTTOM 5:")
    print(by_cfg.tail(5).round(1).to_string())

    # Marginal: make_offset
    print("\n" + "=" * 90)
    print("MARGINAL — mean ACO PnL/day by make_offset")
    print("(positive = improve inside best quote, 0 = join, negative = back off)")
    print("=" * 90)
    m = ok.groupby("make_offset")["aco_pnl"].agg(["mean", "std", "count"]).round(1)
    print(m.to_string())

    # make_offset × min_take_edge
    print("\n" + "=" * 90)
    print("MEAN ACO PnL/day BY make_offset × min_take_edge (ema_alpha=0.25, portion=0.8, skew=0, soft_cap=60)")
    print("=" * 90)
    core = ok[(ok["make_portion"] == 0.8) & (ok["soft_cap"] == 60)
              & (ok["skew_strength"] == 0) & (ok["ema_alpha"] == 0.25)]
    pivot = core.pivot_table(index="make_offset", columns="min_take_edge",
                             values="aco_pnl", aggfunc="mean").round(0)
    print(pivot.to_string())

    # make_offset × make_portion
    print("\n" + "=" * 90)
    print("MEAN ACO PnL/day BY make_offset × make_portion (edge=1)")
    print("=" * 90)
    slice2 = ok[(ok["min_take_edge"] == 1) & (ok["soft_cap"] == 60)
                & (ok["skew_strength"] == 0) & (ok["ema_alpha"] == 0.25)]
    pivot2 = slice2.pivot_table(index="make_offset", columns="make_portion",
                                values="aco_pnl", aggfunc="mean").round(0)
    print(pivot2.to_string())

    # soft_cap x make_offset
    print("\n" + "=" * 90)
    print("MEAN ACO PnL/day BY make_offset × soft_cap (edge=1, portion=0.8, skew=0)")
    print("=" * 90)
    slice3 = ok[(ok["min_take_edge"] == 1) & (ok["make_portion"] == 0.8)
                & (ok["skew_strength"] == 0) & (ok["ema_alpha"] == 0.25)]
    pivot3 = slice3.pivot_table(index="make_offset", columns="soft_cap",
                                values="aco_pnl", aggfunc="mean").round(0)
    print(pivot3.to_string())

    print("\n" + "=" * 90)
    print("MARGINAL — mean ACO PnL/day by skew_strength")
    print("=" * 90)
    print(ok.groupby("skew_strength")["aco_pnl"].agg(["mean", "std", "count"]).round(1).to_string())

    print("\n" + "=" * 90)
    print("MARGINAL — mean ACO PnL/day by ema_alpha")
    print("=" * 90)
    print(ok.groupby("ema_alpha")["aco_pnl"].agg(["mean", "std", "count"]).round(1).to_string())


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--quick", action="store_true", help="smaller sweep for a smoke test")
    ap.add_argument("--match-trades", default="all", choices=["all", "worse", "none"])
    ap.add_argument("--workers", type=int, default=6)
    ap.add_argument("--no-run", action="store_true",
                    help="skip backtests, analyze existing CSV only")
    args = ap.parse_args()

    csv_path = OUT_DIR / ("aco_explore_quick.csv" if args.quick else "aco_explore.csv")

    if not args.no_run:
        configs = build_configs(args.quick)
        print(f"Running {len(configs)} configs × {len(DAYS)} days "
              f"= {len(configs) * len(DAYS)} backtests with "
              f"--match-trades {args.match_trades} (workers={args.workers})")
        df = run_sweep(configs, args.match_trades, args.workers)
        df.to_csv(csv_path, index=False)
        print(f"\nSaved: {csv_path}")
    else:
        df = pd.read_csv(csv_path)
        print(f"Loaded: {csv_path} ({len(df)} rows)")

    analyze(df)


if __name__ == "__main__":
    main()
