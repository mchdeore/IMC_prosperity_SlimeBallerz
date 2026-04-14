"""
IMC Prosperity Data Loader
===========================

Shared utility module for loading and processing IMC Prosperity competition data.
All analysis notebooks in this toolkit import from this module.

Competition Context
-------------------
IMC Prosperity is a global algorithmic trading competition. Each round, teams
submit a Python trading algorithm that trades against bot participants in a
simulated market. This toolkit analyzes the historical orderbook and trade data
to find patterns, estimate fair prices, and design strategies.

Round 1 Products
----------------
- ASH_COATED_OSMIUM : Appears to be a fixed/slow-walk price product (~10,000).
                      Analogous to "Rainforest Resin" in Prosperity 3.
- INTARIAN_PEPPER_ROOT : A moving-price product (~10k on day -2, ~11k on day -1,
                         ~12k on day 0). Analogous to "Kelp" in Prosperity 3.

Data Files
----------
Located in ``ROUND_1_DATA/from imc package/``:

- ``prices_round_1_day_{day}.csv`` : Orderbook snapshots every 100 timestamps.
  Columns: day, timestamp, product, bid_price_1..3, bid_volume_1..3,
  ask_price_1..3, ask_volume_1..3, mid_price, profit_and_loss.
  Semicolon-separated. Up to 3 bid/ask levels (may be NaN if no liquidity).

- ``trades_round_1_day_{day}.csv`` : Executed trades.
  Columns: timestamp, buyer, seller, symbol, currency, price, quantity.
  Semicolon-separated. buyer/seller are empty (anonymous) in early rounds.

Available Days
--------------
Round 1 has days: -2, -1, 0

Key Concepts
------------
- **Wall Mid** : Average of the "wall" bid and "wall" ask prices. The wall is
  the price level with the deepest liquidity (largest volume) on each side.
  This is a more robust fair-price estimate than the raw mid-price, which can
  be distorted by aggressive overbidding or undercutting. Top teams in
  Prosperity 3 considered this "crucial for designing effective strategies."

- **Raw Mid** : Simple (best_bid + best_ask) / 2. Noisy but always available.

- **Edge** : Distance between a trade price and the estimated fair price.
  Positive edge = profitable trade. The core market-making tradeoff is
  edge vs fill probability: wider quotes = more edge but fewer fills.

Usage
-----
    >>> from data_loader import load_prices, load_trades, compute_wall_mid
    >>> prices = load_prices(day=-1, product="ASH_COATED_OSMIUM")
    >>> trades = load_trades(day=-1, product="ASH_COATED_OSMIUM")
    >>> prices = compute_wall_mid(prices)
    >>> print(prices[["timestamp", "wall_mid", "mid_price"]].head())

    >>> # Load all products for a day
    >>> all_prices = load_prices(day=-1)
    >>> all_trades = load_trades(day=-1)

    >>> # Load all days at once
    >>> mega_prices = load_all_prices()
    >>> mega_trades = load_all_trades()
"""

import os
import pandas as pd
import numpy as np
from pathlib import Path
from typing import Optional, List, Union

# ---------------------------------------------------------------------------
# Path configuration
# ---------------------------------------------------------------------------

# Resolve paths relative to this file so notebooks can import regardless of cwd
_THIS_DIR = Path(__file__).resolve().parent
_PROJECT_ROOT = _THIS_DIR.parent
_DATA_DIR = _PROJECT_ROOT / "ROUND_1_DATA" / "from imc package"

AVAILABLE_DAYS: List[int] = [-2, -1, 0]
"""Days available in the Round 1 dataset."""

PRODUCTS: List[str] = ["ASH_COATED_OSMIUM", "INTARIAN_PEPPER_ROOT"]
"""Products available in Round 1."""

# ---------------------------------------------------------------------------
# Column schema documentation
# ---------------------------------------------------------------------------

PRICE_COLUMNS = [
    "day", "timestamp", "product",
    "bid_price_1", "bid_volume_1",
    "bid_price_2", "bid_volume_2",
    "bid_price_3", "bid_volume_3",
    "ask_price_1", "ask_volume_1",
    "ask_price_2", "ask_volume_2",
    "ask_price_3", "ask_volume_3",
    "mid_price", "profit_and_loss",
]
"""Column names in the prices CSV files."""

