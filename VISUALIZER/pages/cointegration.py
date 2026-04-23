"""Cointegration Tester page: ADF + OLS on two raw market-data CSV series."""

from __future__ import annotations

from typing import Callable, Optional

import numpy as np
import pandas as pd
import plotly.graph_objects as go
from dash import Input, Output, State, dcc, html, no_update
import dash_bootstrap_components as dbc

from ..cointegration import run_cointegration
from ..parser import LogBundle


def _data_only_sources(sources: list[dict]) -> list[dict]:
    """Cointegration only works against raw market-data CSVs."""
    return [s for s in sources if s.get("value", "").startswith("data::")]


def layout(sources: list[dict], initial_value: Optional[str]) -> html.Div:
    data_sources = _data_only_sources(sources)
    first = data_sources[0]["value"] if data_sources else None

    source_y = dcc.Dropdown(
        id="coint-source-y",
        options=data_sources,
        value=first,
        clearable=False,
        className="fincept-dropdown",
    )
    product_y = dcc.Dropdown(
        id="coint-product-y",
        options=[],
        value=None,
        clearable=False,
        className="fincept-dropdown",
    )
    source_x = dcc.Dropdown(
        id="coint-source-x",
        options=data_sources,
        value=first,
        clearable=False,
        className="fincept-dropdown",
    )
    product_x = dcc.Dropdown(
        id="coint-product-x",
        options=[],
        value=None,
        clearable=False,
        className="fincept-dropdown",
    )
    split_toggle = dcc.Checklist(
        id="coint-split-toggle",
        options=[{"label": " use two separate CSVs", "value": "split"}],
        value=[],
        inline=True,
        className="fincept-toggles",
    )
    price_col_radio = dcc.RadioItems(
        id="coint-price-col",
        options=[
            {"label": " mid", "value": "mid"},
            {"label": " best bid", "value": "bid1"},
            {"label": " best ask", "value": "ask1"},
        ],
        value="mid",
        inline=True,
        className="fincept-toggles",
    )
    run_btn = html.Button(
        "run test",
        id="coint-run",
        n_clicks=0,
        className="fincept-run-btn",
    )

    controls = html.Div(
        [
            dbc.Row(
                [
                    dbc.Col([html.Label("Series Y  CSV", className="fincept-label"), source_y], md=4),
                    dbc.Col([html.Label("Series Y  product", className="fincept-label"), product_y], md=2),
                    dbc.Col([html.Label("Series X  CSV", className="fincept-label"), source_x], md=4),
                    dbc.Col([html.Label("Series X  product", className="fincept-label"), product_x], md=2),
                ],
                className="g-2",
            ),
            dbc.Row(
                [
                    dbc.Col([html.Label("Price column", className="fincept-label"), price_col_radio], md=4),
                    dbc.Col([html.Label("Options", className="fincept-label"), split_toggle], md=5),
                    dbc.Col([html.Label(" ", className="fincept-label"), run_btn], md=3),
                ],
                className="g-2 fincept-coint-row",
            ),
        ]
    )

    verdict = html.Div(id="coint-verdict", className="fincept-coint-verdict")
    stats = html.Div(id="coint-stats", className="fincept-coint-stats")

    def _graph(gid: str, height: str = "36vh") -> dcc.Graph:
        return dcc.Graph(
            id=gid,
            style={"height": height},
            config={
                "displaylogo": False,
                "scrollZoom": True,
                "doubleClick": "reset",
                "modeBarButtonsToRemove": ["select2d", "lasso2d"],
            },
        )

    plots = html.Div(
        [
            dcc.Loading(_graph("coint-plot-series"), type="dot", color="#ffab40"),
            dbc.Row(
                [
                    dbc.Col(dcc.Loading(_graph("coint-plot-scatter"), type="dot", color="#ffab40"), md=6),
                    dbc.Col(dcc.Loading(_graph("coint-plot-resid"), type="dot", color="#ffab40"), md=6),
                ],
                className="g-2",
            ),
        ]
    )

    return html.Div(
        [
            controls,
            verdict,
            stats,
            plots,
            dcc.Store(id="coint-sources", data=data_sources),
        ]
    )


