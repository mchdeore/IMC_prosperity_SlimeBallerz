"""Shared Plotly configuration and sidebar helpers for all dashboard pages."""
import streamlit as st
import pandas as pd
from pathlib import Path

# ── Colors ────────────────────────────────────────────────────────────────────

BID_COLORS = ["#4fc3f7", "#0288d1", "#01579b"]  # L1 bright -> L3 dark
ASK_COLORS = ["#ef9a9a", "#d32f2f", "#7f0000"]   # L1 bright -> L3 dark
TRADE_COLOR = "#69f0ae"
WALL_MID_COLOR = "#ffa726"
RAW_MID_COLOR = "#bdbdbd"
CSV_MID_COLOR = "#ce93d8"

# ── Bloomberg-style chart config ──────────────────────────────────────────────

_TIGHT_MARGIN = dict(t=30, b=20, l=50, r=10)


def apply_crosshair(fig):
    """Apply dark theme, crosshair spikes, monospace hover, tight margins."""
    fig.update_layout(
        template="plotly_dark",
        hovermode="x unified",
        hoverlabel=dict(font=dict(family="monospace", size=11)),
        margin=_TIGHT_MARGIN,
        xaxis=dict(showgrid=True, gridcolor="#333"),
        yaxis=dict(showgrid=True, gridcolor="#333"),
    )
    fig.update_xaxes(
        showspikes=True, spikemode="across",
        spikethickness=1, spikecolor="#666", spikedash="dot",
    )
    fig.update_yaxes(
        showspikes=True, spikemode="across",
        spikethickness=1, spikecolor="#666", spikedash="dot",
    )
    return fig


# ── Dynamic CSV sidebar selector ─────────────────────────────────────────────

def _project_root():
    return Path(__file__).resolve().parent.parent


def sidebar_data_selector(need_trades=True):
    """
    Render sidebar controls for picking data files and product.

    Returns (prices_path, trades_path, product) where trades_path may be
    None if need_trades is False and no trades file is selected.
    """
    from data_loader import discover_csv_files

    root = _project_root()
    dirs = discover_csv_files(root)

    if not dirs:
        st.sidebar.error("No CSV data found in project.")
        st.stop()

    dir_names = sorted(dirs.keys())
    sel_dir = st.sidebar.selectbox("Data folder", dir_names, index=0)

    entry = dirs[sel_dir]
    price_files = sorted(entry["prices"])
    trade_files = sorted(entry["trades"])

    if not price_files:
        st.sidebar.error(f"No prices CSV in {sel_dir}")
        st.stop()

    sel_price = st.sidebar.selectbox("Prices file", price_files)
    prices_path = root / sel_dir / sel_price

    trades_path = None
    if need_trades:
        if not trade_files:
            st.sidebar.warning("No trades CSV found")
        else:
            sel_trade = st.sidebar.selectbox("Trades file", trade_files)
            trades_path = root / sel_dir / sel_trade

    # Detect products from the selected prices file
    _df = pd.read_csv(prices_path, sep=";", usecols=lambda c: c == "product", nrows=100000)
    products = sorted(_df["product"].unique().tolist()) if "product" in _df.columns else ["ALL"]
    product = st.sidebar.selectbox("Product", products)

    return prices_path, trades_path, product


# ── Page descriptions ─────────────────────────────────────────────────────────

PAGE_DESCRIPTIONS = {
    "01_Orderbook": (
        "Bid/ask price levels over time with trade markers. "
        "Use normalization to strip out drift and compare spread structure. "
        "Look for: consistent wall levels, spread widening/tightening, trades clustering at extremes."
    ),
    "02_Fair_Price": (
        "Compares Wall Mid, Raw Mid, and CSV Mid estimators. "
        "Wall Mid uses deepest liquidity and is more robust. "
        "Look for: divergence spikes where raw mid is distorted by aggressive bots."
    ),
    "03_Spread_Edge": (
        "Bid-ask spread and available edge over time. "
        "Wider spread = more room for market making. "
        "Look for: asymmetry between buy/sell edge, time periods with unusually wide spreads."
    ),
    "04_Product_Compare": (
        "Both products side by side for the same day. "
        "Look for: which product has tighter spreads, whether price movements correlate, "
        "structural differences in book depth."
    ),
    "05_Qty_Distribution": (
        "Histogram of trade sizes. Fixed-size spikes suggest specific bots. "
        "Look for: unusually common quantities (e.g., always 15 lots) -- these are bot signatures."
    ),
    "06_Informed_Detector": (
        "Trades plotted against the running daily min/max of wall mid. "
        "An informed trader buys exactly at the min and sells at the max. "
        "Look for: trades that cluster right on the envelope lines."
    ),
    "07_Trade_Edge": (
        "Each trade's edge (price minus fair value) grouped by quantity. "
        "Look for: quantities with consistently positive or negative edge -- those are directional bots."
    ),
    "08_Trade_Timing": (
        "When trades of each size occur during the day. "
        "Look for: quantities that cluster at specific times or appear at regular intervals -- "
        "signs of systematic bot behavior."
    ),
    "09_Autocorrelation": (
        "Tests whether returns are mean-reverting, trending, or random walk. "
        "Negative lag-1 AC = mean-reverting (trade against moves). "
        "Near zero = random walk (pure market making). Positive = momentum."
    ),
    "10_Backtest": (
        "Simulates a market-making strategy with configurable edge and position limits. "
        "Look for: stable PnL growth, low time-at-limit, reasonable drawdown. "
        "Use grid search to find robust parameter regions."
    ),
    "11_Fill_Rate": (
        "The key edge vs fill probability tradeoff. "
        "Tighter quotes = more fills but less edge per fill. "
        "The EV peak is the optimal aggressiveness."
    ),
    "12_Aggressiveness": (
        "Compares preset aggressiveness levels side by side. "
        "Look for: which setting has the best PnL with acceptable risk. "
        "Check cross-day stability to avoid overfitting."
    ),
    "13_Frequency": (
        "Spectral analysis of price returns. "
        "Look for: dominant periodicities that could be exploited, volatility regimes, "
        "and whether the return distribution has fat tails."
    ),
}


def show_description(page_key: str):
    """Show the description for a page as a caption below the title."""
    desc = PAGE_DESCRIPTIONS.get(page_key, "")
    if desc:
        st.caption(desc)
