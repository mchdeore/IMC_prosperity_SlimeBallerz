#!/usr/bin/env python3
"""Parameter + logic sweep for round4.py."""
import csv, itertools, os, re, subprocess, sys, tempfile, time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
STRATEGY = ROOT / "STRATEGIES" / "round4.py"
ROUND = int(os.environ.get("ROUND", "4"))
LIMITS = " ".join(
    f"--limit {p}:{l}" for p, l in [
        ("HYDROGEL_PACK", 200), ("VELVETFRUIT_EXTRACT", 200),
        ("VEV_4000", 300), ("VEV_4500", 300), ("VEV_5000", 300),
        ("VEV_5100", 300), ("VEV_5200", 300), ("VEV_5300", 300),
        ("VEV_5400", 300), ("VEV_5500", 300), ("VEV_6000", 300), ("VEV_6500", 300),
    ]
)
CMD_TPL = f"python3 -m prosperity4bt {{algo}} {ROUND} --data DATA --no-out --no-progress {LIMITS}"


def out_path(stem: str) -> Path:
    return ROOT / f"sweep_r{ROUND}_{stem}.csv"


def make_variant(replacements: dict) -> Path:
    """Create a temp .py with constant replacements applied."""
    src = STRATEGY.read_text()
    for pattern, value in replacements.items():
        src = re.sub(
            rf"^({re.escape(pattern)}\s*=\s*).*$",
            rf"\g<1>{value}",
            src,
            count=1,
            flags=re.MULTILINE,
        )
    tmp = tempfile.NamedTemporaryFile(suffix=".py", dir=ROOT / "STRATEGIES", delete=False, mode="w")
    tmp.write(src)
    tmp.close()
    return Path(tmp.name)


def run_backtest(algo_path: Path) -> dict:
    """Run backtester, parse per-product per-day PnL."""
    cmd = CMD_TPL.format(algo=algo_path)
    r = subprocess.run(cmd, shell=True, capture_output=True, text=True, cwd=str(ROOT))
    if r.returncode != 0:
        return {"error": r.stderr[:200]}
    lines = r.stdout.strip().split("\n")
    days = {}
    current_day = None
    for line in lines:
        if line.startswith("Backtesting"):
            m = re.search(r"day (\d+)", line)
            if m:
                current_day = int(m.group(1))
                days[current_day] = {}
        elif ":" in line and current_day is not None and not line.startswith("Total") and not line.startswith("Round") and not line.startswith("Profit") and not line.startswith("Risk") and not line.startswith(" "):
            parts = line.strip().split(":")
            if len(parts) == 2:
                prod = parts[0].strip()
                try:
                    pnl = float(parts[1].strip().replace(",", ""))
                    days[current_day][prod] = pnl
                except ValueError:
                    pass
    # compute metrics
    day_totals = [sum(d.values()) for d in days.values()]
    vev_prods = [p for p in list(days.values())[0] if p.startswith("VEV_")] if days else []
    vev_day_totals = [sum(d.get(p, 0) for p in vev_prods) for d in days.values()]
    all_strike_pnls = [d.get(p, 0) for d in days.values() for p in vev_prods]
    return {
        "total": sum(day_totals),
        "min_day": min(day_totals) if day_totals else 0,
        "max_day": max(day_totals) if day_totals else 0,
        "day_std": _std(day_totals),
        "vev_total": sum(vev_day_totals),
        "vev_min_day": min(vev_day_totals) if vev_day_totals else 0,
        "worst_strike": min(all_strike_pnls) if all_strike_pnls else 0,
        "days": days,
    }


def _std(vals):
    if len(vals) < 2:
        return 0
    m = sum(vals) / len(vals)
    return (sum((x - m) ** 2 for x in vals) / len(vals)) ** 0.5


def sweep_params():
    """Grid sweep over key parameters."""
    grid = {
        "HYDROGEL_TAKE_EDGE": [0, 1, 2],
        "VELVET_TAKE_EDGE": [0, 1, 2],
        "VEV_TAKE_EDGE": [0, 1, 2],
        "VEV_STRIKE_CAP": [80, 120],
        "VEV_SMILE_EMA": [0.3, 0.5],
        "VEV_TIGHT_SIZE_FRAC": [0.2, 0.3, 0.5],
        "VEV_WIDE_OFFSET": [1, 2, 3],
        "VEV_DELTA_DIVISOR": [60, 120, 999999],
    }
    # Dependent params
    def deps(combo):
        cap = combo["VEV_STRIKE_CAP"]
        combo["VEV_SOFTCAP"] = max(int(cap * 0.6), 10)
        combo["VEV_HARDCAP"] = f"VEV_STRIKE_CAP"
        combo["VEV_YARDAGE"] = f"VEV_HARDCAP - VEV_SOFTCAP"
        combo["VEV_MAX_QUOTE"] = max(int(cap * 0.2), 5)
        return combo

    keys = list(grid.keys())
    vals = [grid[k] for k in keys]
    combos = [dict(zip(keys, v)) for v in itertools.product(*vals)]
    return combos, deps


def sweep_toggles(base_replacements: dict):
    """Logic toggle tests against a base config."""
    toggles = {
        "no_spot_taking": {"HYDROGEL_TAKE_EDGE": 9999, "VELVET_TAKE_EDGE": 9999},
        "no_vev_taking": {"VEV_TAKE_EDGE": 9999},
        "no_delta_skew": {"VEV_DELTA_DIVISOR": 999999},
        "no_multi_level": {"VEV_TIGHT_SIZE_FRAC": 1.0, "VEV_WIDE_OFFSET": 0},
        "raw_l1_mid_for_S": {"VELVET_EMA_ALPHA": 1.0},
        "velvet_ema_001": {"VELVET_EMA_ALPHA": 0.001},
        "velvet_ema_005": {"VELVET_EMA_ALPHA": 0.005},
    }
    return toggles


def sweep_smiles():
    """Compact grid for current-vs-slow IV smile construction."""
    grid = {
        "VEV_SMILE_FIT_MODE": [1, 3],
        "VEV_INCLUDE_PINNED_IN_FIT": [0, 1],
        "VEV_CURRENT_SMILE_WEIGHT": [0.0, 0.5, 1.0],
        "VEV_SMILE_EMA": [0.35],
        "VEV_WEIGHT_SMILE_BY_SPREAD": [1],
        "VEV_IV_BLEND": [0.35],
    }
    keys = list(grid.keys())
    vals = [grid[k] for k in keys]
    combos = [dict(zip(keys, v)) for v in itertools.product(*vals)]
    return combos


