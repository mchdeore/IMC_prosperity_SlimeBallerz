"""Plotly figure builders for the strategy log visualizer."""

from __future__ import annotations

from typing import Iterable, Optional

import numpy as np
import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots

from .parser import LogBundle


POSITION_LIMITS: dict[str, int] = {
    "ASH_COATED_OSMIUM": 80,
    "INTARIAN_PEPPER_ROOT": 80,
}

COLOR_BID = "#26a69a"        # teal
COLOR_ASK = "#ef5350"        # red
COLOR_MID = "#ffb300"        # amber
COLOR_FAIR = "#42a5f5"       # blue
COLOR_QUOTE_BUY = "#66bb6a"  # green
COLOR_QUOTE_SELL = "#ec407a" # pink
COLOR_MKT_TRADE = "#90a4ae"  # grey
COLOR_OWN_BUY = "#00e676"    # bright green
COLOR_OWN_SELL = "#ff1744"   # bright red
COLOR_POS_POS = "#26a69a"
COLOR_POS_NEG = "#ef5350"


def _slice(df: pd.DataFrame, product: str, t0: float, t1: float) -> pd.DataFrame:
    if df is None or df.empty:
        return df
    mask = (df["product"] == product) & (df["timestamp"] >= t0) & (df["timestamp"] <= t1)
    return df.loc[mask]


