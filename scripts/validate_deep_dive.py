"""Deep-dive checks:

  1. ACO — what does the full L1/L2/L3 book look like? If L1 is wide, where do
     L2/L3 sit? Are L2/L3 *inside* L1 (so 'join' really joins near fair) or
     *outside* L1 (deep iceberg that 'join' actively avoids)?
  2. ACO — fill rate estimate: use trades to count takes per minute.
  3. IPR — does slope behave as a 'position-forcing' dial? Compute implied fair
     after N iters for each sweep slope, compare to real price, and quantify
     how long the strategy sits at the position cap.
  4. IPR — linear fair-value quality per day: R^2 and residual for slope=0.001
     (calibrated) vs slope=0.003 (sweep winner).
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
BOOKS_DIR = ROOT / "ROUND_1_DATA" / "from imc package"

ACO = "ASH_COATED_OSMIUM"
IPR = "INTARIAN_PEPPER_ROOT"


def load_prices(day: int) -> pd.DataFrame:
    return pd.read_csv(BOOKS_DIR / f"prices_round_1_day_{day}.csv", sep=";")


def load_trades(day: int) -> pd.DataFrame | None:
    path = BOOKS_DIR / f"trades_round_1_day_{day}.csv"
    if not path.exists():
        return None
    return pd.read_csv(path, sep=";")


def aco_book_shape(day: int) -> None:
    df = load_prices(day)
    a = df[df["product"] == ACO].dropna(subset=["bid_price_1", "ask_price_1"]).copy()

    # Relative offset of each L2/L3 quote from L1
    for side in ("bid", "ask"):
        for lvl in (2, 3):
            col = f"{side}_offset_{lvl}"
            a[col] = (a[f"{side}_price_{lvl}"] - a[f"{side}_price_1"]).abs()

    print(f"\nday {day}  ACO book shape  (offset of L2/L3 from L1, ticks)")
    for col in ["bid_offset_2", "bid_offset_3", "ask_offset_2", "ask_offset_3"]:
        s = a[col].dropna()
        if len(s) == 0:
            print(f"  {col}: (no data)")
            continue
        print(f"  {col:>14}: present={len(s)/len(a):5.1%}  "
              f"mean={s.mean():4.1f}  median={s.median():4.1f}  "
              f"p10={s.quantile(0.10):4.1f}  p90={s.quantile(0.90):4.1f}")

    # Volume concentration: how much volume sits at L1 vs L2+L3?
    for side in ("bid", "ask"):
        v1 = a[f"{side}_volume_1"].fillna(0)
        v23 = a[f"{side}_volume_2"].fillna(0) + a[f"{side}_volume_3"].fillna(0)
        share = v1 / (v1 + v23).replace(0, np.nan)
        print(f"  {side}_L1 volume share (vs L2+L3): "
              f"mean={share.mean():.2f}  median={share.median():.2f}")


def aco_trade_flow(day: int) -> None:
    trades = load_trades(day)
    if trades is None:
        return
    t = trades[trades["symbol"] == ACO].copy()
    if len(t) == 0:
        return
    print(f"\nday {day}  ACO trade flow")
    print(f"  total trades  : {len(t)}  total volume: {t['quantity'].sum()}")
    print(f"  trades / hour : ~{len(t)/2.78:.0f}  "
          f"(10000 ts/day ≈ 2.78 h at 100ts/s assumption)")
    print(f"  trades / iter : {len(t)/10000:.2f}")


def ipr_slope_vs_position(day: int) -> None:
    df = load_prices(day)
    p = df[df["product"] == IPR].dropna(subset=["mid_price"]).copy()
    p = p.sort_values("timestamp").reset_index(drop=True)
    ts = p["timestamp"].to_numpy(dtype=float)
    mid = p["mid_price"].to_numpy(dtype=float)

    # Linear fit: mid = a + b * ts
    b, a = np.polyfit(ts, mid, 1)
    pred = a + b * ts
    resid = mid - pred
    ss_res = float(np.sum(resid ** 2))
    ss_tot = float(np.sum((mid - mid.mean()) ** 2))
    r2 = 1.0 - ss_res / ss_tot if ss_tot else float("nan")

    print(f"\nday {day}  IPR linear fit of mid vs timestamp")
    print(f"  fitted slope = {b:+.6f} per ts-unit   "
          f"({b*100:+.4f} per iter (100ts))")
    print(f"  R^2 = {r2:.3f}   resid std = {resid.std():.1f}  "
          f"(price range {mid.max()-mid.min():.0f})")

    # For each sweep-slope, what is the strategy's implied fair at end-of-day
    # vs the actual price? If implied_fair >> price, the strategy's take-phase
    # will hit every ask (thinks fair is above ask) -> piles up long -> cap.
    anchor_fair = mid[0]
    end_ts = ts[-1]
    price_end = mid[-1]
    print(f"  actual price: {mid[0]:.1f} -> {price_end:.1f}  "
          f"(Δ = {price_end - mid[0]:+.1f})")
    for slope in (0.0, 0.0005, 0.001, 0.002, 0.003):
        implied_end = anchor_fair + slope * (end_ts - ts[0])
        drift_bias = implied_end - price_end
        print(f"    slope={slope:<6}: strategy-fair at EoD = {implied_end:.1f}  "
              f"(bias vs true price = {drift_bias:+.1f})")


def ipr_slope_vs_best_fit(day: int) -> None:
    df = load_prices(day)
    p = df[df["product"] == IPR].dropna(subset=["mid_price"]).copy()
    ts = p["timestamp"].to_numpy(dtype=float)
    mid = p["mid_price"].to_numpy(dtype=float)
    b, a = np.polyfit(ts, mid, 1)

    # For each candidate strategy slope, compute residual wrt true mid.
    print(f"\nday {day}  IPR: residual std of strategy-fair vs mid for each slope")
    for slope in (0.0005, 0.001, 0.002, 0.003, b):
        strat = mid[0] + slope * (ts - ts[0])
        err = mid - strat
        tag = "best-fit" if slope == b else ""
        print(f"    slope={slope:.6f} {tag:>9}: mean_err={err.mean():+8.1f}  "
              f"abs_err_p50={np.percentile(np.abs(err),50):6.1f}  "
              f"abs_err_p90={np.percentile(np.abs(err),90):6.1f}  "
              f"abs_err_p99={np.percentile(np.abs(err),99):6.1f}")


def main() -> None:
    for day in (-2, -1, 0):
        aco_book_shape(day)
    for day in (-2, -1, 0):
        aco_trade_flow(day)
    for day in (-2, -1, 0):
        ipr_slope_vs_position(day)
    for day in (-2, -1, 0):
        ipr_slope_vs_best_fit(day)


if __name__ == "__main__":
    main()
