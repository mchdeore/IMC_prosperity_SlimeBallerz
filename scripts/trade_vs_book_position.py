"""
For each historical trade, locate its print price relative to the book at the
same timestamp. This tells us how often Phase B can fill a bid at each offset.
"""

from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
BOOKS = ROOT / "ROUND_1_DATA" / "from imc package"


def analyze(day: int, symbol: str) -> None:
    prices = pd.read_csv(BOOKS / f"prices_round_1_day_{day}.csv", sep=";")
    trades = pd.read_csv(BOOKS / f"trades_round_1_day_{day}.csv", sep=";")

    p = prices[prices["product"] == symbol][
        ["timestamp", "bid_price_1", "ask_price_1"]
    ].set_index("timestamp")
    t = trades[trades["symbol"] == symbol].copy()
    t = t.join(p, on="timestamp", how="inner")
    t = t.dropna(subset=["bid_price_1", "ask_price_1"])

    # Classify each trade by position vs L1 bid (buy-side analysis)
    # A historical trade at price P matters for our bid at price X this way:
    #   - mode=all:  fill if P <= X
    #   - mode=worse: fill if P <  X
    # So for a bid at best_bid (join):  fills in `all` if P == best_bid, in `worse` if P < best_bid.
    # For a bid at best_bid+1 (improve_1): fills in both if P <= best_bid.
    t["at_bid1"]  = t["price"] == t["bid_price_1"]
    t["below_b1"] = t["price"] <  t["bid_price_1"]
    t["at_ask1"]  = t["price"] == t["ask_price_1"]
    t["above_a1"] = t["price"] >  t["ask_price_1"]
    t["inside"]   = (t["price"] > t["bid_price_1"]) & (t["price"] < t["ask_price_1"])

    print(f"\nday {day}  {symbol}")
    print(f"  total trades matched to book : {len(t)}")
    print(f"  total volume                 : {t['quantity'].sum()}")
    print(f"  trades AT bid_1 (join fills only in 'all' mode): "
          f"{t['at_bid1'].sum()} ({t['at_bid1'].mean():.1%})  "
          f"vol={t.loc[t['at_bid1'],'quantity'].sum()}")
    print(f"  trades STRICTLY BELOW bid_1 (join fills in 'worse' too): "
          f"{t['below_b1'].sum()} ({t['below_b1'].mean():.1%})  "
          f"vol={t.loc[t['below_b1'],'quantity'].sum()}")
    print(f"  trades INSIDE spread (improve_N catches these): "
          f"{t['inside'].sum()} ({t['inside'].mean():.1%})  "
          f"vol={t.loc[t['inside'],'quantity'].sum()}")
    print(f"  trades AT ask_1 (improve catches, improve_1 too): "
          f"{t['at_ask1'].sum()} ({t['at_ask1'].mean():.1%})  "
          f"vol={t.loc[t['at_ask1'],'quantity'].sum()}")
    print(f"  trades STRICTLY ABOVE ask_1  : "
          f"{t['above_a1'].sum()} ({t['above_a1'].mean():.1%})  "
          f"vol={t.loc[t['above_a1'],'quantity'].sum()}")

    # Fill-rate projection per offset (buy side only for brevity)
    # "bid at best_bid + N": fills on any trade with price <= best_bid + N (all)
    # or price <  best_bid + N (worse).
    print("  buy-side passive-fill volume by offset (bid = best_bid + N):")
    print("    offset  all-mode-vol   worse-mode-vol")
    for N in [-1, 0, 1, 2, 3, 4, 5]:
        # all: sum trade volumes where price <= best_bid + N
        mask_all = t["price"] <= t["bid_price_1"] + N
        mask_worse = t["price"] < t["bid_price_1"] + N
        v_all = t.loc[mask_all, "quantity"].sum()
        v_worse = t.loc[mask_worse, "quantity"].sum()
        print(f"    {N:+d}       {v_all:>8}       {v_worse:>8}")


if __name__ == "__main__":
    for day in (-2, -1, 0):
        analyze(day, "ASH_COATED_OSMIUM")