def sweep_hydrogel():
    """Small grid for Hydrogel anchor-vs-MA fair value."""
    grid = {
        "HYDROGEL_ANCHOR": [9988.0, 9990.0, 9992.0],
        "HYDROGEL_W_ANCHOR": [1.0, 0.9],
        "HYDROGEL_W_MA": [0.0, 0.1],
    }
    combos = []
    for combo in itertools.product(*[grid[k] for k in grid]):
        row = dict(zip(grid.keys(), combo))
        if abs(row["HYDROGEL_W_ANCHOR"] + row["HYDROGEL_W_MA"] - 1.0) < 1e-9:
            combos.append(row)
    return combos


def sweep_hydrogel_fair():
    """Isolated Hydrogel fair/take-edge sweep."""
    grid = {
        "ENABLE_HYDROGEL": [1],
        "ENABLE_VELVET": [0],
        "ENABLE_VEV": [0],
        "HYDROGEL_ANCHOR": [float(x) for x in range(9988, 9997)],
        "HYDROGEL_W_ANCHOR": [1.0],
        "HYDROGEL_W_MA": [0.0],
        "HYDROGEL_TAKE_EDGE": [3, 4, 5, 6, 7, 8, 10, 9999],
    }
    keys = list(grid.keys())
    vals = [grid[k] for k in keys]
    return [dict(zip(keys, v)) for v in itertools.product(*vals)]


def sweep_hydrogel_mm():
    """Isolated Hydrogel hotspot sweep around the best fair-value band."""
    grid = {
        "ENABLE_HYDROGEL": [1],
        "ENABLE_VELVET": [0],
        "ENABLE_VEV": [0],
        "HYDROGEL_ANCHOR": [9995.0, 9996.0, 9997.0],
        "HYDROGEL_W_ANCHOR": [1.0],
        "HYDROGEL_W_MA": [0.0],
        "HYDROGEL_TAKE_EDGE": [6, 8, 10, 12],
        "HYDROGEL_TIGHT": [1, 2],
        "HYDROGEL_WIDE": [4, 8],
        "HYDROGEL_MAX_QUOTE": [120, 200],
        "HYDROGEL_SOFTCAP": [160, 190],
        "HYDROGEL_HARDCAP": [200],
    }
    keys = list(grid.keys())
    vals = [grid[k] for k in keys]
    combos = [dict(zip(keys, v)) for v in itertools.product(*vals)]
    for combo in combos:
        combo["HYDROGEL_YARDAGE"] = "HYDROGEL_HARDCAP - HYDROGEL_SOFTCAP"
    return combos


def sweep_hydrogel_confirm():
    """Tight Hydrogel safe-profit refinement."""
    grid = {
        "ENABLE_HYDROGEL": [1],
        "ENABLE_VELVET": [0],
        "ENABLE_VEV": [0],
        "HYDROGEL_ANCHOR": [9996.5, 9997.0, 9997.5, 9998.0],
        "HYDROGEL_W_ANCHOR": [1.0],
        "HYDROGEL_W_MA": [0.0],
        "HYDROGEL_TAKE_EDGE": [5, 6, 7, 8],
        "HYDROGEL_TIGHT": [1],
        "HYDROGEL_WIDE": [4],
        "HYDROGEL_MAX_QUOTE": [120],
        "HYDROGEL_SOFTCAP": [160, 180, 190],
        "HYDROGEL_HARDCAP": [200],
    }
    keys = list(grid.keys())
    combos = [dict(zip(keys, v)) for v in itertools.product(*[grid[k] for k in keys])]
    for combo in combos:
        combo["HYDROGEL_YARDAGE"] = "HYDROGEL_HARDCAP - HYDROGEL_SOFTCAP"
    return combos


def sweep_hydrogel_fallback():
    """Round-4 Hydrogel anchor-vs-live fallback robustness sweep."""
    grid = {
        "ENABLE_HYDROGEL": [1],
        "ENABLE_VELVET": [0],
        "ENABLE_VEV": [0],
        "HYDROGEL_ANCHOR": [9997.5, 9998.0],
        "HYDROGEL_W_ANCHOR": [0.0],
        "HYDROGEL_W_MA": [1.0],
        "HYDROGEL_MA_WINDOW": [1000],
        "HYDROGEL_ANCHOR_WEIGHT": [1.0, 0.95, 0.90, 0.80],
        "HYDROGEL_DRIFT_THRESHOLD": [20.0, 50.0],
        "HYDROGEL_FALLBACK_ANCHOR_WEIGHT": [0.5, 0.8],
        "HYDROGEL_TAKE_EDGE": [5, 8],
        "HYDROGEL_TIGHT": [1],
        "HYDROGEL_WIDE": [4],
        "HYDROGEL_MAX_QUOTE": [120],
        "HYDROGEL_SOFTCAP": [190],
        "HYDROGEL_HARDCAP": [200],
    }
    keys = list(grid.keys())
    combos = [dict(zip(keys, v)) for v in itertools.product(*[grid[k] for k in keys])]
    for combo in combos:
        combo["HYDROGEL_YARDAGE"] = "HYDROGEL_HARDCAP - HYDROGEL_SOFTCAP"
    return combos


def sweep_options_quick():
    """Small grid for current residual-scalping option risk knobs."""
    grid = {
        "VEV_MIN_TRADE_FAIR": [35.0, 50.0, 75.0],
        "VEV_ENTRY_Z": [1.0, 1.35, 1.8],
        "VEV_PASSIVE_SIZE_FRAC": [0.15, 0.25],
        "VEV_MAX_TAKE": [12, 24],
    }
    keys = list(grid.keys())
    vals = [grid[k] for k in keys]
    return [dict(zip(keys, v)) for v in itertools.product(*vals)]


def sweep_velvet():
    """Small grid for Velvetfruit slow-EMA market making."""
    grid = {
        "VELVET_EMA_ALPHA": [0.0, 0.0005],
        "VELVET_TAKE_EDGE": [0, 9999],
        "VELVET_USE_ANCHOR_INIT": [1],
        "VELVET_MAX_QUOTE": [40, 80],
        "VELVET_WIDE": [4, 6],
    }
    keys = list(grid.keys())
    vals = [grid[k] for k in keys]
    return [dict(zip(keys, v)) for v in itertools.product(*vals)]


