"""
osmium_edge_test_1
==================
Market-making strategy test for ASH_COATED_OSMIUM with edge=7.

Rationale: ASH_COATED_OSMIUM has a fixed/slow-walk price around 10,000.
The fill rate vs edge analysis shows edge=7 sits at a good balance point --
enough fills to stay active, enough edge per fill to be consistently
profitable. The product has wide enough spreads (~16 avg) to support this.

Strategy:
  - Fair price: Wall Mid (deepest-liquidity bid/ask average)
  - Bid at wall_mid - 7, ask at wall_mid + 7
  - Take any existing orders that cross our edge threshold
  - Flatten inventory when |position| hits the flatten threshold

Usage:
  cd /Users/therealmc/IMC_prosperity_SlimeBallerz
  .venv/bin/python strageties/osmium_edge_test_1.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "analysis"))

import numpy as np
import pandas as pd
from data_loader import load_prices, load_trades, compute_wall_mid, AVAILABLE_DAYS

# ── Strategy parameters ───────────────────────────────────────────────────────

PRODUCT = "ASH_COATED_OSMIUM"
EDGE = 7
MAX_POSITION = 50
FLATTEN_THRESHOLD = 40

# ── Backtester ────────────────────────────────────────────────────────────────


def backtest(prices_df, trades_df, edge, max_pos, flatten_thresh):
    wm = prices_df["wall_mid"].values
    best_bid = prices_df["bid_price_1"].values
    best_ask = prices_df["ask_price_1"].values
    timestamps = prices_df["timestamp"].values

    position = 0
    cash = 0.0
    n_trades = 0
    pnl_series = []
    pos_series = []
    flatten_events = []
    time_at_limit = 0
    trade_log = []

    for i in range(len(wm)):
        fair = wm[i]
        if np.isnan(fair):
            pnl_series.append(cash)
            pos_series.append(position)
            continue

        if abs(position) >= max_pos:
            time_at_limit += 1

        # Take profitable asks (buy below fair - edge)
        if not np.isnan(best_ask[i]) and best_ask[i] <= fair - edge and position < max_pos:
            qty = min(1, max_pos - position)
            cash -= best_ask[i] * qty
            position += qty
            n_trades += 1
            trade_log.append({"t": timestamps[i], "side": "BUY", "price": best_ask[i], "qty": qty, "type": "take", "edge": fair - best_ask[i]})

        # Take profitable bids (sell above fair + edge)
        if not np.isnan(best_bid[i]) and best_bid[i] >= fair + edge and position > -max_pos:
            qty = min(1, max_pos + position)
            cash += best_bid[i] * qty
            position -= qty
            n_trades += 1
            trade_log.append({"t": timestamps[i], "side": "SELL", "price": best_bid[i], "qty": qty, "type": "take", "edge": best_bid[i] - fair})

        # Passive fills from historical trades
        ts_trades = trades_df[trades_df["timestamp"] == timestamps[i]]
        for _, trade in ts_trades.iterrows():
            if trade["price"] >= fair + edge and position > -max_pos:
                qty = min(int(trade["quantity"]), max_pos + position)
                if qty > 0:
                    fill_price = fair + edge
                    cash += fill_price * qty
                    position -= qty
                    n_trades += 1
                    trade_log.append({"t": timestamps[i], "side": "SELL", "price": fill_price, "qty": qty, "type": "passive", "edge": edge})
            elif trade["price"] <= fair - edge and position < max_pos:
                qty = min(int(trade["quantity"]), max_pos - position)
                if qty > 0:
                    fill_price = fair - edge
                    cash -= fill_price * qty
                    position += qty
                    n_trades += 1
                    trade_log.append({"t": timestamps[i], "side": "BUY", "price": fill_price, "qty": qty, "type": "passive", "edge": edge})

        # Flatten if over threshold
        if abs(position) >= flatten_thresh:
            cash += position * fair
            flatten_events.append(timestamps[i])
            trade_log.append({"t": timestamps[i], "side": "FLAT", "price": fair, "qty": abs(position), "type": "flatten", "edge": 0})
            position = 0

        pnl_series.append(cash + position * fair)
        pos_series.append(position)

    last_fair = wm[~np.isnan(wm)][-1] if any(~np.isnan(wm)) else 0
    total_pnl = cash + position * last_fair

    pnl_arr = np.array(pnl_series)
    max_drawdown = (pnl_arr - np.maximum.accumulate(pnl_arr)).min()

    return {
        "total_pnl": total_pnl,
        "n_trades": n_trades,
        "max_drawdown": max_drawdown,
        "flatten_count": len(flatten_events),
        "time_at_limit": time_at_limit,
        "time_at_limit_pct": time_at_limit / len(timestamps) * 100,
        "pnl_series": pnl_arr,
        "pos_series": np.array(pos_series),
        "timestamps": timestamps,
        "trade_log": pd.DataFrame(trade_log),
        "final_position": position,
    }


# ── Run across all days ───────────────────────────────────────────────────────


def main():
    print("=" * 70)
    print(f"  osmium_edge_test_1")
    print(f"  {PRODUCT} | edge={EDGE} | max_pos={MAX_POSITION} | flatten={FLATTEN_THRESHOLD}")
    print("=" * 70)

    all_results = []

    for day in AVAILABLE_DAYS:
        prices = compute_wall_mid(load_prices(day=day, product=PRODUCT))
        trades = load_trades(day=day, product=PRODUCT)
        r = backtest(prices, trades, EDGE, MAX_POSITION, FLATTEN_THRESHOLD)
        all_results.append(r)

        tl = r["trade_log"]
        passive = tl[tl["type"] == "passive"]
        takes = tl[tl["type"] == "take"]

        print(f"\n--- Day {day} ---")
        print(f"  Total PnL:      {r['total_pnl']:>10.0f}")
        print(f"  Trades:         {r['n_trades']:>10d}  (passive={len(passive)}, take={len(takes)}, flatten={r['flatten_count']})")
        print(f"  Max Drawdown:   {r['max_drawdown']:>10.0f}")
        print(f"  Time @ Limit:   {r['time_at_limit_pct']:>9.1f}%")
        print(f"  Final Position: {r['final_position']:>10d}")
        if len(passive) > 0:
            print(f"  Avg Passive Edge: {passive['edge'].mean():>7.2f}")
            print(f"  Passive Buys:     {len(passive[passive['side'] == 'BUY']):>5d}  |  Passive Sells: {len(passive[passive['side'] == 'SELL']):>5d}")

    # Cross-day summary
    pnls = [r["total_pnl"] for r in all_results]
    print(f"\n{'=' * 70}")
    print(f"  CROSS-DAY SUMMARY")
    print(f"  Avg PnL:  {np.mean(pnls):>10.0f}")
    print(f"  Std PnL:  {np.std(pnls):>10.0f}")
    print(f"  Min PnL:  {np.min(pnls):>10.0f}")
    print(f"  Max PnL:  {np.max(pnls):>10.0f}")
    print(f"  Total:    {np.sum(pnls):>10.0f}  (across {len(AVAILABLE_DAYS)} days)")
    print(f"{'=' * 70}")


if __name__ == "__main__":
    main()
