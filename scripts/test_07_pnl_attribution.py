"""
Test 07 - PnL attribution by phase
===================================

Match each executed fill back to the phase (take_pos / flatten / make)
that submitted it. Then compute "mark-to-fair" PnL for each phase:

    phase_pnl = sum over fills of (side_sign * (fair_at_close - fill_price) * qty)

Where fair_at_close is the fair price at EOD (same for all fills, so it's
effectively a mark-to-final price). This isolates per-phase profit from
position-closure accounting.

For better accuracy we also compute: for each fill, the immediate edge
relative to fair at THE TIME of that fill:
    fill_edge = side_sign * (fair_at_fill - fill_price)
    (positive if we filled at a better price than fair)

The sum of fill_edges across all fills of a phase = that phase's
theoretical edge capture (before drift realization). Final PnL = sum of
fill_edges + any drift-held-inventory term.

Runs primo_explorer with default config, 3 days, worse mode, order_log=True.

Output: results/primo_exploration/test_07_pnl_attribution.csv + table.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _backtest_helpers import day_to_arg, TRADER
from _log_parser import split_sections, iter_orders, get_our_fills

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "results" / "primo_exploration"
LOG_DIR = OUT / "logs"
OUT.mkdir(parents=True, exist_ok=True)
LOG_DIR.mkdir(parents=True, exist_ok=True)

DAYS = [-2, -1, 0]

PRODUCT_MAP = {"ASH_COATED_OSMIUM": "ACO", "INTARIAN_PEPPER_ROOT": "IPR"}


def run_and_get_log(day):
    out_log = LOG_DIR / f"attribution_day_{day}.log"
    env = os.environ.copy()
    env["EXPL_GLOBAL"] = json.dumps({"order_log": True})
    cmd = [
        "prosperity4btest", str(TRADER), day_to_arg(day),
        "--out", str(out_log), "--no-progress",
        "--match-trades", "worse",
    ]
    subprocess.run(cmd, cwd=str(ROOT), env=env,
                   capture_output=True, text=True, timeout=180)
    return out_log


def attribute_fills(log_path):
    """
    Returns list of per-fill rows with phase attribution.
    Each row: {ts, product, side, price, qty, fill_phase, fair_at_fill, fill_edge}.
    """
    sandbox_text, trade_text = split_sections(log_path)

    # Index our orders by (ts, product) -> list of (phase, side, price, qty)
    orders_by_key = {}
    for o in iter_orders(sandbox_text):
        key = (o["timestamp"], o["product"])
        orders_by_key.setdefault(key, []).append(o)

    attributed = []
    for fill in get_our_fills(trade_text):
        short = PRODUCT_MAP.get(fill["symbol"], fill["symbol"])
        key = (fill["timestamp"], short)
        candidates = orders_by_key.get(key, [])
        # Match fill to an order: same side, same price, qty <= order qty.
        matched_phase = "unknown"
        matched_fair = None
        for o in candidates:
            if o["side"] != fill["side"]:
                continue
            if o["price"] != fill["price"]:
                continue
            if o["qty"] >= fill["qty"]:
                matched_phase = o["phase"]
                matched_fair = o["fair"]
                o["qty"] -= fill["qty"]   # consume; next fills at same price go to other orders
                break
        # Fallback: match by side only if price didn't match (can happen
        # when take_positive crossed into book at multiple levels)
        if matched_phase == "unknown":
            for o in candidates:
                if o["side"] != fill["side"]:
                    continue
                matched_phase = o["phase"]
                matched_fair = o["fair"]
                break

        side_sign = 1 if fill["side"] == "B" else -1
        # "edge at fill" = side_sign * (fair - price)
        # For a BUY (side_sign=+1): positive edge means we bought below fair.
        # For a SELL (side_sign=-1): positive edge means we sold above fair.
        fill_edge = (side_sign * (matched_fair - fill["price"])) if matched_fair is not None else 0.0
        attributed.append({
            "timestamp":     fill["timestamp"],
            "product":       short,
            "side":          fill["side"],
            "price":         fill["price"],
            "qty":           fill["qty"],
            "fill_phase":    matched_phase,
            "fair_at_fill":  matched_fair,
            "fill_edge":     round(fill_edge, 2),
        })
    return attributed


def main():
    all_fills = []
    for day in DAYS:
        print(f"  Running day {day}...")
        log_path = run_and_get_log(day)
        fills = attribute_fills(log_path)
        for f in fills:
            f["day"] = day
        all_fills.extend(fills)
        n_matched = sum(1 for f in fills if f["fill_phase"] != "unknown")
        print(f"    {len(fills)} fills, {n_matched} phase-matched")

    df = pd.DataFrame(all_fills)
    fills_path = OUT / "test_07_pnl_attribution_fills.csv"
    df.to_csv(fills_path, index=False)
    print(f"\nSaved fill-level: {fills_path}")

    # Per-phase edge capture, per product
    agg = (df.groupby(["product", "fill_phase"])
             .agg(n_fills=("qty", "count"),
                  total_qty=("qty", "sum"),
                  total_edge=("fill_edge", lambda s: (s * df.loc[s.index, "qty"]).sum()),
                  mean_edge_per_share=("fill_edge", "mean"))
             .round(2)
             .reset_index())

    out_path = OUT / "test_07_pnl_attribution.csv"
    agg.to_csv(out_path, index=False)
    print(f"Saved aggregated: {out_path}")

    print("\n==== Per-phase edge capture (3-day totals) ====")
    print("  total_edge = sum over fills of qty * (fair - fill_price) * side_sign")
    print("  This is the THEORETICAL PnL from each phase at moment of fill.")
    print()
    for product in ["ACO", "IPR"]:
        sub = agg[agg["product"] == product]
        if sub.empty:
            continue
        print(f"\n  {product}:")
        print(sub.drop(columns=["product"]).to_string(index=False))
        total = sub["total_edge"].sum()
        print(f"    TOTAL edge capture: {total:.0f}")

    # Side breakdown
    print("\n==== Per-phase x side (3-day totals) ====")
    side_agg = (df.groupby(["product", "fill_phase", "side"])
                  .agg(total_qty=("qty", "sum"),
                       total_edge=("fill_edge", lambda s: (s * df.loc[s.index, "qty"]).sum()))
                  .round(0)
                  .reset_index())
    print(side_agg.to_string(index=False))

    print("\n  Note: 'edge capture' measures moment-of-fill PnL. Drift-holding PnL")
    print("  (e.g. IPR lots bought and held for drift) shows up in TAKE/MAKE edges")
    print("  because the fair used is current (not future). The actual backtest")
    print("  PnL includes drift realization which isn't captured here.")


if __name__ == "__main__":
    main()