def sweep_velvet_fair():
    """Isolated Velvet coarse fair/take-edge hotspot sweep."""
    grid = {
        "ENABLE_HYDROGEL": [0],
        "ENABLE_VELVET": [1],
        "ENABLE_VEV": [0],
        "VELVET_ANCHOR": [float(x) for x in range(5240, 5253)],
        "VELVET_EMA_ALPHA": [0.0],
        "VELVET_TAKE_EDGE": [0, 2, 4, 5, 6, 8, 10, 9999],
        "VELVET_USE_ANCHOR_INIT": [1],
    }
    keys = list(grid.keys())
    vals = [grid[k] for k in keys]
    return [dict(zip(keys, v)) for v in itertools.product(*vals)]


def sweep_velvet_mm():
    """Isolated Velvet compact quote/inventory hotspot sweep."""
    grid = {
        "ENABLE_HYDROGEL": [0],
        "ENABLE_VELVET": [1],
        "ENABLE_VEV": [0],
        "VELVET_ANCHOR": [5245.0, 5246.0, 5247.0, 5248.0],
        "VELVET_EMA_ALPHA": [0.0, 0.00001, 0.00005, 0.0001],
        "VELVET_TAKE_EDGE": [6, 8, 10, 9999],
        "VELVET_TIGHT": [1, 2],
        "VELVET_WIDE": [4, 6],
        "VELVET_MAX_QUOTE": [40, 80],
        "VELVET_SOFTCAP": [60, 120, 180],
        "VELVET_HARDCAP": [200],
        "VELVET_USE_ANCHOR_INIT": [1],
    }
    keys = list(grid.keys())
    vals = [grid[k] for k in keys]
    combos = []
    for values in itertools.product(*vals):
        combo = dict(zip(keys, values))
        if combo["VELVET_HARDCAP"] <= combo["VELVET_SOFTCAP"]:
            continue
        combo["VELVET_YARDAGE"] = "VELVET_HARDCAP - VELVET_SOFTCAP"
        combos.append(combo)
    return combos


def sweep_velvet_refine():
    """Tight Velvet anchor/edge/low-EMA refinement."""
    grid = {
        "ENABLE_HYDROGEL": [0],
        "ENABLE_VELVET": [1],
        "ENABLE_VEV": [0],
        "VELVET_ANCHOR": [5247.0, 5248.0, 5249.0],
        "VELVET_EMA_ALPHA": [0.0, 0.000025, 0.0001, 0.0005],
        "VELVET_TAKE_EDGE": [9, 10, 11, 12],
        "VELVET_TIGHT": [1],
        "VELVET_WIDE": [4],
        "VELVET_MAX_QUOTE": [80],
        "VELVET_SOFTCAP": [60],
        "VELVET_HARDCAP": [200],
        "VELVET_USE_ANCHOR_INIT": [1],
    }
    keys = list(grid.keys())
    combos = [dict(zip(keys, v)) for v in itertools.product(*[grid[k] for k in keys])]
    for combo in combos:
        combo["VELVET_YARDAGE"] = "VELVET_HARDCAP - VELVET_SOFTCAP"
    return combos


def sweep_velvet_fallback():
    """Round-4 Velvet anchor-vs-EMA fallback robustness sweep."""
    grid = {
        "ENABLE_HYDROGEL": [0],
        "ENABLE_VELVET": [1],
        "ENABLE_VEV": [0],
        "VELVET_ANCHOR": [5248.0],
        "VELVET_EMA_ALPHA": [0.000025, 0.0001, 0.0005],
        "VELVET_ANCHOR_WEIGHT": [1.0, 0.95, 0.90, 0.80],
        "VELVET_DRIFT_THRESHOLD": [20.0, 50.0],
        "VELVET_FALLBACK_ANCHOR_WEIGHT": [0.5, 0.8],
        "VELVET_TAKE_EDGE": [12],
        "VELVET_TIGHT": [1],
        "VELVET_WIDE": [4],
        "VELVET_MAX_QUOTE": [80],
        "VELVET_SOFTCAP": [60],
        "VELVET_HARDCAP": [200],
        "VELVET_USE_ANCHOR_INIT": [1],
    }
    keys = list(grid.keys())
    combos = [dict(zip(keys, v)) for v in itertools.product(*[grid[k] for k in keys])]
    for combo in combos:
        combo["VELVET_YARDAGE"] = "VELVET_HARDCAP - VELVET_SOFTCAP"
    return combos


def sweep_options_vol_blend():
    """Wide realized-vol vs implied-vol blend sweep for options."""
    grid = {
        "VEV_REALIZED_VOL_WEIGHT": [i / 10.0 for i in range(11)],
        "VEV_REALIZED_VOL_ALPHA": [0.01, 0.02, 0.05],
        "VEV_REALIZED_VOL_MIN_SAMPLES": [25, 50],
    }
    keys = list(grid.keys())
    return [dict(zip(keys, v)) for v in itertools.product(*[grid[k] for k in keys])]


def sweep_mm_combined():
    """Final combined sanity checks for top individual MM configs."""
    variants = [
        {
            "label": "current",
        },
        {
            "label": "conservative_current",
            "HYDROGEL_TAKE_EDGE": 5,
            "VELVET_TAKE_EDGE": 5,
            "HYDROGEL_ANCHOR": 9992.0,
            "VELVET_ANCHOR": 5246.0,
            "VELVET_EMA_ALPHA": 0.0,
        },
        {
            "label": "safe_mm_no_vev",
            "ENABLE_VEV": 0,
            "HYDROGEL_ANCHOR": 9997.0,
            "HYDROGEL_TAKE_EDGE": 6,
            "HYDROGEL_SOFTCAP": 190,
            "HYDROGEL_YARDAGE": "HYDROGEL_HARDCAP - HYDROGEL_SOFTCAP",
            "VELVET_ANCHOR": 5248.0,
            "VELVET_TAKE_EDGE": 10,
            "VELVET_EMA_ALPHA": 0.0,
        },
        {
            "label": "safe_mm_with_vev",
            "ENABLE_VEV": 1,
            "HYDROGEL_ANCHOR": 9997.0,
            "HYDROGEL_TAKE_EDGE": 6,
            "HYDROGEL_SOFTCAP": 190,
            "HYDROGEL_YARDAGE": "HYDROGEL_HARDCAP - HYDROGEL_SOFTCAP",
            "VELVET_ANCHOR": 5248.0,
            "VELVET_TAKE_EDGE": 10,
            "VELVET_EMA_ALPHA": 0.0,
        },
    ]
    return variants