TRADE_COLUMNS = [
    "timestamp", "buyer", "seller", "symbol", "currency", "price", "quantity",
]
"""Column names in the trades CSV files."""


# ---------------------------------------------------------------------------
# Loading functions
# ---------------------------------------------------------------------------

def load_prices(
    day: int,
    product: Optional[str] = None,
    data_dir: Optional[Path] = None,
) -> pd.DataFrame:
    """
    Load orderbook snapshot data for a given day.

    Parameters
    ----------
    day : int
        Which day to load. Must be one of: -2, -1, 0.
    product : str, optional
        Filter to a single product (e.g. "ASH_COATED_OSMIUM").
        If None, returns all products.
    data_dir : Path, optional
        Override the default data directory. Useful if your data lives
        somewhere other than ``ROUND_1_DATA/from imc package/``.

    Returns
    -------
    pd.DataFrame
        Orderbook snapshots with columns:
        - day, timestamp, product
        - bid_price_1..3, bid_volume_1..3 (NaN where no level exists)
        - ask_price_1..3, ask_volume_1..3 (NaN where no level exists)
        - mid_price, profit_and_loss

    Examples
    --------
    >>> prices = load_prices(day=-1, product="ASH_COATED_OSMIUM")
    >>> prices.shape
    (10001, 17)
    """
    if data_dir is None:
        data_dir = _DATA_DIR

    filepath = data_dir / f"prices_round_1_day_{day}.csv"
    if not filepath.exists():
        raise FileNotFoundError(
            f"Price file not found: {filepath}\n"
            f"Available days: {AVAILABLE_DAYS}"
        )

    df = pd.read_csv(filepath, sep=";")

    # Ensure numeric types for price/volume columns
    for col in df.columns:
        if "price" in col or "volume" in col or col in ("mid_price", "profit_and_loss"):
            df[col] = pd.to_numeric(df[col], errors="coerce")

    if product is not None:
        df = df[df["product"] == product].reset_index(drop=True)

    return df


def load_trades(
    day: int,
    product: Optional[str] = None,
    data_dir: Optional[Path] = None,
) -> pd.DataFrame:
    """
    Load trade data for a given day.

    Parameters
    ----------
    day : int
        Which day to load. Must be one of: -2, -1, 0.
    product : str, optional
        Filter to a single product. The trades CSV uses "symbol" as the
        column name; this function filters on that column.
        If None, returns all products.
    data_dir : Path, optional
        Override the default data directory.

    Returns
    -------
    pd.DataFrame
        Trade records with columns:
        - timestamp : int, the timestep (multiples of 100)
        - buyer : str, buyer ID (empty/NaN if anonymous)
        - seller : str, seller ID (empty/NaN if anonymous)
        - symbol : str, the product name
        - currency : str, always "XIRECS" in Round 1
        - price : float, trade execution price
        - quantity : int, number of lots traded

    Examples
    --------
    >>> trades = load_trades(day=-1, product="ASH_COATED_OSMIUM")
    >>> trades[["timestamp", "price", "quantity"]].head()
    """
    if data_dir is None:
        data_dir = _DATA_DIR

    filepath = data_dir / f"trades_round_1_day_{day}.csv"
    if not filepath.exists():
        raise FileNotFoundError(
            f"Trade file not found: {filepath}\n"
            f"Available days: {AVAILABLE_DAYS}"
        )

    df = pd.read_csv(filepath, sep=";")
    df["price"] = pd.to_numeric(df["price"], errors="coerce")
    df["quantity"] = pd.to_numeric(df["quantity"], errors="coerce")

    if product is not None:
        df = df[df["symbol"] == product].reset_index(drop=True)

    return df


def load_all_prices(product: Optional[str] = None) -> pd.DataFrame:
    """
    Load and concatenate price data across all available days.

    Parameters
    ----------
    product : str, optional
        Filter to a single product. If None, returns all products.

    Returns
    -------
    pd.DataFrame
        Combined orderbook data sorted by (day, timestamp, product).
    """
    frames = [load_prices(day=d, product=product) for d in AVAILABLE_DAYS]
    return pd.concat(frames, ignore_index=True).sort_values(
        ["day", "timestamp", "product"]
    ).reset_index(drop=True)


