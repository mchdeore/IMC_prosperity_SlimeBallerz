"""
Compare IPR knobs across backtester fill modes.

Focus: does the 'quote_bias_ticks=+2' story hold up under --match-trades worse?
"""

from __future__ import annotations

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

DAYS = [-2, -1, 0]
MODES = ["all", "worse", "none"]

# Small, targeted grid
OFFSETS = [0, 1, 2]
BIASES = [0, 1, 2, 3]

PNL_RE = re.compile(r"^(?P<sym>[A-Z_]+):\s*([-+\d,]+)\s*$")


def _parse_pnl(text: str) -> dict[str, float]:
    out = {}
    for line in text.splitlines():
        m = PNL_RE.match(line.strip())
        if m:
            out[m.group("sym")] = float(m.group(2).replace(",", ""))
    return out


def _run(cfg: dict, day: int, mode: str) -> dict:
    full = {
        "make_portion": 0.9,
        "soft_cap": 75,
        "bid_frac": 0.7,
        "ask_frac": 0.3,
        "pressure_mode": "long_bias",
        "skew_strength": 0,
        "min_take_edge": 1,
        "slope": 0.001,
        **cfg,
    }
    env = os.environ.copy()
    env["EXPL_ACTIVE"] = "IPR"
    env["EXPL_ACO_CFG"] = "{}"
    env["EXPL_IPR_CFG"] = json.dumps(full)
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
        **{k: cfg.get(k) for k in ["make_offset", "quote_bias_ticks"]},
        "day": day,
        "mode": mode,
        "ipr_pnl": pnls.get("INTARIAN_PEPPER_ROOT", 0.0),
    }


def main() -> None:
    tasks = []
    for mo, qb in itertools.product(OFFSETS, BIASES):
        cfg = {"make_offset": mo, "quote_bias_ticks": qb}
        for d in DAYS:
            for m in MODES:
                tasks.append((cfg, d, m))

    print(f"Running {len(tasks)} backtests")
    t0 = time.time()
    rows = []
    with ThreadPoolExecutor(max_workers=6) as ex:
        futures = {ex.submit(_run, *args): args for args in tasks}
        for i, fut in enumerate(as_completed(futures), 1):
            rows.append(fut.result())
            if i % 30 == 0 or i == len(tasks):
                print(f"  [{i}/{len(tasks)}] elapsed={time.time()-t0:5.1f}s")

    df = pd.DataFrame(rows)
    out = ROOT / "results" / "optimize_pepper" / "match_mode_comparison.csv"
    out.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(out, index=False)

    print("\n" + "=" * 90)
    print("IPR mean PnL/day by make_offset × quote_bias_ticks  (slope=0.001 fixed)")
    print("=" * 90)
    for mode in MODES:
        sub = df[df["mode"] == mode]
        pv = sub.pivot_table(index="make_offset", columns="quote_bias_ticks",
                             values="ipr_pnl", aggfunc="mean").round(0)
        print(f"\n  --match-trades {mode}:")
        print(pv.to_string())


if __name__ == "__main__":
    main()