def sweep_combined_candidates():
    """Small combined validation set for robust MM + option candidates."""
    hydro_safe = {
        "HYDROGEL_ANCHOR": 9998.0,
        "HYDROGEL_W_ANCHOR": 0.0,
        "HYDROGEL_W_MA": 1.0,
        "HYDROGEL_MA_WINDOW": 1000,
        "HYDROGEL_ANCHOR_WEIGHT": 1.0,
        "HYDROGEL_DRIFT_THRESHOLD": 20.0,
        "HYDROGEL_FALLBACK_ANCHOR_WEIGHT": 0.5,
        "HYDROGEL_TAKE_EDGE": 8,
        "HYDROGEL_TIGHT": 1,
        "HYDROGEL_WIDE": 4,
        "HYDROGEL_MAX_QUOTE": 120,
        "HYDROGEL_SOFTCAP": 190,
        "HYDROGEL_HARDCAP": 200,
        "HYDROGEL_YARDAGE": "HYDROGEL_HARDCAP - HYDROGEL_SOFTCAP",
    }
    hydro_total = dict(hydro_safe, HYDROGEL_FALLBACK_ANCHOR_WEIGHT=0.8)
    velvet_simple = {
        "VELVET_ANCHOR": 5248.0,
        "VELVET_EMA_ALPHA": 0.0,
        "VELVET_ANCHOR_WEIGHT": 1.0,
        "VELVET_DRIFT_THRESHOLD": 999999.0,
        "VELVET_FALLBACK_ANCHOR_WEIGHT": 1.0,
        "VELVET_TAKE_EDGE": 12,
        "VELVET_TIGHT": 1,
        "VELVET_WIDE": 4,
        "VELVET_MAX_QUOTE": 80,
        "VELVET_SOFTCAP": 60,
        "VELVET_HARDCAP": 200,
        "VELVET_YARDAGE": "VELVET_HARDCAP - VELVET_SOFTCAP",
        "VELVET_USE_ANCHOR_INIT": 1,
    }
    skip_4000_on = {"VEV_SKIP_QUOTE: set": "{4500}"}
    smile_points = {
        "VEV_IV_POINT_MODE": 1,
        "VEV_IV_POINT_ALPHA": 0.02,
        "VEV_IV_POINT_HIST_WEIGHT": 0.5,
    }
    smile_fit = {
        "VEV_SMILE_FIT_MODE": 5,
        "VEV_CURRENT_SMILE_WEIGHT": 1.0,
        "VEV_SMILE_EMA": 0.35,
    }
    z_simple = {"VEV_ENTRY_Z": 0.75, "VEV_EXIT_Z": 0.25, "VEV_Z_METHOD": 0}
    hedge_light = {"VELVET_HEDGE_CAP": 0, "VEV_HEDGE_MODE": 1, "VEV_HEDGE_FRAC": 0.25}

    variants = [
        {"label": "current"},
        {"label": "safe_mm_no_vev", "ENABLE_VEV": 0, **hydro_safe, **velvet_simple},
        {"label": "safe_mm_current_vev", "ENABLE_VEV": 1, **hydro_safe, **velvet_simple},
        {"label": "total_hydro_current_vev", "ENABLE_VEV": 1, **hydro_total, **velvet_simple},
        {"label": "safe_mm_skip_4500", "ENABLE_VEV": 1, **hydro_safe, **velvet_simple, **skip_4000_on},
        {"label": "safe_mm_skip_4500_points", "ENABLE_VEV": 1, **hydro_safe, **velvet_simple, **skip_4000_on, **smile_points},
        {"label": "safe_mm_skip_4500_smilefit", "ENABLE_VEV": 1, **hydro_safe, **velvet_simple, **skip_4000_on, **smile_fit},
        {"label": "safe_mm_skip_4500_z", "ENABLE_VEV": 1, **hydro_safe, **velvet_simple, **skip_4000_on, **z_simple},
        {"label": "safe_mm_skip_4500_hedge", "ENABLE_VEV": 1, **hydro_safe, **velvet_simple, **skip_4000_on, **hedge_light},
    ]
    return variants


def sweep_validate_top():
    """Validate the current applied strategy against the top-4 combined candidates."""
    hydro_safe = {
        "HYDROGEL_ANCHOR": 9998.0,
        "HYDROGEL_W_ANCHOR": 0.0,
        "HYDROGEL_W_MA": 1.0,
        "HYDROGEL_MA_WINDOW": 1000,
        "HYDROGEL_ANCHOR_WEIGHT": 1.0,
        "HYDROGEL_DRIFT_THRESHOLD": 20.0,
        "HYDROGEL_FALLBACK_ANCHOR_WEIGHT": 0.5,
        "HYDROGEL_TAKE_EDGE": 8,
        "HYDROGEL_TIGHT": 1,
        "HYDROGEL_WIDE": 4,
        "HYDROGEL_MAX_QUOTE": 120,
        "HYDROGEL_SOFTCAP": 190,
        "HYDROGEL_HARDCAP": 200,
        "HYDROGEL_YARDAGE": "HYDROGEL_HARDCAP - HYDROGEL_SOFTCAP",
    }
    hydro_total = dict(hydro_safe, HYDROGEL_FALLBACK_ANCHOR_WEIGHT=0.8)
    velvet_simple = {
        "VELVET_ANCHOR": 5248.0,
        "VELVET_EMA_ALPHA": 0.0,
        "VELVET_ANCHOR_WEIGHT": 1.0,
        "VELVET_DRIFT_THRESHOLD": 999999.0,
        "VELVET_FALLBACK_ANCHOR_WEIGHT": 1.0,
        "VELVET_TAKE_EDGE": 12,
        "VELVET_TIGHT": 1,
        "VELVET_WIDE": 4,
        "VELVET_MAX_QUOTE": 80,
        "VELVET_SOFTCAP": 60,
        "VELVET_HARDCAP": 200,
        "VELVET_YARDAGE": "VELVET_HARDCAP - VELVET_SOFTCAP",
        "VELVET_USE_ANCHOR_INIT": 1,
    }
    skip_4500 = {"VEV_SKIP_QUOTE: set": "{4500}"}
    hedge_light = {"VELVET_HEDGE_CAP": 0, "VEV_HEDGE_MODE": 1, "VEV_HEDGE_FRAC": 0.25}

    variants = [
        {"label": "current_applied"},
        {"label": "safe_mm_no_vev", "ENABLE_VEV": 0, **hydro_safe, **velvet_simple},
        {"label": "safe_mm_skip_4500", "ENABLE_VEV": 1, **hydro_safe, **velvet_simple, **skip_4500},
        {"label": "total_hydro_skip_4500", "ENABLE_VEV": 1, **hydro_total, **velvet_simple, **skip_4500},
        {"label": "safe_mm_skip_4500_hedge", "ENABLE_VEV": 1, **hydro_safe, **velvet_simple, **skip_4500, **hedge_light},
    ]
    return variants


