"""
Shared helpers for the primo exploration sweep scripts.
Keeps each test script short and uniform.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TRADER = ROOT / "strageties" / "primo_explorer.py"

PNL_RE = re.compile(r"^(?P<sym>[A-Z_]+):\s*(?P<val>[-+\d,]+)\s*$")


def parse_pnl(stdout_text: str) -> dict:
    """Pull '<SYMBOL>: <number>' lines from prosperity4btest stdout."""
    out = {}
    for line in stdout_text.splitlines():
        m = PNL_RE.match(line.strip())
        if m:
            out[m.group("sym")] = float(m.group("val").replace(",", ""))
    return out


def day_to_arg(day: int) -> str:
    if day >= 0:
        return f"1-{day}"
    return f"1--{-day}"


def run_backtest(
    day: int,
    aco_cfg: dict | None = None,
    ipr_a_cfg: dict | None = None,
    ipr_b_cfg: dict | None = None,
    global_cfg: dict | None = None,
    match_trades: str = "all",
    trader_file: Path | None = None,
    extra_env: dict | None = None,
    timeout: int = 180,
) -> dict:
    """
    Run primo_explorer on a single day with the given JSON configs,
    return a dict with per-product PnL and the subprocess return code.
    """
    env = os.environ.copy()
    if aco_cfg is not None:
        env["EXPL_ACO_CFG"] = json.dumps(aco_cfg)
    if ipr_a_cfg is not None:
        env["EXPL_IPR_A_CFG"] = json.dumps(ipr_a_cfg)
    if ipr_b_cfg is not None:
        env["EXPL_IPR_B_CFG"] = json.dumps(ipr_b_cfg)
    if global_cfg is not None:
        env["EXPL_GLOBAL"] = json.dumps(global_cfg)
    if extra_env:
        env.update(extra_env)

    trader = str(trader_file or TRADER)
    cmd = [
        "prosperity4btest", trader, day_to_arg(day),
        "--no-out", "--no-progress",
        "--match-trades", match_trades,
    ]
    result = subprocess.run(
        cmd,
        cwd=str(ROOT),
        env=env,
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    pnls = parse_pnl(result.stdout)
    return {
        "day":      day,
        "aco_pnl":  pnls.get("ASH_COATED_OSMIUM", 0.0),
        "ipr_pnl":  pnls.get("INTARIAN_PEPPER_ROOT", 0.0),
        "total":    pnls.get("Total", 0.0) if "Total" in pnls else (
                        pnls.get("ASH_COATED_OSMIUM", 0.0)
                        + pnls.get("INTARIAN_PEPPER_ROOT", 0.0)
                    ),
        "ok":       result.returncode == 0,
        "stderr":   result.stderr if result.returncode != 0 else "",
    }


_VALID_BT_KWARGS = {
    "day", "aco_cfg", "ipr_a_cfg", "ipr_b_cfg", "global_cfg",
    "match_trades", "trader_file", "extra_env", "timeout",
}


def run_many(tasks: list, workers: int = 8, progress_every: int = 20):
    """
    tasks = list of dicts. Recognized keys are passed to run_backtest;
    all OTHER keys are kept as label fields and merged into the result row.
    """
    results = []
    with ThreadPoolExecutor(max_workers=workers) as ex:
        # Split each task into (backtest_kwargs, label_fields)
        submitted = {}
        for t in tasks:
            bt_kwargs = {k: v for k, v in t.items() if k in _VALID_BT_KWARGS}
            labels = {k: v for k, v in t.items() if k not in _VALID_BT_KWARGS}
            fut = ex.submit(run_backtest, **bt_kwargs)
            submitted[fut] = labels
        for i, fut in enumerate(as_completed(submitted), 1):
            res = fut.result()
            merged = dict(submitted[fut])
            merged.update({k: v for k, v in res.items() if k != "day"})
            merged["day"] = res["day"]
            results.append(merged)
            if i % progress_every == 0 or i == len(tasks):
                print(f"  [{i}/{len(tasks)}] done")
    return results