def load_all_trades(product: Optional[str] = None) -> pd.DataFrame:
    """
    Load and concatenate trade data across all available days.

    Adds a ``day`` column inferred from the source file.

    Parameters
    ----------
    product : str, optional
        Filter to a single product. If None, returns all products.

    Returns
    -------
    pd.DataFrame
        Combined trade data with an added ``day`` column.
    """
    frames = []
    for d in AVAILABLE_DAYS:
        df = load_trades(day=d, product=product)
        df["day"] = d
        frames.append(df)
    return pd.concat(frames, ignore_index=True).sort_values(
        ["day", "timestamp"]
    ).reset_index(drop=True)


# ---------------------------------------------------------------------------
# Fair price computation
# ---------------------------------------------------------------------------

def compute_wall_mid(prices_df: pd.DataFrame) -> pd.DataFrame:
    """
    Compute the Wall Mid fair-price estimate for each orderbook snapshot.

    The Wall Mid is the average of the "bid wall" and "ask wall" prices.
    The wall on each side is the price level with the **largest volume**
    (deepest liquidity). This is more robust than the raw mid-price because
    deep liquidity levels are typically placed by designated market makers
    who know the true price, while the best bid/ask can be distorted by
    aggressive overbidding or undercutting from other bots.

    Algorithm
    ---------
    For each row:
    1. Among bid_price_1/2/3, find the one with the largest bid_volume.
       That price is the "bid wall."
    2. Among ask_price_1/2/3, find the one with the largest ask_volume.
       That price is the "ask wall."
    3. Wall Mid = (bid_wall + ask_wall) / 2.
    4. If only one side has data, fall back to that side's wall price.
    5. If neither side has data, fall back to mid_price from the CSV.

    Parameters
    ----------
    prices_df : pd.DataFrame
        Orderbook data as returned by ``load_prices()``.

    Returns
    -------
    pd.DataFrame
        The input DataFrame with added columns:
        - ``bid_wall_price`` : price of the deepest bid level
        - ``bid_wall_volume`` : volume at the bid wall
        - ``ask_wall_price`` : price of the deepest ask level
        - ``ask_wall_volume`` : volume at the ask wall
        - ``wall_mid`` : (bid_wall_price + ask_wall_price) / 2
    """
    df = prices_df.copy()

    # Gather bid levels into arrays for vectorized comparison
    bid_prices = df[["bid_price_1", "bid_price_2", "bid_price_3"]].values
    bid_volumes = df[["bid_volume_1", "bid_volume_2", "bid_volume_3"]].values

    ask_prices = df[["ask_price_1", "ask_price_2", "ask_price_3"]].values
    ask_volumes = df[["ask_volume_1", "ask_volume_2", "ask_volume_3"]].values

    # For each row, find the index of the max-volume level
    # np.nanargmax raises on all-NaN rows, so handle that
    n = len(df)
    bid_wall_p = np.full(n, np.nan)
    bid_wall_v = np.full(n, np.nan)
    ask_wall_p = np.full(n, np.nan)
    ask_wall_v = np.full(n, np.nan)

    for i in range(n):
        bv = bid_volumes[i]
        if not np.all(np.isnan(bv)):
            idx = np.nanargmax(bv)
            bid_wall_p[i] = bid_prices[i, idx]
            bid_wall_v[i] = bv[idx]

        av = ask_volumes[i]
        if not np.all(np.isnan(av)):
            idx = np.nanargmax(av)
            ask_wall_p[i] = ask_prices[i, idx]
            ask_wall_v[i] = av[idx]

    df["bid_wall_price"] = bid_wall_p
    df["bid_wall_volume"] = bid_wall_v
    df["ask_wall_price"] = ask_wall_p
    df["ask_wall_volume"] = ask_wall_v

    # Wall Mid: average of bid wall and ask wall, with fallbacks
    both = ~np.isnan(bid_wall_p) & ~np.isnan(ask_wall_p)
    only_bid = ~np.isnan(bid_wall_p) & np.isnan(ask_wall_p)
    only_ask = np.isnan(bid_wall_p) & ~np.isnan(ask_wall_p)

    wall_mid = np.where(
        both, (bid_wall_p + ask_wall_p) / 2,
        np.where(only_bid, bid_wall_p,
                 np.where(only_ask, ask_wall_p, df["mid_price"].values))
    )
    df["wall_mid"] = wall_mid

    return df