def sweep_perturb_winner():
    """Quick plateau sanity around safe_mm_skip_4500_hedge winner."""
    base = {
        "ENABLE_VEV": 1,
        "HYDROGEL_ANCHOR": 9998.0,
        "HYDROGEL_W_ANCHOR": 0.0,
        "HYDROGEL_W_MA": 1.0,
        "HYDROGEL_MA_WINDOW": 1000,
        "HYDROGEL_ANCHOR_WEIGHT": 1.0,
        "HYDROGEL_DRIFT_THRESHOLD": 20.0,
        "HYDROGEL_TIGHT": 1,
        "HYDROGEL_WIDE": 4,
        "HYDROGEL_MAX_QUOTE": 120,
        "HYDROGEL_SOFTCAP": 190,
        "HYDROGEL_HARDCAP": 200,
        "HYDROGEL_YARDAGE": "HYDROGEL_HARDCAP - HYDROGEL_SOFTCAP",
        "VELVET_ANCHOR": 5248.0,
        "VELVET_EMA_ALPHA": 0.0,
        "VELVET_ANCHOR_WEIGHT": 1.0,
        "VELVET_DRIFT_THRESHOLD": 999999.0,
        "VELVET_FALLBACK_ANCHOR_WEIGHT": 1.0,
        "VELVET_TIGHT": 1,
        "VELVET_WIDE": 4,
        "VELVET_TAKE_EDGE": 12,
        "VELVET_MAX_QUOTE": 80,
        "VELVET_SOFTCAP": 60,
        "VELVET_HARDCAP": 200,
        "VELVET_YARDAGE": "VELVET_HARDCAP - VELVET_SOFTCAP",
        "VELVET_USE_ANCHOR_INIT": 1,
        "VEV_SKIP_QUOTE: set": "{4500}",
        "VELVET_HEDGE_CAP": 0,
        "VEV_HEDGE_MODE": 1,
    }
    # Keep this tiny for speed: 3*1*3 = 9 combos.
    grid = {
        "HYDROGEL_TAKE_EDGE": [6, 7, 8],
        "HYDROGEL_FALLBACK_ANCHOR_WEIGHT": [0.5],
        "VEV_HEDGE_FRAC": [0.15, 0.25, 0.35],
    }
    keys = list(grid.keys())
    combos = []
    for values in itertools.product(*[grid[k] for k in keys]):
        combo = dict(base)
        for k, v in zip(keys, values):
            combo[k] = v
        combos.append(combo)
    return combos


def sweep_take_edges():
    """Grid over fair-value take edges before passive market making."""
    grid = {
        "HYDROGEL_TAKE_EDGE": [0, 1, 2, 3, 5, 9999],
        "VELVET_TAKE_EDGE": [0, 1, 2, 3, 5, 9999],
    }
    keys = list(grid.keys())
    vals = [grid[k] for k in keys]
    return [dict(zip(keys, v)) for v in itertools.product(*vals)]


def sweep_hedge():
    """Grid over delta hedge modes and OTM skip."""
    variants = [
        {"label": "baseline_no_hedge",
         "VEV_HEDGE_MODE": 9,  # no-op mode (neither 0 nor 1 triggers)
         "VELVET_SOFTCAP": 180, "VELVET_HARDCAP": 200,
         "VEV_SKIP_QUOTE: set": "set()"},
        {"label": "hedge_mode0_all",
         "VEV_HEDGE_MODE": 0, "VEV_SKIP_QUOTE: set": "set()"},
        {"label": "hedge_mode1_all",
         "VEV_HEDGE_MODE": 1, "VEV_SKIP_QUOTE: set": "set()"},
        {"label": "hedge_mode0_skip_deep",
         "VEV_HEDGE_MODE": 0, "VEV_SKIP_QUOTE: set": "{4000, 4500}"},
        {"label": "hedge_mode1_skip_deep",
         "VEV_HEDGE_MODE": 1, "VEV_SKIP_QUOTE: set": "{4000, 4500}"},
        {"label": "no_hedge_no_skew",
         "VEV_HEDGE_MODE": 9,
         "VELVET_SOFTCAP": 60, "VELVET_HARDCAP": 80,
         "VEV_SKIP_QUOTE: set": "set()"},
    ]
    return variants


