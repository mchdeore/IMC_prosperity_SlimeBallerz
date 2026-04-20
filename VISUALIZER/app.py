"""Dash dashboard for visualizing strategy performance from logs.

Run from the repository root:

    python -m VISUALIZER.app                # auto-discovers LOGS/ and DATA/
    python -m VISUALIZER.app LOGS/foo.log   # preload a specific source

Then open http://127.0.0.1:8050.
"""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Optional

import dash
from dash import Dash, Input, Output, dcc, html, no_update
import dash_bootstrap_components as dbc

from .parser import LogBundle, discover_sources, load_source
from .figures import build_figure, compute_kpis, POSITION_LIMITS


REPO_ROOT = Path(__file__).resolve().parent.parent


_CACHE: dict[str, LogBundle] = {}


def _get_bundle(value: str) -> LogBundle:
    if value not in _CACHE:
        _CACHE[value] = load_source(value)
    return _CACHE[value]


def _initial_source_value(preload: Optional[Path]) -> Optional[str]:
    if preload is None:
        return None
    p = Path(preload)
    if p.suffix == ".csv":
        return f"data::{p}"
    return f"log::{p}"


def build_layout(sources: list[dict], initial_value: Optional[str]) -> html.Div:
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
        ],
        value=["quotes", "market", "fair", "mid"],
        inline=True,
        className="fincept-toggles",
    )
    time_slider = dcc.RangeSlider(
        id="time-slider",
        min=0,
        max=1,
        value=[0, 1],
        allowCross=False,
        tooltip={"placement": "bottom"},
    )

    controls = dbc.Row(
        [
            dbc.Col([html.Label("Source", className="fincept-label"), source_dropdown], md=5),
            dbc.Col([html.Label("Product", className="fincept-label"), product_dropdown], md=3),
            dbc.Col([html.Label("Layers", className="fincept-label"), toggles], md=4),
        ],
        className="g-2",
    )

    kpi_row = html.Div(id="kpi-row", className="fincept-kpi-row")

    main = dcc.Loading(
        dcc.Graph(
            id="main-figure",
            style={"height": "78vh"},
            config={"displaylogo": False, "scrollZoom": True},
        ),
        type="dot",
        color="#ffab40",
    )

    footer = html.Div(id="source-meta", className="fincept-footer")

    return dbc.Container(
        [
            html.Div(
                [
                    html.Span("SLIME", className="fincept-brand-a"),
                    html.Span("BALLERZ", className="fincept-brand-b"),
                    html.Span(" // STRATEGY MONITOR", className="fincept-brand-sub"),
                ],
                className="fincept-header",
            ),
            controls,
            html.Div(
                [html.Label("Timestamp range", className="fincept-label"), time_slider],
                className="fincept-slider-wrap",
            ),
            kpi_row,
            main,
            footer,
        ],
        fluid=True,
        className="fincept-container",
    )


def create_app(preload: Optional[Path] = None) -> Dash:
    sources = discover_sources(REPO_ROOT)
    initial = _initial_source_value(preload)
    if initial and not any(s["value"] == initial for s in sources):
        label = Path(initial.split("::", 1)[1]).name
        sources = [{"label": f"* {label}", "value": initial}, *sources]

    app = Dash(
        __name__,
        external_stylesheets=[dbc.themes.CYBORG],
        title="SlimeBallerz Strategy Monitor",
        update_title=None,
    )
    app.layout = build_layout(sources, initial)

    @app.callback(
        Output("product-picker", "options"),
        Output("product-picker", "value"),
        Output("time-slider", "min"),
        Output("time-slider", "max"),
        Output("time-slider", "value"),
        Output("time-slider", "marks"),
        Output("source-meta", "children"),
        Input("source-picker", "value"),
    )
    def _on_source(value):
        if not value:
            return [], None, 0, 1, [0, 1], {}, ""
        bundle = _get_bundle(value)
        products = [{"label": p, "value": p} for p in bundle.products]
        product_value = bundle.products[0] if bundle.products else None
        if bundle.book.empty:
            tmin, tmax = 0, 1
        else:
            tmin = int(bundle.book["timestamp"].min())
            tmax = int(bundle.book["timestamp"].max())
            if tmin == tmax:
                tmax = tmin + 1
        span = tmax - tmin
        marks = {
            int(tmin + span * frac): f"{int(tmin + span * frac):,}"
            for frac in (0, 0.25, 0.5, 0.75, 1.0)
        }
        meta_bits = [
            f"format: {bundle.meta.get('format')}",
            f"file: {bundle.meta.get('name')}",
            f"ticks: {len(bundle.book):,}",
            f"fills: {(bundle.trades['source'].isin(('own_buy', 'own_sell'))).sum() if not bundle.trades.empty else 0}",
            f"quotes: {len(bundle.quotes):,}",
        ]
        if bundle.meta.get("submissionId"):
            meta_bits.insert(0, f"submission: {bundle.meta['submissionId']}")
        return products, product_value, tmin, tmax, [tmin, tmax], marks, " | ".join(meta_bits)

    @app.callback(
        Output("main-figure", "figure"),
        Output("kpi-row", "children"),
        Input("source-picker", "value"),
        Input("product-picker", "value"),
        Input("time-slider", "value"),
        Input("layer-toggles", "value"),
    )
    def _on_inputs(source_value, product, time_range, layers):
        if not source_value or not product or not time_range:
            return no_update, no_update
        bundle = _get_bundle(source_value)
        fig = build_figure(
            bundle,
            product=product,
            time_range=tuple(time_range),
            show_quotes="quotes" in (layers or []),
            show_market_trades="market" in (layers or []),
            show_fair="fair" in (layers or []),
            show_mid="mid" in (layers or []),
        )
        kpis = compute_kpis(bundle, product, tuple(time_range))
        return fig, _kpi_cards(kpis, product)

    return app


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


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", nargs="?", default=None, help="Optional log (.log) or market-data CSV to preload.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8050)
    parser.add_argument("--debug", action="store_true")
    args = parser.parse_args()

    preload = Path(args.source).resolve() if args.source else None
    app = create_app(preload)
    app.run(host=args.host, port=args.port, debug=args.debug)


if __name__ == "__main__":
    main()