def compute_raw_mid(prices_df: pd.DataFrame) -> pd.DataFrame:
    """
    Compute the raw mid-price from best bid and best ask.

    This is simply ``(bid_price_1 + ask_price_1) / 2``, which is the most
    basic fair-price estimate. It can be noisy when the best bid or ask is
    placed by an aggressive bot far from the true price.

    Parameters
    ----------
    prices_df : pd.DataFrame
        Orderbook data as returned by ``load_prices()``.

    Returns
    -------
    pd.DataFrame
        The input DataFrame with an added ``raw_mid`` column.
    """
    df = prices_df.copy()
    df["raw_mid"] = (df["bid_price_1"] + df["ask_price_1"]) / 2
    # If one side is missing, fall back to the available side or mid_price
    df["raw_mid"] = df["raw_mid"].fillna(df["mid_price"])
    return df


def compute_spread(prices_df: pd.DataFrame) -> pd.DataFrame:
    """
    Compute the bid-ask spread and edge metrics.

    Parameters
    ----------
    prices_df : pd.DataFrame
        Orderbook data, ideally already processed by ``compute_wall_mid()``.

    Returns
    -------
    pd.DataFrame
        The input DataFrame with added columns:
        - ``spread`` : ask_price_1 - bid_price_1 (the visible spread)
        - ``buy_edge`` : wall_mid - bid_price_1 (edge if you sell to best bid)
        - ``sell_edge`` : ask_price_1 - wall_mid (edge if you buy from best ask)
        If wall_mid is not yet computed, uses mid_price as fallback.
    """
    df = prices_df.copy()
    df["spread"] = df["ask_price_1"] - df["bid_price_1"]

    fair = df["wall_mid"] if "wall_mid" in df.columns else df["mid_price"]
    df["buy_edge"] = fair - df["bid_price_1"]
    df["sell_edge"] = df["ask_price_1"] - fair

    return df


# ---------------------------------------------------------------------------
# Convenience helpers
# ---------------------------------------------------------------------------

def get_products(prices_df: pd.DataFrame) -> List[str]:
    """Return the list of unique products in a prices DataFrame."""
    return sorted(prices_df["product"].unique().tolist())


def filter_time_range(
    df: pd.DataFrame,
    start: int = 0,
    end: int = 1_000_000,
) -> pd.DataFrame:
    """
    Filter a DataFrame to a specific timestamp range.

    Parameters
    ----------
    df : pd.DataFrame
        Must have a ``timestamp`` column.
    start : int
        Inclusive lower bound (default 0).
    end : int
        Inclusive upper bound (default 1,000,000).

    Returns
    -------
    pd.DataFrame
        Filtered subset.
    """
    return df[(df["timestamp"] >= start) & (df["timestamp"] <= end)].copy()


def merge_trades_with_prices(
    prices_df: pd.DataFrame,
    trades_df: pd.DataFrame,
) -> pd.DataFrame:
    """
    Merge trade data with the nearest orderbook snapshot.

    Each trade is matched to the most recent orderbook snapshot at or before
    its timestamp (for the same product). This lets you see the orderbook
    state at the time each trade occurred.

    Parameters
    ----------
    prices_df : pd.DataFrame
        Orderbook snapshots (should have wall_mid computed).
    trades_df : pd.DataFrame
        Trade records.

    Returns
    -------
    pd.DataFrame
        Trades with orderbook columns joined (suffixed with ``_book``
        where names collide).
    """
    # Standardize product column name
    trades = trades_df.copy()
    if "symbol" in trades.columns and "product" not in trades.columns:
        trades = trades.rename(columns={"symbol": "product"})

    merged = pd.merge_asof(
        trades.sort_values("timestamp"),
        prices_df.sort_values("timestamp"),
        on="timestamp",
        by="product",
        direction="backward",
        suffixes=("", "_book"),
    )
    return merged