def run_sweep(mode="params"):
    results = []
    print(f"Backtesting round {ROUND}")
    if mode == "params":
        combos, deps_fn = sweep_params()
        print(f"Running {len(combos)} parameter combos...")
        for i, combo in enumerate(combos):
            combo = deps_fn(dict(combo))
            replacements = {}
            for k, v in combo.items():
                replacements[k] = v
            t0 = time.time()
            tmp = make_variant(replacements)
            try:
                r = run_backtest(tmp)
            finally:
                os.unlink(tmp)
            elapsed = time.time() - t0
            row = {**{k: combo[k] for k in ["HYDROGEL_TAKE_EDGE", "VELVET_TAKE_EDGE", "VEV_TAKE_EDGE", "VEV_STRIKE_CAP", "VEV_SMILE_EMA", "VEV_TIGHT_SIZE_FRAC", "VEV_WIDE_OFFSET", "VEV_DELTA_DIVISOR"]},
                   **{k: r.get(k, "") for k in ["total", "min_day", "day_std", "vev_total", "vev_min_day", "worst_strike"]}}
            results.append(row)
            if (i + 1) % 10 == 0 or i == 0:
                print(f"  [{i+1}/{len(combos)}] {elapsed:.1f}s  total={r.get('total',0):,.0f}  min_day={r.get('min_day',0):,.0f}  vev={r.get('vev_total',0):,.0f}")
        out = out_path("params")
    elif mode == "smiles":
        combos = sweep_smiles()
        print(f"Running {len(combos)} smile combos...")
        for i, combo in enumerate(combos):
            t0 = time.time()
            tmp = make_variant(combo)
            try:
                r = run_backtest(tmp)
            finally:
                os.unlink(tmp)
            elapsed = time.time() - t0
            row = {
                **{k: combo[k] for k in [
                    "VEV_SMILE_FIT_MODE", "VEV_INCLUDE_PINNED_IN_FIT",
                    "VEV_CURRENT_SMILE_WEIGHT", "VEV_SMILE_EMA",
                    "VEV_WEIGHT_SMILE_BY_SPREAD", "VEV_IV_BLEND",
                ]},
                **{k: r.get(k, "") for k in [
                    "total", "min_day", "day_std", "vev_total", "vev_min_day", "worst_strike",
                ]},
            }
            results.append(row)
            print(
                f"  [{i+1}/{len(combos)}] {elapsed:.1f}s "
                f"mode={combo['VEV_SMILE_FIT_MODE']} pinned={combo['VEV_INCLUDE_PINNED_IN_FIT']} "
                f"w={combo['VEV_CURRENT_SMILE_WEIGHT']} ema={combo['VEV_SMILE_EMA']} "
                f"total={r.get('total',0):,.0f} vev={r.get('vev_total',0):,.0f}"
            )
        out = out_path("smiles")
    elif mode == "hydrogel":
        combos = sweep_hydrogel()
        print(f"Running {len(combos)} hydrogel combos...")
        for i, combo in enumerate(combos):
            t0 = time.time()
            tmp = make_variant(combo)
            try:
                r = run_backtest(tmp)
            finally:
                os.unlink(tmp)
            elapsed = time.time() - t0
            hydrogel_total = sum(d.get("HYDROGEL_PACK", 0) for d in r.get("days", {}).values())
            row = {
                **combo,
                "hydrogel_total": hydrogel_total,
                **{k: r.get(k, "") for k in ["total", "min_day", "day_std", "vev_total", "vev_min_day", "worst_strike"]},
            }
            results.append(row)
            print(
                f"  [{i+1}/{len(combos)}] {elapsed:.1f}s "
                f"anchor={combo['HYDROGEL_ANCHOR']} w={combo['HYDROGEL_W_ANCHOR']}/{combo['HYDROGEL_W_MA']} "
                f"hydro={hydrogel_total:,.0f} total={r.get('total',0):,.0f}"
            )
        out = out_path("hydrogel")
    elif mode in {"hydrogel_fair", "hydrogel_mm", "hydrogel_confirm", "hydrogel_fallback"}:
        if mode == "hydrogel_fair":
            combos = sweep_hydrogel_fair()
        elif mode == "hydrogel_confirm":
            combos = sweep_hydrogel_confirm()
        elif mode == "hydrogel_fallback":
            combos = sweep_hydrogel_fallback()
        else:
            combos = sweep_hydrogel_mm()
        print(f"Running {len(combos)} {mode} combos...")
        for i, combo in enumerate(combos):
            t0 = time.time()
            tmp = make_variant(combo)
            try:
                r = run_backtest(tmp)
            finally:
                os.unlink(tmp)
            elapsed = time.time() - t0
            days = r.get("days", {})
            hydrogel_total = sum(d.get("HYDROGEL_PACK", 0) for d in days.values())
            hydrogel_min_day = min((d.get("HYDROGEL_PACK", 0) for d in days.values()), default=0)
            row = {
                **combo,
                "hydrogel_total": hydrogel_total,
                "hydrogel_min_day": hydrogel_min_day,
                **{k: r.get(k, "") for k in ["total", "min_day", "day_std"]},
            }
            results.append(row)
            print(
                f"  [{i+1}/{len(combos)}] {elapsed:.1f}s "
                f"anchor={combo.get('HYDROGEL_ANCHOR')} edge={combo.get('HYDROGEL_TAKE_EDGE')} "
                f"tight={combo.get('HYDROGEL_TIGHT', '')} wide={combo.get('HYDROGEL_WIDE', '')} "
                f"q={combo.get('HYDROGEL_MAX_QUOTE', '')} soft={combo.get('HYDROGEL_SOFTCAP', '')} "
                f"hydro={hydrogel_total:,.0f} min={hydrogel_min_day:,.0f}"
            )
        out = out_path(mode)
    elif mode in {"options", "options_vol_blend"}:
        combos = sweep_options_vol_blend() if mode == "options_vol_blend" else sweep_options_quick()
        print(f"Running {len(combos)} option combos...")
        for i, combo in enumerate(combos):
            t0 = time.time()
            tmp = make_variant(combo)
            try:
                r = run_backtest(tmp)
            finally:
                os.unlink(tmp)
            elapsed = time.time() - t0
            row = {
                **combo,
                **{k: r.get(k, "") for k in ["total", "min_day", "day_std", "vev_total", "vev_min_day", "worst_strike"]},
            }
            results.append(row)
            print(
                f"  [{i+1}/{len(combos)}] {elapsed:.1f}s "
                f"minfair={combo.get('VEV_MIN_TRADE_FAIR', '')} entry={combo.get('VEV_ENTRY_Z', '')} "
                f"rvw={combo.get('VEV_REALIZED_VOL_WEIGHT', '')} rva={combo.get('VEV_REALIZED_VOL_ALPHA', '')} "
                f"frac={combo.get('VEV_PASSIVE_SIZE_FRAC', '')} take={combo.get('VEV_MAX_TAKE', '')} "
                f"total={r.get('total',0):,.0f} vev={r.get('vev_total',0):,.0f}"
            )
        out = out_path("options_vol_blend" if mode == "options_vol_blend" else "options")
    elif mode == "velvet":
        combos = sweep_velvet()
        print(f"Running {len(combos)} velvet combos...")
        for i, combo in enumerate(combos):
            t0 = time.time()
            tmp = make_variant(combo)
            try:
                r = run_backtest(tmp)
            finally:
                os.unlink(tmp)
            elapsed = time.time() - t0
            velvet_total = sum(d.get("VELVETFRUIT_EXTRACT", 0) for d in r.get("days", {}).values())
            row = {
                **combo,
                "velvet_total": velvet_total,
                **{k: r.get(k, "") for k in ["total", "min_day", "day_std", "vev_total", "vev_min_day", "worst_strike"]},
            }
            results.append(row)
            print(
                f"  [{i+1}/{len(combos)}] {elapsed:.1f}s "
                f"alpha={combo['VELVET_EMA_ALPHA']} edge={combo['VELVET_TAKE_EDGE']} "
                f"q={combo['VELVET_MAX_QUOTE']} wide={combo['VELVET_WIDE']} "
                f"velvet={velvet_total:,.0f} total={r.get('total',0):,.0f}"
            )
        out = out_path("velvet")
    elif mode in {"velvet_fair", "velvet_mm", "velvet_refine", "velvet_fallback"}:
        if mode == "velvet_fair":
            combos = sweep_velvet_fair()
        elif mode == "velvet_refine":
            combos = sweep_velvet_refine()
        elif mode == "velvet_fallback":
            combos = sweep_velvet_fallback()
        else:
            combos = sweep_velvet_mm()
        print(f"Running {len(combos)} {mode} combos...")
        for i, combo in enumerate(combos):
            t0 = time.time()
            tmp = make_variant(combo)
            try:
                r = run_backtest(tmp)
            finally:
                os.unlink(tmp)
            elapsed = time.time() - t0
            days = r.get("days", {})
            velvet_total = sum(d.get("VELVETFRUIT_EXTRACT", 0) for d in days.values())
            velvet_min_day = min((d.get("VELVETFRUIT_EXTRACT", 0) for d in days.values()), default=0)
            row = {
                **combo,
                "velvet_total": velvet_total,
                "velvet_min_day": velvet_min_day,
                **{k: r.get(k, "") for k in ["total", "min_day", "day_std"]},
            }
            results.append(row)
            print(
                f"  [{i+1}/{len(combos)}] {elapsed:.1f}s "
                f"anchor={combo.get('VELVET_ANCHOR')} ema={combo.get('VELVET_EMA_ALPHA')} "
                f"edge={combo.get('VELVET_TAKE_EDGE')} velvet={velvet_total:,.0f} min={velvet_min_day:,.0f}"
            )
        out = out_path(mode)
    elif mode == "takes":
        combos = sweep_take_edges()
        print(f"Running {len(combos)} take-edge combos...")
        for i, combo in enumerate(combos):
            t0 = time.time()
            tmp = make_variant(combo)
            try:
                r = run_backtest(tmp)
            finally:
                os.unlink(tmp)
            elapsed = time.time() - t0
            days = r.get("days", {})
            hydrogel_total = sum(d.get("HYDROGEL_PACK", 0) for d in days.values())
            velvet_total = sum(d.get("VELVETFRUIT_EXTRACT", 0) for d in days.values())
            row = {
                **combo,
                "hydrogel_total": hydrogel_total,
                "velvet_total": velvet_total,
                **{k: r.get(k, "") for k in ["total", "min_day", "day_std", "vev_total", "vev_min_day", "worst_strike"]},
            }
            results.append(row)
            print(
                f"  [{i+1}/{len(combos)}] {elapsed:.1f}s "
                f"hydro_edge={combo['HYDROGEL_TAKE_EDGE']} velvet_edge={combo['VELVET_TAKE_EDGE']} "
                f"hydro={hydrogel_total:,.0f} velvet={velvet_total:,.0f} total={r.get('total',0):,.0f}"
            )
        out = out_path("takes")
    elif mode == "hedge":
        variants = sweep_hedge()
        print(f"Running {len(variants)} hedge variants...")
        for i, v in enumerate(variants):
            label = v.pop("label")
            t0 = time.time()
            tmp = make_variant(v)
            try:
                r = run_backtest(tmp)
            finally:
                os.unlink(tmp)
            elapsed = time.time() - t0
            days = r.get("days", {})
            velvet_total = sum(d.get("VELVETFRUIT_EXTRACT", 0) for d in days.values())
            deep_pnl = sum(
                d.get(p, 0) for d in days.values()
                for p in ["VEV_4000", "VEV_4500", "VEV_6000", "VEV_6500"]
            )
            row = {
                "variant": label,
                "velvet_total": velvet_total,
                "deep_strike_pnl": deep_pnl,
                **{k: r.get(k, "") for k in [
                    "total", "min_day", "max_day", "day_std",
                    "vev_total", "vev_min_day", "worst_strike",
                ]},
            }
            results.append(row)
            print(
                f"  [{i+1}/{len(variants)}] {label} {elapsed:.1f}s "
                f"total={r.get('total',0):,.0f} velvet={velvet_total:,.0f} "
                f"vev={r.get('vev_total',0):,.0f} deep={deep_pnl:,.0f}"
            )
        out = out_path("hedge")
    elif mode == "mm_combined":
        variants = sweep_mm_combined()
        print(f"Running {len(variants)} combined MM variants...")
        for i, variant in enumerate(variants):
            v = dict(variant)
            label = v.pop("label")
            t0 = time.time()
            tmp = make_variant(v)
            try:
                r = run_backtest(tmp)
            finally:
                os.unlink(tmp)
            elapsed = time.time() - t0
            days = r.get("days", {})
            hydrogel_total = sum(d.get("HYDROGEL_PACK", 0) for d in days.values())
            velvet_total = sum(d.get("VELVETFRUIT_EXTRACT", 0) for d in days.values())
            row = {
                "variant": label,
                **v,
                "hydrogel_total": hydrogel_total,
                "velvet_total": velvet_total,
                **{k: r.get(k, "") for k in [
                    "total", "min_day", "max_day", "day_std",
                    "vev_total", "vev_min_day", "worst_strike",
                ]},
            }
            results.append(row)
            print(
                f"  [{i+1}/{len(variants)}] {label} {elapsed:.1f}s "
                f"total={r.get('total',0):,.0f} hydro={hydrogel_total:,.0f} velvet={velvet_total:,.0f}"
            )
        out = out_path("mm_combined")
    elif mode == "combined_candidates":
        variants = sweep_combined_candidates()
        print(f"Running {len(variants)} combined candidate variants...")
        for i, variant in enumerate(variants):
            v = dict(variant)
            label = v.pop("label")
            t0 = time.time()
            tmp = make_variant(v)
            try:
                r = run_backtest(tmp)
            finally:
                os.unlink(tmp)
            elapsed = time.time() - t0
            days = r.get("days", {})
            hydrogel_total = sum(d.get("HYDROGEL_PACK", 0) for d in days.values())
            velvet_total = sum(d.get("VELVETFRUIT_EXTRACT", 0) for d in days.values())
            row = {
                "variant": label,
                **v,
                "hydrogel_total": hydrogel_total,
                "velvet_total": velvet_total,
                **{k: r.get(k, "") for k in [
                    "total", "min_day", "max_day", "day_std",
                    "vev_total", "vev_min_day", "worst_strike",
                ]},
            }
            results.append(row)
            print(
                f"  [{i+1}/{len(variants)}] {label} {elapsed:.1f}s "
                f"total={r.get('total',0):,.0f} min={r.get('min_day',0):,.0f} "
                f"hydro={hydrogel_total:,.0f} velvet={velvet_total:,.0f} vev={r.get('vev_total',0):,.0f}"
            )
        out = out_path("combined_candidates")
    elif mode == "validate_top":
        variants = sweep_validate_top()
        print(f"Running {len(variants)} validate_top variants...")
        for i, variant in enumerate(variants):
            v = dict(variant)
            label = v.pop("label")
            t0 = time.time()
            tmp = make_variant(v)
            try:
                r = run_backtest(tmp)
            finally:
                os.unlink(tmp)
            elapsed = time.time() - t0
            days = r.get("days", {})
            hydrogel_total = sum(d.get("HYDROGEL_PACK", 0) for d in days.values())
            velvet_total = sum(d.get("VELVETFRUIT_EXTRACT", 0) for d in days.values())
            day_pnls = {f"day_{d}": sum(p.values()) for d, p in days.items()}
            avg_day = sum(day_pnls.values()) / max(len(day_pnls), 1)
            row = {
                "variant": label,
                **v,
                "hydrogel_total": hydrogel_total,
                "velvet_total": velvet_total,
                **day_pnls,
                "avg_day": avg_day,
                **{k: r.get(k, "") for k in [
                    "total", "min_day", "max_day", "day_std",
                    "vev_total", "vev_min_day", "worst_strike",
                ]},
            }
            results.append(row)
            print(
                f"  [{i+1}/{len(variants)}] {label} {elapsed:.1f}s "
                f"total={r.get('total',0):,.0f} min={r.get('min_day',0):,.0f} avg={avg_day:,.0f} "
                f"days={ {k: f'{v:,.0f}' for k, v in day_pnls.items()} }"
            )
        out = out_path("validate_top")
    elif mode == "perturb_winner":
        combos = sweep_perturb_winner()
        print(f"Running {len(combos)} perturb_winner combos...")
        for i, combo in enumerate(combos):
            t0 = time.time()
            tmp = make_variant(combo)
            try:
                r = run_backtest(tmp)
            finally:
                os.unlink(tmp)
            elapsed = time.time() - t0
            days = r.get("days", {})
            day_pnls = {f"day_{d}": sum(p.values()) for d, p in days.items()}
            avg_day = sum(day_pnls.values()) / max(len(day_pnls), 1)
            row = {
                "HYDROGEL_TAKE_EDGE": combo.get("HYDROGEL_TAKE_EDGE"),
                "HYDROGEL_FALLBACK_ANCHOR_WEIGHT": combo.get("HYDROGEL_FALLBACK_ANCHOR_WEIGHT"),
                "VEV_HEDGE_FRAC": combo.get("VEV_HEDGE_FRAC"),
                **day_pnls,
                "avg_day": avg_day,
                **{k: r.get(k, "") for k in [
                    "total", "min_day", "max_day", "day_std",
                    "vev_total", "vev_min_day", "worst_strike",
                ]},
            }
            results.append(row)
            print(
                f"  [{i+1}/{len(combos)}] {elapsed:.1f}s "
                f"edge={combo.get('HYDROGEL_TAKE_EDGE')} fwt={combo.get('HYDROGEL_FALLBACK_ANCHOR_WEIGHT')} "
                f"hfrac={combo.get('VEV_HEDGE_FRAC')} total={r.get('total',0):,.0f} min={r.get('min_day',0):,.0f}"
            )
        out = out_path("perturb_winner")
    else:
        # First find best params from existing sweep
        best = load_best_params()
        toggles = sweep_toggles(best)
        print(f"Running {len(toggles)} toggle tests...")
        # baseline
        tmp = make_variant(best)
        try:
            r = run_backtest(tmp)
        finally:
            os.unlink(tmp)
        results.append({"toggle": "BASELINE", **{k: r.get(k, "") for k in ["total", "min_day", "day_std", "vev_total", "vev_min_day", "worst_strike"]}})
        print(f"  BASELINE: total={r.get('total',0):,.0f}  min_day={r.get('min_day',0):,.0f}")

        for name, overrides in toggles.items():
            merged = {**best, **overrides}
            tmp = make_variant(merged)
            try:
                r = run_backtest(tmp)
            finally:
                os.unlink(tmp)
            row = {"toggle": name, **{k: r.get(k, "") for k in ["total", "min_day", "day_std", "vev_total", "vev_min_day", "worst_strike"]}}
            results.append(row)
            print(f"  {name}: total={r.get('total',0):,.0f}  min_day={r.get('min_day',0):,.0f}")
        out = out_path("toggles")

    with open(out, "w", newline="") as f:
        fieldnames = []
        for row in results:
            for key in row.keys():
                if key not in fieldnames:
                    fieldnames.append(key)
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        w.writerows(results)
    print(f"\nWrote {out} ({len(results)} rows)")


