"""
Compare the ACO `make_offset` ranking across the three backtester fill models.

Context
-------
prosperity4bt matches each submitted order twice:
  A) Aggressive against the current OrderDepth (crosses resting sells).
  B) Passive against historical market_trades at the current timestamp:
       mode=none   -> skip B entirely (passive orders never fill)
       mode=worse  -> fill only when trade_price < order_price (strictly inside)
       mode=all    -> fill when trade_price <= order_price (at-or-inside; you
                      SHARE prints at your own level with the pre-existing
                      resting bot). Default.

Live competition flow (per IMC docs):
  1) Bots post  2) You run()  3) Your aggressive fills
  4) Bots trade against your remaining passive orders   5) Next iter

Step 4 is approximated by Phase B. Queue priority in live means a pre-existing
resting bot at price p likely absorbs flow at p before you do. That favors
`worse` over `all` as the "realistic" setting for passive fills — making the
`all`-mode join-vs-improve comparison optimistic for join.

This script reruns ACO with make_offset in {-1, 0, 1, 2, 3, 4} under all three
modes and prints a side-by-side table so the ranking shift is visible.

Usage
-----
    python3 scripts/compare_match_modes.py
"""

from __future__ import annotations

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

DAYS = [-2, -1, 0]
MODES = ["all", "worse", "none"]
OFFSETS = [-1, 0, 1, 2, 3, 4]

PNL_RE = re.compile(r"^(?P<sym>[A-Z_]+):\s*([-+\d,]+)\s*$")


def _parse_pnl(text: str) -> dict[str, float]:
    out = {}
    for line in text.splitlines():
        m = PNL_RE.match(line.strip())
        if m:
            out[m.group("sym")] = float(m.group(2).replace(",", ""))
    return out


def _run(make_offset: int, day: int, mode: str) -> dict:
    cfg = {
        "make_offset": make_offset,
        "min_take_edge": 1,
        "make_portion": 0.8,
        "soft_cap": 60,
        "skew_strength": 0,
        "ema_alpha": 0.25,
    }
    env = os.environ.copy()
    env["EXPL_ACTIVE"] = "ACO"
    env["EXPL_ACO_CFG"] = json.dumps(cfg)
    env["EXPL_IPR_CFG"] = "{}"
    env["EXPL_VERBOSE"] = "0"

    day_spec = f"1-{day}" if day >= 0 else f"1--{-day}"
    cmd = [
        "prosperity4btest", str(TRADER), day_spec,
        "--no-out", "--no-progress",
        "--match-trades", mode,
    ]
    res = subprocess.run(cmd, cwd=ROOT, env=env, capture_output=True, text=True, timeout=180)
    pnls = _parse_pnl(res.stdout)
    return {
        "make_offset": make_offset,
        "day": day,
        "mode": mode,
        "aco_pnl": pnls.get("ASH_COATED_OSMIUM", 0.0),
        "ok": res.returncode == 0,
    }


def main() -> None:
    tasks = [(mo, d, m) for mo in OFFSETS for d in DAYS for m in MODES]
    print(f"Running {len(tasks)} backtests (6 offsets × 3 days × 3 match modes)")
    t0 = time.time()
    rows = []
    with ThreadPoolExecutor(max_workers=6) as ex:
        futures = {ex.submit(_run, *args): args for args in tasks}
        for i, fut in enumerate(as_completed(futures), 1):
            rows.append(fut.result())
            if i % 10 == 0 or i == len(tasks):
                print(f"  [{i}/{len(tasks)}] elapsed={time.time()-t0:5.1f}s")

    df = pd.DataFrame(rows)
    out_path = ROOT / "results" / "explore_aco" / "match_mode_comparison.csv"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(out_path, index=False)
    print(f"\nSaved: {out_path}")

    print("\n" + "=" * 78)
    print("ACO PnL per day × make_offset × match mode (isolated run)")
    print("=" * 78)
    for mode in MODES:
        sub = df[df["mode"] == mode]
        pv = sub.pivot_table(index="make_offset", columns="day",
                             values="aco_pnl", aggfunc="sum").round(0)
        pv["total_3d"] = pv.sum(axis=1)
        pv["mean"] = pv[DAYS].mean(axis=1).round(0)
        print(f"\n  --match-trades {mode}:")
        print(pv.to_string())

    # Rank comparison
    print("\n" + "=" * 78)
    print("Ranking of make_offset under each mode (1 = best)")
    print("=" * 78)
    means = (df.groupby(["mode", "make_offset"])["aco_pnl"]
               .mean().unstack("make_offset").round(0))
    ranks = means.rank(axis=1, ascending=False).astype(int)
    print("\nMean PnL/day (one line per mode):")
    print(means.to_string())
    print("\nRank (1 = best, 6 = worst):")
    print(ranks.to_string())


if __name__ == "__main__":
    main()
