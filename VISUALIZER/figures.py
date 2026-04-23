"""Plotly figure builders for the strategy log visualizer."""

from __future__ import annotations

from typing import Iterable, Optional

import numpy as np
import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots

from .parser import DAY_TICKS, LogBundle


POSITION_LIMITS: dict[str, int] = {
    "ASH_COATED_OSMIUM": 80,
    "INTARIAN_PEPPER_ROOT": 80,
}

COLOR_BID = "#26a69a"        # teal
COLOR_ASK = "#ef5350"        # red
COLOR_MID = "#ffb300"        # amber      (market reference)
COLOR_FAIR = "#4fc3f7"       # bright blue (strategy anchor)
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


def _align_ref(series_df: pd.DataFrame,
               ts_col: str,
               ref_df: pd.DataFrame) -> pd.Series:
    """Return the ``ref`` column of ``ref_df`` aligned to ``series_df[ts_col]``
    via backward merge_asof, preserving the original row order of ``series_df``.

    ``ref_df`` must be sorted by ``timestamp`` and expose a ``ref`` column.
    Rows that land before the first ref tick come back as NaN.
    """
    if series_df is None or series_df.empty or ref_df.empty:
        n = 0 if series_df is None else len(series_df)
        return pd.Series([float("nan")] * n, dtype=float)
    left = series_df[[ts_col]].copy()
    left["_orig_order"] = np.arange(len(left))
    left = left.sort_values(ts_col)
    merged = pd.merge_asof(
        left,
        ref_df[["timestamp", "ref"]],
        left_on=ts_col, right_on="timestamp",
        direction="backward",
    )
    merged = merged.sort_values("_orig_order")
    return merged["ref"].reset_index(drop=True)


def _shift_by_ref(series_df: pd.DataFrame,
                  ts_col: str,
                  y_col: str,
                  ref_df: pd.DataFrame) -> pd.Series:
    """Return ``series_df[y_col] - ref(ts)`` aligned via backward merge_asof."""
    if series_df is None or series_df.empty or ref_df.empty:
        return series_df[y_col] if series_df is not None else pd.Series(dtype=float)
    ref = _align_ref(series_df, ts_col, ref_df)
    y = series_df[y_col].reset_index(drop=True).astype(float)
    return (y - ref.fillna(0)).astype(float)


