"""
Test 04 - Adverse selection analysis
=====================================

Question: When primo's maker bid (best_bid+1) or ask (best_ask-1) would
have been filled by a historical trade, does the mid-price drift AGAINST
us over the next N ticks? If yes, we're being picked off by informed
flow and should consider backing off our maker quotes.

Methodology:
- For each historical market_trade at time t:
    * Get the book at time t (best_bid, best_ask).
    * Construct our hypothetical bid at best_bid+1 and ask at best_ask-1.
    * If the trade price is <= our bid (buy fill for us): record a bid fill.
    * If the trade price is >= our ask (sell fill for us): record an ask fill.
- For each simulated fill, measure the mid-price change at t+5, t+20,
  t+100, t+500 ticks.
- Report mean/median/p25/p75 of mid-delta by (product, side, horizon).

Interpretation:
    mean_mid_delta < -1 for bid fills at horizon=20 => bid is toxic (picked off)
    mean_mid_delta >  0 for bid fills at horizon=20 => bid is clean (profitable)

Output: results/primo_exploration/test_04_adverse_selection.csv + stdout table.

Usage:
    python3 scripts/test_04_adverse_selection.py
"""

from __future__ import annotations

from pathlib import Path
import pandas as pd
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "ROUND_1_DATA" / "from imc package"
OUT_DIR = ROOT / "results" / "primo_exploration"
OUT_DIR.mkdir(parents=True, exist_ok=True)

HORIZONS = [5, 20, 100, 500]
PRODUCTS = ["ASH_COATED_OSMIUM", "INTARIAN_PEPPER_ROOT"]
DAYS = [-2, -1, 0]
BEAT_TICKS = 1   # primo_v3 default


def _load_day(day: int):
    prices = pd.read_csv(DATA_DIR / f"prices_round_1_day_{day}.csv", sep=";")
    trades = pd.read_csv(DATA_DIR / f"trades_round_1_day_{day}.csv", sep=";")
    return prices, trades


def _build_book_lookup(prices: pd.DataFrame, product: str):
    """Return a df indexed by timestamp with best_bid, best_ask, mid."""
    df = prices[prices["product"] == product].copy()
    df = df.dropna(subset=["bid_price_1", "ask_price_1"])
    df = df[["timestamp", "bid_price_1", "ask_price_1", "mid_price"]]
    df = df.set_index("timestamp")
    df = df[~df.index.duplicated(keep="first")]
    return df


def analyze_product(product: str) -> pd.DataFrame:
    """Returns one row per (day, side, horizon) with adverse-selection stats."""
    rows = []
    for day in DAYS:
        prices, trades = _load_day(day)
        book = _build_book_lookup(prices, product)
        product_trades = trades[trades["symbol"] == product].copy()

        # Attach book snapshot at trade time
        product_trades = product_trades.join(book, on="timestamp", how="inner")
        if len(product_trades) == 0:
            continue

        # Our hypothetical maker quotes
        product_trades["our_bid"] = product_trades["bid_price_1"] + BEAT_TICKS
        product_trades["our_ask"] = product_trades["ask_price_1"] - BEAT_TICKS

        # Note: we record a bid fill whenever trade_price <= our_bid.
        # This matches the backtester's "all"-mode fill rule.
        bid_fills = product_trades[product_trades["price"] <= product_trades["our_bid"]].copy()
        ask_fills = product_trades[product_trades["price"] >= product_trades["our_ask"]].copy()

        # Compute mid at t+h for each horizon; vectorize via pandas.
        for horizon in HORIZONS:
            # We want: given timestamp T (fill time), look up mid at (T + h*100).
            # Build a Series where index T maps to mid(T + h*100) by shifting
            # the book's index LEFT by h*100 so lookups at T land on that
            # future timestamp's mid.
            future_mid = pd.Series(
                book["mid_price"].values,
                index=book.index - horizon * 100,
            )
            future_mid = future_mid[~future_mid.index.duplicated(keep="first")]

            # BID side: our_bid fills at price P; the "fill price" for
            # us is our_bid (we would have paid that). Mid-delta =
            # mid(t+h) - our_bid. Positive = profit (price went up
            # after we bought).
            bid_fills[f"future_mid_{horizon}"] = bid_fills["timestamp"].map(future_mid)
            bid_fills[f"delta_{horizon}"] = bid_fills[f"future_mid_{horizon}"] - bid_fills["our_bid"]

            # ASK side: we sold at our_ask; profit = our_ask - mid(t+h).
            ask_fills[f"future_mid_{horizon}"] = ask_fills["timestamp"].map(future_mid)
            ask_fills[f"delta_{horizon}"] = ask_fills["our_ask"] - ask_fills[f"future_mid_{horizon}"]

        # Build output rows
        for side_name, fills in [("bid", bid_fills), ("ask", ask_fills)]:
            for horizon in HORIZONS:
                col = f"delta_{horizon}"
                series = fills[col].dropna()
                if len(series) == 0:
                    continue
                rows.append({
                    "day":              day,
                    "product":          product,
                    "side":             side_name,
                    "horizon_ticks":    horizon,
                    "n_fills":          int(len(series)),
                    "mean_delta":       round(series.mean(), 3),
                    "median":           round(series.median(), 3),
                    "p25":              round(series.quantile(0.25), 3),
                    "p75":              round(series.quantile(0.75), 3),
                    "std":              round(series.std(), 3),
                })
    return pd.DataFrame(rows)


def main():
    all_rows = []
    for product in PRODUCTS:
        df = analyze_product(product)
        all_rows.append(df)
    combined = pd.concat(all_rows, ignore_index=True)

    out_path = OUT_DIR / "test_04_adverse_selection.csv"
    combined.to_csv(out_path, index=False)
    print(f"Saved {len(combined)} rows to {out_path}\n")

    # Summary: mean across 3 days for each (product, side, horizon)
    summary = (combined
               .groupby(["product", "side", "horizon_ticks"])
               .agg(n_fills=("n_fills", "sum"),
                    mean_delta=("mean_delta", "mean"),
                    median=("median", "mean"))
               .round(3)
               .reset_index())

    print("=" * 72)
    print("Mean mid-delta at each horizon (averaged across 3 days)")
    print("Positive = our fills were profitable. Negative = picked off.")
    print("=" * 72)
    for product in PRODUCTS:
        sub = summary[summary["product"] == product]
        if len(sub) == 0:
            continue
        pv = sub.pivot_table(
            index="side", columns="horizon_ticks",
            values="mean_delta"
        ).round(2)
        print(f"\n{product}:")
        print(pv.to_string())

    print("\n" + "=" * 72)
    print("Interpretation:")
    print("  bid side delta > 0 at h=20 => bid fills are profitable, clean flow")
    print("  bid side delta < -1 at h=20 => bid is toxic, consider make_beat_ticks=0")
    print("=" * 72)


if __name__ == "__main__":
    main()