def build_figure(
    bundle: LogBundle,
    product: str,
    time_range: tuple[float, float],
    *,
    show_quotes: bool = True,
    show_market_trades: bool = True,
    show_fair: bool = True,
    show_mid: bool = True,
) -> go.Figure:
    """Build the 2-row figure: price chart + position chart (shared x)."""
    t0, t1 = time_range

    book = _slice(bundle.book, product, t0, t1)
    trades = _slice(bundle.trades, product, t0, t1)
    quotes = _slice(bundle.quotes, product, t0, t1)
    position = _slice(bundle.position, product, t0, t1)
    fair = _slice(bundle.fair, product, t0, t1)

    fig = make_subplots(
        rows=2, cols=1,
        shared_xaxes=True,
        row_heights=[0.72, 0.28],
        vertical_spacing=0.03,
        subplot_titles=(f"{product} - order book & trades", "Position"),
    )

    # --- Top: order book -------------------------------------------------
    if not book.empty:
        fig.add_trace(
            go.Scatter(
                x=book["timestamp"], y=book["bid1"],
                mode="lines", name="best bid",
                line=dict(color=COLOR_BID, width=1.2, shape="hv"),
                hovertemplate="t=%{x}<br>bid=%{y}<extra></extra>",
            ),
            row=1, col=1,
        )
        fig.add_trace(
            go.Scatter(
                x=book["timestamp"], y=book["ask1"],
                mode="lines", name="best ask",
                line=dict(color=COLOR_ASK, width=1.2, shape="hv"),
                fill="tonexty", fillcolor="rgba(120,120,120,0.08)",
                hovertemplate="t=%{x}<br>ask=%{y}<extra></extra>",
            ),
            row=1, col=1,
        )
        if show_mid:
            fig.add_trace(
                go.Scatter(
                    x=book["timestamp"], y=book["mid"],
                    mode="lines", name="mid",
                    line=dict(color=COLOR_MID, width=1, dash="dot"),
                    hovertemplate="t=%{x}<br>mid=%{y}<extra></extra>",
                ),
                row=1, col=1,
            )

    # --- Fair value ------------------------------------------------------
    if show_fair and fair is not None and not fair.empty:
        fig.add_trace(
            go.Scatter(
                x=fair["timestamp"], y=fair["fair"],
                mode="lines", name="fair",
                line=dict(color=COLOR_FAIR, width=1.2, dash="dash"),
                hovertemplate="t=%{x}<br>fair=%{y}<extra></extra>",
            ),
            row=1, col=1,
        )

    # --- Market trades ---------------------------------------------------
    if show_market_trades and trades is not None and not trades.empty:
        m = trades[trades["source"] == "market"]
        if not m.empty:
            size = np.clip(m["quantity"].astype(float), 4, 18)
            fig.add_trace(
                go.Scatter(
                    x=m["timestamp"], y=m["price"],
                    mode="markers", name="market trade",
                    marker=dict(color=COLOR_MKT_TRADE, size=size, opacity=0.55,
                                line=dict(width=0)),
                    customdata=m[["quantity"]].values,
                    hovertemplate=("t=%{x}<br>px=%{y}<br>qty=%{customdata[0]}"
                                   "<extra>market</extra>"),
                ),
                row=1, col=1,
            )

    # --- Quotes we posted ------------------------------------------------
    if show_quotes and quotes is not None and not quotes.empty:
        qb = quotes[quotes["side"] == "buy"]
        qs = quotes[quotes["side"] == "sell"]
        if not qb.empty:
            size = np.clip(qb["quantity"].abs().astype(float) * 0.6, 4, 18)
            fig.add_trace(
                go.Scatter(
                    x=qb["timestamp"], y=qb["price"],
                    mode="markers", name="quote buy",
                    marker=dict(symbol="triangle-up-open", color=COLOR_QUOTE_BUY,
                                size=size, line=dict(width=1.3, color=COLOR_QUOTE_BUY)),
                    customdata=qb[["quantity"]].values,
                    hovertemplate=("t=%{x}<br>px=%{y}<br>qty=%{customdata[0]}"
                                   "<extra>quote buy</extra>"),
                ),
                row=1, col=1,
            )
        if not qs.empty:
            size = np.clip(qs["quantity"].abs().astype(float) * 0.6, 4, 18)
            fig.add_trace(
                go.Scatter(
                    x=qs["timestamp"], y=qs["price"],
                    mode="markers", name="quote sell",
                    marker=dict(symbol="triangle-down-open", color=COLOR_QUOTE_SELL,
                                size=size, line=dict(width=1.3, color=COLOR_QUOTE_SELL)),
                    customdata=qs[["quantity"]].values,
                    hovertemplate=("t=%{x}<br>px=%{y}<br>qty=%{customdata[0]}"
                                   "<extra>quote sell</extra>"),
                ),
                row=1, col=1,
            )

    # --- Our fills -------------------------------------------------------
    if trades is not None and not trades.empty:
        ob = trades[trades["source"] == "own_buy"]
        os_ = trades[trades["source"] == "own_sell"]
        if not ob.empty:
            size = np.clip(ob["quantity"].astype(float), 6, 22)
            fig.add_trace(
                go.Scatter(
                    x=ob["timestamp"], y=ob["price"],
                    mode="markers", name="own buy",
                    marker=dict(symbol="triangle-up", color=COLOR_OWN_BUY,
                                size=size, line=dict(color="#003d24", width=1)),
                    customdata=ob[["quantity"]].values,
                    hovertemplate=("t=%{x}<br>px=%{y}<br>qty=%{customdata[0]}"
                                   "<extra>own buy</extra>"),
                ),
                row=1, col=1,
            )
        if not os_.empty:
            size = np.clip(os_["quantity"].astype(float), 6, 22)
            fig.add_trace(
                go.Scatter(
                    x=os_["timestamp"], y=os_["price"],
                    mode="markers", name="own sell",
                    marker=dict(symbol="triangle-down", color=COLOR_OWN_SELL,
                                size=size, line=dict(color="#4a0010", width=1)),
                    customdata=os_[["quantity"]].values,
                    hovertemplate=("t=%{x}<br>px=%{y}<br>qty=%{customdata[0]}"
                                   "<extra>own sell</extra>"),
                ),
                row=1, col=1,
            )

    # --- Bottom: position -----------------------------------------------
    pos_series = _position_timeline(position, book, product)
    if not pos_series.empty:
        fig.add_trace(
            go.Scatter(
                x=pos_series["timestamp"], y=pos_series["position"],
                mode="lines", name="position",
                line=dict(color="#ffffff", width=1.2, shape="hv"),
                fill="tozeroy",
                fillcolor="rgba(38,166,154,0.25)",
                hovertemplate="t=%{x}<br>pos=%{y}<extra></extra>",
            ),
            row=2, col=1,
        )

    limit = POSITION_LIMITS.get(product)
    if limit is not None:
        for y in (limit, -limit):
            fig.add_hline(
                y=y, line=dict(color="#ffab40", width=1, dash="dot"),
                row=2, col=1,
            )
        fig.add_hline(y=0, line=dict(color="rgba(255,255,255,0.25)", width=1),
                      row=2, col=1)

    # --- Layout ----------------------------------------------------------
    fig.update_layout(
        template="plotly_dark",
        paper_bgcolor="#121212",
        plot_bgcolor="#121212",
        margin=dict(l=50, r=20, t=40, b=30),
        legend=dict(
            orientation="h", yanchor="bottom", y=1.02,
            xanchor="left", x=0, bgcolor="rgba(0,0,0,0)",
        ),
        hovermode="x unified",
        uirevision=product,  # keep zoom when toggles change
    )
    fig.update_xaxes(
        showgrid=True, gridcolor="rgba(255,255,255,0.05)",
        zeroline=False, title_text="timestamp", row=2, col=1,
    )
    fig.update_xaxes(showgrid=True, gridcolor="rgba(255,255,255,0.05)",
                     zeroline=False, row=1, col=1)
    fig.update_yaxes(showgrid=True, gridcolor="rgba(255,255,255,0.05)",
                     zeroline=False, title_text="price", row=1, col=1)
    fig.update_yaxes(showgrid=True, gridcolor="rgba(255,255,255,0.05)",
                     zeroline=False, title_text="position", row=2, col=1)
    return fig