def build_figure(
    bundle: LogBundle,
    product: str,
    time_range: tuple[float, float],
    *,
    show_quotes: bool = True,
    show_market_trades: bool = True,
    show_fair: bool = True,
    show_mid: bool = True,
    show_levels: bool = False,
    normalize_mode: str = "none",
) -> go.Figure:
    """Build the 2-row figure: price chart + position chart (shared x).

    ``show_levels`` overlays every visible quote level (L1 is always on,
    L2/L3 are added as semi-transparent step lines + markers on the
    timestamps where the book actually published them).
    ``normalize_mode`` controls the y-axis reference:
        - ``"none"``: raw prices
        - ``"mid"``:  subtract the robust mid at each tick (collapses the
          book around 0, so spread + quote structure reads cleanly)
        - ``"fair"``: subtract the strategy's fair value at each tick
          (turns the chart into a pure edge view - bid/ask/quote/fill
          all become ``px - fair``)
    """
    t0, t1 = time_range

    book = _slice(bundle.book, product, t0, t1)
    trades = _slice(bundle.trades, product, t0, t1)
    quotes = _slice(bundle.quotes, product, t0, t1)
    position = _slice(bundle.position, product, t0, t1)
    fair = _slice(bundle.fair, product, t0, t1)

    # Pick the reference series used for normalization. If the user asks
    # for a mode we can't honor (e.g. ``fair`` but this log has no fair
    # samples) we silently degrade to raw prices rather than rendering
    # a chart full of NaNs.
    mode = normalize_mode or "none"
    ref_df = pd.DataFrame(columns=["timestamp", "ref"])
    if mode == "mid" and not book.empty and "mid" in book.columns:
        ref_df = (book[["timestamp", "mid"]].dropna()
                  .rename(columns={"mid": "ref"})
                  .sort_values("timestamp"))
    elif mode == "fair" and fair is not None and not fair.empty:
        ref_df = (fair[["timestamp", "fair"]].dropna()
                  .rename(columns={"fair": "ref"})
                  .sort_values("timestamp"))
    normalize_active = not ref_df.empty
    if not normalize_active:
        mode = "none"

    # Pre-align the ref onto each book row once; every y_book() call
    # reuses it instead of re-doing a merge_asof.
    if normalize_active and not book.empty:
        ref_on_book = _align_ref(book, "timestamp", ref_df)
        ref_on_book_np = ref_on_book.fillna(0).to_numpy()
    else:
        ref_on_book_np = None

    def y_book(col: str) -> pd.Series:
        if normalize_active and ref_on_book_np is not None:
            return (book[col].to_numpy(dtype=float) - ref_on_book_np)
        return book[col]

    def y_series(df: pd.DataFrame, y_col: str, ts_col: str = "timestamp") -> pd.Series:
        if normalize_active:
            return _shift_by_ref(df, ts_col, y_col, ref_df)
        return df[y_col]

    if mode == "mid":
        price_label = "price - mid"
        title_suffix = " (mid-normalized)"
    elif mode == "fair":
        price_label = "price - fair"
        title_suffix = " (fair-normalized)"
    else:
        price_label = "price"
        title_suffix = ""

    fig = make_subplots(
        rows=2, cols=1,
        shared_xaxes=True,
        row_heights=[0.72, 0.28],
        vertical_spacing=0.03,
        subplot_titles=(f"{product} - order book & trades{title_suffix}", "Position"),
    )

    # --- Top: order book -------------------------------------------------
    # Use Scattergl everywhere: WebGL renders 30k-point traces at 60fps
    # on GPU, which is what makes native plotly pan/scroll-zoom feel
    # instant even across the whole multi-day backtest.
    if not book.empty:
        fig.add_trace(
            go.Scattergl(
                x=book["timestamp"], y=y_book("bid1"),
                mode="lines", name="best bid",
                legendgroup="book", line=dict(color=COLOR_BID, width=1.2, shape="hv"),
                hovertemplate="t=%{x}<br>bid=%{y}<extra></extra>",
            ),
            row=1, col=1,
        )
        # NOTE: no fill="tonexty" here -- the shaded spread band was the
        # single biggest visual-noise offender. The bid/ask lines alone
        # already convey the spread.
        fig.add_trace(
            go.Scattergl(
                x=book["timestamp"], y=y_book("ask1"),
                mode="lines", name="best ask",
                legendgroup="book", line=dict(color=COLOR_ASK, width=1.2, shape="hv"),
                hovertemplate="t=%{x}<br>ask=%{y}<extra></extra>",
            ),
            row=1, col=1,
        )
        if show_levels:
            # Render L2/L3 as step lines PLUS markers. L3 in particular
            # is only populated on ~2% of ticks, so a step line alone
            # looks ghostly; the markers anchor each published level
            # so sparse data still reads at a glance.
            for lvl, width, dash, marker in (
                (2, 0.9, "dot", dict(size=3, opacity=0.55)),
                (3, 0.6, "dash", dict(size=4, opacity=0.7, symbol="circle-open")),
            ):
                bcol, acol = f"bid{lvl}", f"ask{lvl}"
                if bcol in book.columns and book[bcol].notna().any():
                    fig.add_trace(
                        go.Scattergl(
                            x=book["timestamp"], y=y_book(bcol),
                            mode="lines+markers", name=f"bid L{lvl}",
                            legendgroup="book",
                            line=dict(color=COLOR_BID, width=width,
                                      shape="hv", dash=dash),
                            marker=dict(color=COLOR_BID, **marker),
                            opacity=0.55,
                            hovertemplate=f"t=%{{x}}<br>bid L{lvl}=%{{y}}<extra></extra>",
                        ),
                        row=1, col=1,
                    )
                if acol in book.columns and book[acol].notna().any():
                    fig.add_trace(
                        go.Scattergl(
                            x=book["timestamp"], y=y_book(acol),
                            mode="lines+markers", name=f"ask L{lvl}",
                            legendgroup="book",
                            line=dict(color=COLOR_ASK, width=width,
                                      shape="hv", dash=dash),
                            marker=dict(color=COLOR_ASK, **marker),
                            opacity=0.55,
                            hovertemplate=f"t=%{{x}}<br>ask L{lvl}=%{{y}}<extra></extra>",
                        ),
                        row=1, col=1,
                    )
        if show_mid:
            fig.add_trace(
                go.Scattergl(
                    x=book["timestamp"], y=y_book("mid"),
                    mode="lines", name="mid",
                    legendgroup="reference",
                    line=dict(color=COLOR_MID, width=1.4),
                    hovertemplate="t=%{x}<br>mid=%{y}<extra></extra>",
                ),
                row=1, col=1,
            )

    # --- Fair value ------------------------------------------------------
    # Thicker + solid so the strategy's anchor is the most readable line
    # (especially critical in the normalized view where ``fair - mid`` is
    # literally the edge signal).
    if show_fair and fair is not None and not fair.empty:
        fig.add_trace(
            go.Scattergl(
                x=fair["timestamp"], y=y_series(fair, "fair"),
                mode="lines", name="fair",
                legendgroup="reference",
                line=dict(color=COLOR_FAIR, width=1.8),
                hovertemplate="t=%{x}<br>fair=%{y}<extra></extra>",
            ),
            row=1, col=1,
        )

    # --- Market trades ---------------------------------------------------
    if show_market_trades and trades is not None and not trades.empty:
        m = trades[trades["source"] == "market"]
        if not m.empty:
            size = np.clip(m["quantity"].astype(float), 3, 10)
            fig.add_trace(
                go.Scattergl(
                    x=m["timestamp"], y=y_series(m, "price"),
                    mode="markers", name="market trade",
                    legendgroup="market",
                    marker=dict(color=COLOR_MKT_TRADE, size=size, opacity=0.35,
                                line=dict(width=0)),
                    customdata=m[["quantity"]].values,
                    hovertemplate=("t=%{x}<br>px=%{y}<br>qty=%{customdata[0]}"
                                   "<extra>market</extra>"),
                ),
                row=1, col=1,
            )

    # --- Quotes we posted ------------------------------------------------
    # Scattergl doesn't support the open triangle symbols, so use the
    # solid triangles with low opacity and a thin matching-color border.
    # That still keeps quotes visually distinct from own fills (which
    # are fully opaque and a size tier bigger).
    if show_quotes and quotes is not None and not quotes.empty:
        qb = quotes[quotes["side"] == "buy"]
        qs = quotes[quotes["side"] == "sell"]
        if not qb.empty:
            size = np.clip(qb["quantity"].abs().astype(float) * 0.5, 3, 9)
            fig.add_trace(
                go.Scattergl(
                    x=qb["timestamp"], y=y_series(qb, "price"),
                    mode="markers", name="quote buy",
                    legendgroup="quotes",
                    marker=dict(symbol="triangle-up", color=COLOR_QUOTE_BUY,
                                size=size, opacity=0.35,
                                line=dict(width=0.8, color=COLOR_QUOTE_BUY)),
                    customdata=qb[["quantity"]].values,
                    hovertemplate=("t=%{x}<br>px=%{y}<br>qty=%{customdata[0]}"
                                   "<extra>quote buy</extra>"),
                ),
                row=1, col=1,
            )
        if not qs.empty:
            size = np.clip(qs["quantity"].abs().astype(float) * 0.5, 3, 9)
            fig.add_trace(
                go.Scattergl(
                    x=qs["timestamp"], y=y_series(qs, "price"),
                    mode="markers", name="quote sell",
                    legendgroup="quotes",
                    marker=dict(symbol="triangle-down", color=COLOR_QUOTE_SELL,
                                size=size, opacity=0.35,
                                line=dict(width=0.8, color=COLOR_QUOTE_SELL)),
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
                go.Scattergl(
                    x=ob["timestamp"], y=y_series(ob, "price"),
                    mode="markers", name="own buy",
                    legendgroup="fills",
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
                go.Scattergl(
                    x=os_["timestamp"], y=y_series(os_, "price"),
                    mode="markers", name="own sell",
                    legendgroup="fills",
                    marker=dict(symbol="triangle-down", color=COLOR_OWN_SELL,
                                size=size, line=dict(color="#4a0010", width=1)),
                    customdata=os_[["quantity"]].values,
                    hovertemplate=("t=%{x}<br>px=%{y}<br>qty=%{customdata[0]}"
                                   "<extra>own sell</extra>"),
                ),
                row=1, col=1,
            )

    if normalize_active:
        # Horizontal zero line marks the chosen reference (mid or fair).
        fig.add_hline(
            y=0, line=dict(color="rgba(255,255,255,0.35)", width=1),
            row=1, col=1,
        )

    # --- Bottom: position -----------------------------------------------
    # Scattergl doesn't support ``fill="tozeroy"`` so we use regular
    # Scatter here -- the position series is a single line with far
    # fewer points than the book, so the non-GL path is fine.
    pos_series = _position_timeline(position, book, product)
    if not pos_series.empty:
        fig.add_trace(
            go.Scatter(
                x=pos_series["timestamp"], y=pos_series["position"],
                mode="lines", name="position",
                legendgroup="position",
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
        pos_pad = max(int(round(limit * 0.15)), 5)
        fig.update_yaxes(
            range=[-limit - pos_pad, limit + pos_pad],
            autorange=False,
            row=2, col=1,
        )

    # --- Layout ----------------------------------------------------------
    fig.update_layout(
        template="plotly_dark",
        paper_bgcolor="#121212",
        plot_bgcolor="#121212",
        margin=dict(l=50, r=20, t=40, b=30),
        legend=dict(
            orientation="h", yanchor="bottom", y=1.02,
            xanchor="left", x=0, bgcolor="rgba(0,0,0,0)",
            groupclick="togglegroup",
        ),
        hovermode="x unified",
        dragmode="pan",      # click-drag pans; native plotly behavior
        uirevision=product,  # keep zoom/pan state when toggles change
    )
    fig.update_xaxes(
        showgrid=True, gridcolor="rgba(255,255,255,0.05)",
        zeroline=False, title_text="timestamp", fixedrange=False, row=2, col=1,
    )
    fig.update_xaxes(showgrid=True, gridcolor="rgba(255,255,255,0.05)",
                     zeroline=False, fixedrange=False, row=1, col=1)
    # Price y-axis auto-rescales to visible data so zooming in on a small
    # time window reveals price structure that would otherwise be compressed.
    fig.update_yaxes(showgrid=True, gridcolor="rgba(255,255,255,0.05)",
                     zeroline=False, title_text=price_label,
                     autorange=True, fixedrange=False, row=1, col=1)
    fig.update_yaxes(showgrid=True, gridcolor="rgba(255,255,255,0.05)",
                     zeroline=False, title_text="position",
                     fixedrange=False, row=2, col=1)
    return fig


def _position_timeline(position: pd.DataFrame,
                       book: pd.DataFrame,
                       product: str) -> pd.DataFrame:
    """Return a position step series that spans the full visible window.

    ``position`` only has rows at fill times, so we forward-fill onto the
    book timestamps (which exist for every tick) to get a smooth step plot
    that starts at 0 from the beginning of the day.

    Forward-fill is scoped to each sandbox day (``DAY_TICKS``) so positions
    never bleed across day boundaries (each Prosperity day starts at 0).
    """
    if book is None or book.empty:
        return position
    grid = book[["timestamp"]].drop_duplicates().sort_values("timestamp").copy()
    grid["day"] = (grid["timestamp"] // DAY_TICKS).astype(int)
    if position is None or position.empty:
        grid = grid.assign(position=0, product=product)
        return grid[["timestamp", "product", "position"]]
    pos = position.sort_values("timestamp").copy()
    pos["day"] = (pos["timestamp"] // DAY_TICKS).astype(int)
    merged = pd.merge_asof(
        grid.sort_values("timestamp"),
        pos[["timestamp", "position", "day"]],
        on="timestamp",
        by="day",
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
