"""
Test 05 - Book imbalance as a predictor of short-term returns
==============================================================

Question: Does the L1 book imbalance
    imbalance = bid_vol_L1 / (bid_vol_L1 + ask_vol_L1)
predict the mid-price change at +5, +20, +100 ticks?

If yes, primo could add a conditional quote_bias_ticks that fires only
when the book is imbalanced in our favor - a short-term signal on top
of the longer-term drift.

Methodology:
- For each (day, product, timestamp):
    * Compute imbalance at that tick.
    * Compute mid-price change at each horizon (scaled to absolute ticks).
- Report:
    * Pearson correlation of imbalance vs future return per horizon.
    * R^2 (= correlation^2) per horizon.
    * Bucket-mean future return by imbalance decile (is the response monotonic?).

Interpretation:
    R^2 > 0.05 at any horizon  => signal is actionable, add conditional bias.
    R^2 < 0.01                => noise; skip.

Output: results/primo_exploration/test_05_book_imbalance.csv + console table.

Usage:
    python3 scripts/test_05_book_imbalance.py
"""

from __future__ import annotations

from pathlib import Path
import pandas as pd
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "ROUND_1_DATA" / "from imc package"
OUT_DIR = ROOT / "results" / "primo_exploration"
OUT_DIR.mkdir(parents=True, exist_ok=True)

HORIZONS = [5, 20, 100]
PRODUCTS = ["ASH_COATED_OSMIUM", "INTARIAN_PEPPER_ROOT"]
DAYS = [-2, -1, 0]


def _load(day: int, product: str) -> pd.DataFrame:
    prices = pd.read_csv(DATA_DIR / f"prices_round_1_day_{day}.csv", sep=";")
    df = prices[prices["product"] == product].copy()
    df = df.dropna(subset=["bid_price_1", "ask_price_1",
                           "bid_volume_1", "ask_volume_1"])
    df["imbalance"] = df["bid_volume_1"] / (df["bid_volume_1"] + df["ask_volume_1"])
    df = df.sort_values("timestamp").reset_index(drop=True)
    df["day"] = day
    df["product"] = product
    return df[["day", "product", "timestamp", "imbalance", "mid_price"]]


def analyze(df: pd.DataFrame) -> dict:
    """
    Returns nested dict: { horizon -> {pearson, r2, decile_table} }.
    `df` must be one contiguous day+product sequence of ticks.
    """
    results = {}
    for horizon in HORIZONS:
        future_mid = df["mid_price"].shift(-horizon)
        future_return = future_mid - df["mid_price"]

        aligned = pd.DataFrame({
            "imbalance": df["imbalance"],
            "future_return": future_return,
        }).dropna()

        if len(aligned) < 100:
            continue

        pearson = aligned["imbalance"].corr(aligned["future_return"])
        r2 = pearson ** 2 if pearson == pearson else float("nan")

        # Bucket by imbalance decile and compute mean future return per bucket
        aligned["bucket"] = pd.qcut(aligned["imbalance"], 10,
                                    labels=False, duplicates="drop")
        bucket_means = aligned.groupby("bucket")["future_return"].mean().round(3)

        results[horizon] = {
            "pearson":      round(pearson, 4),
            "r_squared":    round(r2, 4),
            "n_samples":    int(len(aligned)),
            "bucket_means": bucket_means.tolist(),
        }
    return results


def main():
    all_rows = []
    console_lines = []

    for product in PRODUCTS:
        console_lines.append(f"\n{'=' * 72}")
        console_lines.append(f"Product: {product}")
        console_lines.append("=" * 72)

        for day in DAYS:
            df = _load(day, product)
            res = analyze(df)

            for horizon, stats in res.items():
                all_rows.append({
                    "day":       day,
                    "product":   product,
                    "horizon":   horizon,
                    "pearson":   stats["pearson"],
                    "r_squared": stats["r_squared"],
                    "n_samples": stats["n_samples"],
                    "decile_0_mean": stats["bucket_means"][0] if stats["bucket_means"] else None,
                    "decile_9_mean": stats["bucket_means"][-1] if stats["bucket_means"] else None,
                })

            # Per-day compact table
            console_lines.append(f"\n  Day {day}:")
            console_lines.append("    horizon | Pearson r |   R^2   | decile 0->9 future-return trend")
            for horizon in HORIZONS:
                if horizon not in res:
                    continue
                s = res[horizon]
                d0 = s["bucket_means"][0] if s["bucket_means"] else float("nan")
                d9 = s["bucket_means"][-1] if s["bucket_means"] else float("nan")
                trend = f"{d0:+.2f} -> {d9:+.2f}"
                console_lines.append(
                    f"     {horizon:>6} | {s['pearson']:>+.4f} | {s['r_squared']:>.4f} | {trend}"
                )

    combined = pd.DataFrame(all_rows)
    out_path = OUT_DIR / "test_05_book_imbalance.csv"
    combined.to_csv(out_path, index=False)
    print(f"Saved {len(combined)} rows to {out_path}")

    for line in console_lines:
        print(line)

    # Final 3-day aggregated interpretation
    print("\n" + "=" * 72)
    print("3-day mean R^2 by (product, horizon):")
    print("=" * 72)
    pv = (combined.groupby(["product", "horizon"])["r_squared"]
                  .mean().round(4).unstack("horizon"))
    print(pv.to_string())
    print("\nR^2 > 0.05 anywhere => imbalance is an actionable short-term signal.")
    print("R^2 < 0.01 everywhere => imbalance is noise; no improvement from adding it.")


if __name__ == "__main__":
    main()
