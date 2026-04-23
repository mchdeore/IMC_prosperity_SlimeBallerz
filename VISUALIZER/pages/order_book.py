"""Order Book Viewer page: the strategy-log-driven book + fills chart."""

from __future__ import annotations

from typing import Callable, Optional

from dash import Input, Output, dcc, html, no_update
import dash_bootstrap_components as dbc

from ..figures import build_figure, compute_kpis, POSITION_LIMITS
from ..parser import LogBundle


def layout(sources: list[dict], initial_value: Optional[str]) -> html.Div:
    source_dropdown = dcc.Dropdown(
        id="source-picker",
        options=sources,
        value=initial_value or (sources[0]["value"] if sources else None),
        clearable=False,
        className="fincept-dropdown",
    )
    product_dropdown = dcc.Dropdown(
        id="product-picker",
        options=[],
        value=None,
        clearable=False,
        className="fincept-dropdown",
    )
    toggles = dcc.Checklist(
        id="layer-toggles",
        options=[
            {"label": " my quotes", "value": "quotes"},
            {"label": " market trades", "value": "market"},
            {"label": " fair value", "value": "fair"},
            {"label": " mid", "value": "mid"},
            {"label": " all quote levels", "value": "levels"},
        ],
        value=["quotes", "market", "fair", "mid"],
        inline=True,
        className="fincept-toggles",
    )
    normalize_radio = dcc.RadioItems(
        id="normalize-mode",
        options=[
            {"label": " raw", "value": "none"},
            {"label": " - mid", "value": "mid"},
            {"label": " - fair", "value": "fair"},
        ],
        value="none",
        inline=True,
        className="fincept-toggles",
    )
    controls = dbc.Row(
        [
            dbc.Col([html.Label("Source", className="fincept-label"), source_dropdown], md=4),
            dbc.Col([html.Label("Product", className="fincept-label"), product_dropdown], md=2),
            dbc.Col([html.Label("Layers", className="fincept-label"), toggles], md=4),
            dbc.Col([html.Label("Normalize", className="fincept-label"), normalize_radio], md=2),
        ],
        className="g-2",
    )

    kpi_row = html.Div(id="kpi-row", className="fincept-kpi-row")

    main = dcc.Loading(
        dcc.Graph(
            id="main-figure",
            style={"height": "78vh"},
            config={
                "displaylogo": False,
                "scrollZoom": True,
                "doubleClick": "reset",
                "modeBarButtonsToRemove": ["select2d", "lasso2d"],
            },
        ),
        type="dot",
        color="#ffab40",
    )

    footer = html.Div(id="source-meta", className="fincept-footer")

    return html.Div(
        [
            controls,
            html.Div(
                "click-drag to pan - scroll to zoom - double-click to reset",
                className="fincept-hint",
            ),
            kpi_row,
            main,
            footer,
        ]
    )


def register_callbacks(app, get_bundle: Callable[[str], LogBundle]) -> None:
    @app.callback(
        Output("product-picker", "options"),
        Output("product-picker", "value"),
        Output("source-meta", "children"),
        Input("source-picker", "value"),
    )
    def _on_source(value):
        if not value:
            return [], None, ""
        bundle = get_bundle(value)
        products = [{"label": p, "value": p} for p in bundle.products]
        product_value = bundle.products[0] if bundle.products else None
        meta_bits = [
            f"format: {bundle.meta.get('format')}",
            f"file: {bundle.meta.get('name')}",
            f"ticks: {len(bundle.book):,}",
            f"fills: {(bundle.trades['source'].isin(('own_buy', 'own_sell'))).sum() if not bundle.trades.empty else 0}",
            f"quotes: {len(bundle.quotes):,}",
        ]
        if bundle.meta.get("submissionId"):
            meta_bits.insert(0, f"submission: {bundle.meta['submissionId']}")
        return products, product_value, " | ".join(meta_bits)

    @app.callback(
        Output("main-figure", "figure"),
        Output("kpi-row", "children"),
        Input("source-picker", "value"),
        Input("product-picker", "value"),
        Input("layer-toggles", "value"),
        Input("normalize-mode", "value"),
    )
    def _on_inputs(source_value, product, layers, normalize_mode):
        if not source_value or not product:
            return no_update, no_update
        bundle = get_bundle(source_value)
        if bundle.book.empty:
            tmin, tmax = 0, 1
        else:
            tmin = int(bundle.book["timestamp"].min())
            tmax = int(bundle.book["timestamp"].max())
            if tmin == tmax:
                tmax = tmin + 1
        layers = layers or []
        fig = build_figure(
            bundle,
            product=product,
            time_range=(tmin, tmax),
            show_quotes="quotes" in layers,
            show_market_trades="market" in layers,
            show_fair="fair" in layers,
            show_mid="mid" in layers,
            show_levels="levels" in layers,
            normalize_mode=normalize_mode or "none",
        )
        kpis = compute_kpis(bundle, product, (tmin, tmax))
        return fig, _kpi_cards(kpis, product)


def _kpi_cards(kpis: dict, product: str) -> list:
    limit = POSITION_LIMITS.get(product)

    def card(title, value, sub=None, accent="#ffab40"):
        children = [
            html.Div(title, className="fincept-kpi-title"),
            html.Div(value, className="fincept-kpi-value", style={"color": accent}),
        ]
        if sub:
            children.append(html.Div(sub, className="fincept-kpi-sub"))
        return html.Div(children, className="fincept-kpi")

    pnl = kpis.get("pnl")
    pnl_delta = kpis.get("pnl_delta")
    pnl_txt = f"{pnl:,.2f}" if pnl is not None else "-"
    pnl_sub = f"Δ {pnl_delta:+,.2f}" if pnl_delta is not None else ""
    pnl_color = "#66bb6a" if (pnl_delta or 0) >= 0 else "#ef5350"

    pos = kpis.get("current_position", 0)
    pos_sub = f"limit  +/-{limit}" if limit else ""
    pos_color = "#ffab40"
    if limit:
        if abs(pos) >= limit:
            pos_color = "#ef5350"
        elif abs(pos) >= limit * 0.8:
            pos_color = "#ffab40"
        else:
            pos_color = "#66bb6a"

    return [
        card("P&L (last)", pnl_txt, pnl_sub, accent=pnl_color),
        card("Position", f"{pos:+d}", pos_sub, accent=pos_color),
        card("Own fills", f"{kpis.get('fills', 0):,}", f"vol {kpis.get('own_volume', 0):,}"),
        card("Quotes posted", f"{kpis.get('quotes', 0):,}"),
        card("Market trades", f"{kpis.get('market_trades', 0):,}"),
    ]
