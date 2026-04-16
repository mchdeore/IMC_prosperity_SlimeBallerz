"""Cross-check the exhaustive-sweep conclusions against the raw order books.

Addresses three questions:
  1. Does the ACO L1 spread actually leave room to "improve by 1 tick"? How
     often is spread == 1 vs >= 2 vs >= 3? If most spreads are 1, `join` is
     structurally the only legal choice and `improve_1` forces you to cross.
  2. Is IPR's fair value really a near-perfect linear ramp (slope × ticks since
     start)? If yes, the `slope` sweep is a model-calibration test, not an
     asset-behavior test.
  3. Sanity-check joint PnL = ACO PnL + IPR PnL.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
BOOKS_DIR = ROOT / "ROUND_1_DATA" / "from imc package"

ACO = "ASH_COATED_OSMIUM"
IPR = "INTARIAN_PEPPER_ROOT"


def load_day(day: int) -> pd.DataFrame:
    path = BOOKS_DIR / f"prices_round_1_day_{day}.csv"
    df = pd.read_csv(path, sep=";")
    return df


def aco_book_stats(df: pd.DataFrame, label: str) -> None:
    a = df[(df["product"] == ACO)].copy()
    a["spread"] = a["ask_price_1"] - a["bid_price_1"]
    a["mid_l1"] = (a["ask_price_1"] + a["bid_price_1"]) / 2

    # L2+L3 "book-weighted" mid (matches the ACO fair-value logic in the strategy)
    def depth_mid(row: pd.Series) -> float | None:
        bids, asks = [], []
        for i in (1, 2, 3):
            bp, bv = row.get(f"bid_price_{i}"), row.get(f"bid_volume_{i}")
            if pd.notna(bp) and pd.notna(bv):
                bids.append((bp, bv))
            ap, av = row.get(f"ask_price_{i}"), row.get(f"ask_volume_{i}")
            if pd.notna(ap) and pd.notna(av):
                asks.append((ap, av))
        if not bids or not asks:
            return None
        bmid = sum(p * v for p, v in bids) / sum(v for _, v in bids)
        amid = sum(p * v for p, v in asks) / sum(v for _, v in asks)
        return (bmid + amid) / 2

    a["fair_bookweighted"] = a.apply(depth_mid, axis=1)
    a["fair_l1_dev"] = a["mid_l1"] - a["fair_bookweighted"]

    print(f"\n{label} — ASH_COATED_OSMIUM")
    print(f"  rows with both sides: {a.dropna(subset=['bid_price_1','ask_price_1']).shape[0]}/{len(a)}")
    spread = a["spread"].dropna()
    if len(spread) == 0:
        print("  (no two-sided quotes)")
        return
    print("  L1 spread distribution (ticks):")
    counts = spread.value_counts().sort_index()
    total = counts.sum()
    for k, v in counts.items():
        print(f"    spread={int(k):>2}: {v:>6}  ({v/total:6.1%})")
    print(f"  mean spread = {spread.mean():.3f}, median = {spread.median():.1f}")

    # How often does the book-weighted fair line up with L1 mid?
    dev = a["fair_l1_dev"].dropna()
    print(f"  bookweighted-fair vs L1-mid:")
    print(f"    mean={dev.mean():+.3f}  std={dev.std():.3f}  "
          f"|dev|>0.5 ticks: {(dev.abs()>0.5).mean():.1%}")

    # Where does `improve_1` land relative to fair?
    # improve_1 bid = bid_price_1 + 1, ask = ask_price_1 - 1.
    a["improve_bid_vs_fair"] = a["bid_price_1"] + 1 - a["fair_bookweighted"]
    a["improve_ask_vs_fair"] = a["ask_price_1"] - 1 - a["fair_bookweighted"]
    a["join_bid_vs_fair"] = a["bid_price_1"] - a["fair_bookweighted"]
    a["join_ask_vs_fair"] = a["ask_price_1"] - a["fair_bookweighted"]
    both_sides = a.dropna(subset=["bid_price_1", "ask_price_1", "fair_bookweighted"])
    print("  maker-quote placement relative to book-weighted fair (ticks):")
    for col in ["join_bid_vs_fair", "improve_bid_vs_fair",
                "join_ask_vs_fair", "improve_ask_vs_fair"]:
        s = both_sides[col]
        print(f"    {col:>24}: mean={s.mean():+.3f}  median={s.median():+.1f}  "
              f"p_crossing_fair={((s>0 if 'bid' in col else s<0)).mean():.1%}")

    # Key: when spread==1, "improve_1" is illegal / locks/crosses book
    narrow = both_sides["spread"] == 1
    print(f"  fraction of time spread==1 (improve_1 illegal/crosses): "
          f"{narrow.mean():.1%}")
    narrow2 = both_sides["spread"] == 2
    print(f"  fraction of time spread==2 (improve_1 crosses/inside): "
          f"{narrow2.mean():.1%}")


def ipr_drift_fit(df: pd.DataFrame, label: str) -> None:
    p = df[df["product"] == IPR].copy()
    p = p.dropna(subset=["mid_price"])
    # Use the raw mid_price field to compare against strategy's slope param.
    # IPR fair = anchor + slope * (ticks_since_start) where anchor ~ first mid.
    ts = p["timestamp"].to_numpy(dtype=float)
    # In the strategy the drift is per-timestamp-step (each iter = 100 ts).
    # Fit mid = a + b*t where t is the per-step counter (timestamp / 100).
    t_steps = ts / 100.0
    y = p["mid_price"].to_numpy(dtype=float)
    slope, intercept = np.polyfit(t_steps, y, 1)
    pred = intercept + slope * t_steps
    resid = y - pred
    ss_res = float(np.sum(resid ** 2))
    ss_tot = float(np.sum((y - y.mean()) ** 2))
    r2 = 1.0 - ss_res / ss_tot if ss_tot > 0 else float("nan")

    print(f"\n{label} — INTARIAN_PEPPER_ROOT mid-price linear drift fit "
          f"(per 100-ts step)")
    print(f"  samples        : {len(y)}")
    print(f"  first / last   : {y[0]:.1f} / {y[-1]:.1f} "
          f"(range over day: {y.max()-y.min():.1f})")
    print(f"  fitted slope   : {slope:+.6f} per step  "
          f"(3-day sweep winner: 0.003)")
    print(f"  fitted intercept: {intercept:.2f}")
    print(f"  R^2            : {r2:.4f}")
    print(f"  residual std   : {resid.std():.3f}")
    print(f"  residual p95   : {np.percentile(np.abs(resid), 95):.3f}")

    # How much of the day's move is drift vs noise?
    drift_total = slope * (t_steps[-1] - t_steps[0])
    print(f"  total drift over day: {drift_total:+.2f}  "
          f"(vs price range {y.max()-y.min():.1f})")


def main() -> None:
    for day in (-2, -1, 0):
        df = load_day(day)
        aco_book_stats(df, f"day {day}")
    for day in (-2, -1, 0):
        df = load_day(day)
        ipr_drift_fit(df, f"day {day}")


if __name__ == "__main__":
    main()