def register_callbacks(app, get_bundle: Callable[[str], LogBundle]) -> None:
    @app.callback(
        Output("coint-source-x", "disabled"),
        Input("coint-split-toggle", "value"),
    )
    def _toggle_split(split_value):
        return "split" not in (split_value or [])

    @app.callback(
        Output("coint-source-x", "value"),
        Input("coint-source-y", "value"),
        Input("coint-split-toggle", "value"),
        State("coint-source-x", "value"),
    )
    def _mirror_source_when_single(y_value, split_value, x_value):
        if "split" in (split_value or []):
            return x_value or y_value
        return y_value

    @app.callback(
        Output("coint-product-y", "options"),
        Output("coint-product-y", "value"),
        Input("coint-source-y", "value"),
    )
    def _products_y(value):
        return _products_for(get_bundle, value, preferred_index=0)

    @app.callback(
        Output("coint-product-x", "options"),
        Output("coint-product-x", "value"),
        Input("coint-source-x", "value"),
    )
    def _products_x(value):
        return _products_for(get_bundle, value, preferred_index=1)

    @app.callback(
        Output("coint-verdict", "children"),
        Output("coint-stats", "children"),
        Output("coint-plot-series", "figure"),
        Output("coint-plot-scatter", "figure"),
        Output("coint-plot-resid", "figure"),
        Input("coint-run", "n_clicks"),
        Input("coint-source-y", "value"),
        Input("coint-source-x", "value"),
        Input("coint-product-y", "value"),
        Input("coint-product-x", "value"),
        Input("coint-price-col", "value"),
    )
    def _on_run(_n, sy, sx, py, px, price_col):
        if not sy or not sx or not py or not px:
            return (_empty_verdict("pick two series"), None,
                    _empty_fig("series"), _empty_fig("scatter"), _empty_fig("residuals"))
        if sy == sx and py == px:
            return (_empty_verdict("Y and X are identical - pick different products"),
                    None,
                    _empty_fig("series"), _empty_fig("scatter"), _empty_fig("residuals"))

        by = get_bundle(sy)
        bx = get_bundle(sx)
        col = price_col or "mid"
        y_series = _extract_series(by, py, col)
        x_series = _extract_series(bx, px, col)
        if y_series.empty or x_series.empty:
            return (_empty_verdict(f"no {col} data for chosen product"), None,
                    _empty_fig("series"), _empty_fig("scatter"), _empty_fig("residuals"))

        joined = pd.concat(
            [y_series.rename("y"), x_series.rename("x")],
            axis=1, join="inner",
        ).dropna()
        if len(joined) < 40:
            return (_empty_verdict(f"only {len(joined)} shared ticks - need more"),
                    None,
                    _empty_fig("series"), _empty_fig("scatter"), _empty_fig("residuals"))

        result = run_cointegration(joined["y"], joined["x"])

        verdict = _render_verdict(result)
        stats = _render_stats(result, py, px, col, n=len(joined))
        fig_series = _plot_series(joined, py, px)
        fig_scatter = _plot_scatter(joined, py, px, result)
        fig_resid = _plot_residuals(result)
        return verdict, stats, fig_series, fig_scatter, fig_resid


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _products_for(get_bundle, value, preferred_index=0):
    if not value:
        return [], None
    bundle = get_bundle(value)
    products = [{"label": p, "value": p} for p in bundle.products]
    if not products:
        return [], None
    idx = min(preferred_index, len(products) - 1)
    return products, products[idx]["value"]


def _extract_series(bundle: LogBundle, product: str, col: str) -> pd.Series:
    book = bundle.book
    if book is None or book.empty:
        return pd.Series(dtype=float)
    sub = book[book["product"] == product]
    if sub.empty or col not in sub.columns:
        return pd.Series(dtype=float)
    return (sub.set_index("timestamp")[col]
            .astype(float)
            .sort_index())


def _empty_fig(title: str) -> go.Figure:
    fig = go.Figure()
    fig.update_layout(
        template="plotly_dark",
        paper_bgcolor="#121212",
        plot_bgcolor="#121212",
        margin=dict(l=40, r=20, t=30, b=30),
        title=title,
        dragmode="pan",
    )
    return fig


def _empty_verdict(msg: str) -> html.Div:
    return html.Div(msg, className="fincept-coint-verdict-none")


def _render_verdict(result: dict) -> html.Div:
    is_coint = result.get("verdict") == "cointegrated"
    cls = "fincept-coint-verdict-yes" if is_coint else "fincept-coint-verdict-no"
    adf_p = result["adf_resid"]["pvalue"]
    beta = result["beta"]
    headline = "COINTEGRATED" if is_coint else "NOT COINTEGRATED"
    return html.Div(
        [
            html.Span(headline, className="fincept-coint-headline"),
            html.Span(
                f"  |  residual ADF p = {adf_p:.4f}  |  beta = {beta:.4f}",
                className="fincept-coint-sub",
            ),
        ],
        className=cls,
    )