def _position_timeline(position: pd.DataFrame,
                       book: pd.DataFrame,
                       product: str) -> pd.DataFrame:
    """Return a position step series that spans the full visible window.

    ``position`` only has rows at fill times, so we forward-fill onto the
    book timestamps (which exist for every tick) to get a smooth step plot
    that starts at 0 from the beginning of the day.
    """
    if book is None or book.empty:
        return position
    grid = book[["timestamp"]].drop_duplicates().sort_values("timestamp")
    if position is None or position.empty:
        grid = grid.assign(position=0, product=product)
        return grid[["timestamp", "product", "position"]]
    merged = pd.merge_asof(
        grid.sort_values("timestamp"),
        position.sort_values("timestamp")[["timestamp", "position"]],
        on="timestamp",
        direction="backward",
    )
    merged["position"] = merged["position"].fillna(0)
    merged["product"] = product
    return merged[["timestamp", "product", "position"]]


def compute_kpis(bundle: LogBundle,
                 product: str,
                 time_range: tuple[float, float]) -> dict:
    """Return summary statistics for the visible window."""
    t0, t1 = time_range
    book = _slice(bundle.book, product, t0, t1)
    trades = _slice(bundle.trades, product, t0, t1)
    quotes = _slice(bundle.quotes, product, t0, t1)

    pnl_start = pnl_end = None
    if book is not None and not book.empty and "pnl" in book.columns:
        pnl_start = float(book["pnl"].iloc[0])
        pnl_end = float(book["pnl"].iloc[-1])
    pnl_delta = (pnl_end - pnl_start) if (pnl_start is not None and pnl_end is not None) else None

    fills = trades[trades["source"].isin(("own_buy", "own_sell"))] if trades is not None else pd.DataFrame()
    n_fills = int(len(fills))
    own_volume = int(fills["quantity"].sum()) if not fills.empty else 0

    n_quotes = int(len(quotes)) if quotes is not None else 0
    n_mkt_trades = int((trades["source"] == "market").sum()) if trades is not None and not trades.empty else 0

    position_series = _position_timeline(_slice(bundle.position, product, t0, t1), book, product)
    current_pos = int(position_series["position"].iloc[-1]) if not position_series.empty else 0

    return {
        "pnl": pnl_end,
        "pnl_delta": pnl_delta,
        "current_position": current_pos,
        "fills": n_fills,
        "own_volume": own_volume,
        "quotes": n_quotes,
        "market_trades": n_mkt_trades,
    }
