"""
Record which market data files correspond to a backtest run (for plotting joins).

Resolves paths for the bundled ``prosperity4bt`` resources and, if present, copies
under the repo ``DATA/`` folder that match the same round/day names.
"""

from __future__ import annotations

import json
import os
from importlib import resources
from pathlib import Path
from typing import Any, Dict, Optional


def _repo_root() -> Path:
    return Path(__file__).resolve().parent.parent


def _env_int(name: str) -> Optional[int]:
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return None
    try:
        return int(raw)
    except ValueError:
        return None


def _package_csv_path(round_num: int, filename: str) -> Optional[str]:
    subpkg = f"prosperity4bt.resources.round{round_num}"
    try:
        traversable = resources.files(subpkg) / filename
        if not traversable.is_file():
            return None
        with resources.as_file(traversable) as p:
            return str(p.resolve())
    except Exception:
        return None


def _filesystem_csv_path(data_root: Path, round_num: int, filename: str) -> Optional[str]:
    p = data_root / f"round{round_num}" / filename
    if p.is_file():
        return str(p.resolve())
    return None


def resolve_inputs_for_run(round_num: int, day_num: int) -> Dict[str, Any]:
    prices_name = f"prices_round_{round_num}_day_{day_num}.csv"
    trades_name = f"trades_round_{round_num}_day_{day_num}.csv"
    obs_name = f"observations_round_{round_num}_day_{day_num}.csv"

    out: Dict[str, Any] = {
        "round": round_num,
        "day": day_num,
        "bundled_package": {
            "prices": _package_csv_path(round_num, prices_name),
            "trades": _package_csv_path(round_num, trades_name),
            "observations": _package_csv_path(round_num, obs_name),
        },
    }

    custom = os.environ.get("PROSPERITY4BT_DATA_ROOT", "").strip()
    if custom:
        root = Path(custom).expanduser().resolve()
        out["custom_data_root"] = str(root)
        out["custom_data_root_files"] = {
            "prices": _filesystem_csv_path(root, round_num, prices_name),
            "trades": _filesystem_csv_path(root, round_num, trades_name),
            "observations": _filesystem_csv_path(root, round_num, obs_name),
        }

    repo = _repo_root()
    flat_data = repo / "DATA"
    candidates: Dict[str, Optional[str]] = {}
    for key, name in (
        ("prices", prices_name),
        ("trades", trades_name),
        ("observations", obs_name),
    ):
        fp = flat_data / name
        candidates[key] = str(fp.resolve()) if fp.is_file() else None
    if any(candidates.values()):
        out["repository_DATA_folder"] = str(flat_data.resolve())
        out["repository_DATA_folder_files"] = candidates

    return out


def write_manifest_next_to_tick_csv(tick_csv_path: Path) -> Optional[Path]:
    """
    Write ``<tick_csv_stem>.source_manifest.json`` next to the tick CSV.
    Uses ``PROSPERITY4BT_ROUND`` / ``PROSPERITY4BT_DAY`` (set by the backtester).
    """
    rnd = _env_int("PROSPERITY4BT_ROUND")
    day = _env_int("PROSPERITY4BT_DAY")
    if rnd is None or day is None:
        return None

    manifest_path = tick_csv_path.with_name(f"{tick_csv_path.stem}.source_manifest.json")
    payload = resolve_inputs_for_run(rnd, day)
    payload["outputs"] = {"tick_csv": str(tick_csv_path.resolve())}
    out_log = os.environ.get("PROSPERITY4BT_OUT_LOG", "").strip()
    if out_log:
        payload["outputs"]["backtest_log"] = str(Path(out_log).expanduser().resolve())

    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return manifest_path