def _render_stats(result: dict, py: str, px: str, col: str, n: int) -> html.Div:
    def _row(label, stat, p):
        p_str = f"{p:.4f}" if p is not None else "-"
        stat_str = f"{stat:+.3f}" if stat is not None else "-"
        return html.Tr([
            html.Td(label, className="fincept-coint-cell-label"),
            html.Td(stat_str, className="fincept-coint-cell"),
            html.Td(p_str, className="fincept-coint-cell"),
        ])

    header = html.Thead(
        html.Tr([
            html.Th("series"),
            html.Th("ADF stat"),
            html.Th("p-value"),
        ])
    )
    body = html.Tbody([
        _row(f"Y  {py}  ({col})", result["adf_y"]["stat"], result["adf_y"]["pvalue"]),
        _row(f"X  {px}  ({col})", result["adf_x"]["stat"], result["adf_x"]["pvalue"]),
        _row("residual", result["adf_resid"]["stat"], result["adf_resid"]["pvalue"]),
    ])
    table = html.Table([header, body], className="fincept-coint-table")
    meta = html.Div(
        [
            html.Span(f"beta = {result['beta']:.5f}", className="fincept-coint-meta"),
            html.Span(f"alpha = {result['alpha']:.4f}", className="fincept-coint-meta"),
            html.Span(f"n = {n:,} shared ticks", className="fincept-coint-meta"),
        ],
        className="fincept-coint-meta-row",
    )
    return html.Div([table, meta])


def _plot_series(joined: pd.DataFrame, py: str, px: str) -> go.Figure:
    fig = go.Figure()
    fig.add_trace(
        go.Scattergl(
            x=joined.index, y=joined["y"],
            mode="lines", name=f"Y  {py}",
            line=dict(color="#4fc3f7", width=1.4),
            yaxis="y1",
        )
    )
    fig.add_trace(
        go.Scattergl(
            x=joined.index, y=joined["x"],
            mode="lines", name=f"X  {px}",
            line=dict(color="#ffab40", width=1.2),
            yaxis="y2",
        )
    )
    fig.update_layout(
        template="plotly_dark",
        paper_bgcolor="#121212",
        plot_bgcolor="#121212",
        margin=dict(l=50, r=50, t=30, b=30),
        title="both series (twin y-axes)",
        dragmode="pan",
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="left", x=0,
                    bgcolor="rgba(0,0,0,0)"),
        yaxis=dict(title=f"Y  {py}", side="left",
                   gridcolor="rgba(255,255,255,0.05)", zeroline=False),
        yaxis2=dict(title=f"X  {px}", side="right", overlaying="y",
                    gridcolor="rgba(255,255,255,0.0)", zeroline=False),
        xaxis=dict(title="timestamp",
                   gridcolor="rgba(255,255,255,0.05)", zeroline=False),
    )
    return fig


def _plot_scatter(joined: pd.DataFrame, py: str, px: str, result: dict) -> go.Figure:
    fig = go.Figure()
    fig.add_trace(
        go.Scattergl(
            x=joined["x"], y=joined["y"],
            mode="markers", name="observations",
            marker=dict(color="#90a4ae", size=3, opacity=0.35),
        )
    )
    xs = np.array([joined["x"].min(), joined["x"].max()])
    ys = result["beta"] * xs + result["alpha"]
    fig.add_trace(
        go.Scatter(
            x=xs, y=ys, mode="lines",
            name=f"OLS  Y = {result['beta']:.3f}*X + {result['alpha']:.2f}",
            line=dict(color="#ffab40", width=2),
        )
    )
    fig.update_layout(
        template="plotly_dark",
        paper_bgcolor="#121212",
        plot_bgcolor="#121212",
        margin=dict(l=50, r=20, t=30, b=30),
        title="OLS regression",
        dragmode="pan",
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="left", x=0,
                    bgcolor="rgba(0,0,0,0)"),
        xaxis=dict(title=f"X  {px}", gridcolor="rgba(255,255,255,0.05)", zeroline=False),
        yaxis=dict(title=f"Y  {py}", gridcolor="rgba(255,255,255,0.05)", zeroline=False),
    )
    return fig


def _plot_residuals(result: dict) -> go.Figure:
    resid = result["residuals"]
    fig = go.Figure()
    fig.add_trace(
        go.Scattergl(
            x=resid.index, y=resid.values,
            mode="lines", name="residual",
            line=dict(color="#ef5350", width=1.0),
        )
    )
    fig.add_hline(y=0, line=dict(color="rgba(255,255,255,0.35)", width=1))
    adf = result["adf_resid"]
    p = adf.get("pvalue")
    fig.update_layout(
        template="plotly_dark",
        paper_bgcolor="#121212",
        plot_bgcolor="#121212",
        margin=dict(l=50, r=20, t=30, b=30),
        title=f"residual series  |  ADF p = {p:.4f}" if p is not None else "residual series",
        dragmode="pan",
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="left", x=0,
                    bgcolor="rgba(0,0,0,0)"),
        xaxis=dict(title="timestamp", gridcolor="rgba(255,255,255,0.05)", zeroline=False),
        yaxis=dict(title="residual", gridcolor="rgba(255,255,255,0.05)", zeroline=False),
    )
    return fig