def load_best_params():
    """Load best params from sweep_params.csv."""
    p = ROOT / "sweep_params.csv"
    if not p.exists():
        return {}
    rows = list(csv.DictReader(open(p)))
    rows.sort(key=lambda r: (float(r.get("min_day", 0)), float(r.get("total", 0))), reverse=True)
    best = rows[0]
    out = {}
    for k in [
        "HYDROGEL_TAKE_EDGE", "VELVET_TAKE_EDGE", "VEV_TAKE_EDGE",
        "VEV_STRIKE_CAP", "VEV_SMILE_EMA", "VEV_TIGHT_SIZE_FRAC",
        "VEV_WIDE_OFFSET", "VEV_DELTA_DIVISOR", "VEV_SMILE_FIT_MODE",
        "VEV_INCLUDE_PINNED_IN_FIT", "VEV_CURRENT_SMILE_WEIGHT",
        "VEV_WEIGHT_SMILE_BY_SPREAD", "VEV_IV_BLEND",
    ]:
        if k not in best:
            continue
        v = best[k]
        out[k] = int(float(v)) if float(v) == int(float(v)) else float(v)
    cap = out["VEV_STRIKE_CAP"]
    out["VEV_SOFTCAP"] = max(int(cap * 0.6), 10)
    out["VEV_MAX_QUOTE"] = max(int(cap * 0.2), 5)
    out["VEV_HARDCAP"] = "VEV_STRIKE_CAP"
    out["VEV_YARDAGE"] = "VEV_HARDCAP - VEV_SOFTCAP"
    return out


if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else "params"
    run_sweep(mode)
