"""Dash dashboard for strategy + market-data analysis.

Two pages sharing the same source cache:

  /               - Order Book Viewer (book, fills, quotes, fair line)
  /cointegration  - Cointegration Tester (ADF + OLS on raw market CSVs)

Run from the repository root:

    python -m VISUALIZER.app                # auto-discovers LOGS/ and DATA/
    python -m VISUALIZER.app LOGS/foo.log   # preload a specific source

Then open http://127.0.0.1:8050.
"""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Optional

from dash import Dash, Input, Output, dcc, html
import dash_bootstrap_components as dbc

from .parser import LogBundle, discover_sources, load_source
from .pages import order_book, cointegration


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


def build_shell(sources: list[dict], initial_value: Optional[str]) -> html.Div:
    header = html.Div(
        [
            html.Span("SLIME", className="fincept-brand-a"),
            html.Span("BALLERZ", className="fincept-brand-b"),
            html.Span(" // STRATEGY MONITOR", className="fincept-brand-sub"),
        ],
        className="fincept-header",
    )
    nav = html.Div(
        [
            dcc.Link("Order Book Viewer", href="/", className="fincept-nav-link", id="nav-order-book"),
            dcc.Link("Cointegration Tester", href="/cointegration", className="fincept-nav-link", id="nav-coint"),
        ],
        className="fincept-nav",
    )
    return dbc.Container(
        [
            dcc.Location(id="url", refresh=False),
            header,
            nav,
            html.Div(id="page-content", className="fincept-page"),
            dcc.Store(id="__sources", data=sources),
            dcc.Store(id="__initial-source", data=initial_value),
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
        suppress_callback_exceptions=True,
    )
    app.layout = build_shell(sources, initial)

    @app.callback(
        Output("page-content", "children"),
        Output("nav-order-book", "className"),
        Output("nav-coint", "className"),
        Input("url", "pathname"),
    )
    def _route(pathname):
        path = (pathname or "/").rstrip("/") or "/"
        base = "fincept-nav-link"
        active = "fincept-nav-link fincept-nav-active"
        if path == "/cointegration":
            return cointegration.layout(sources, initial), base, active
        return order_book.layout(sources, initial), active, base

    order_book.register_callbacks(app, _get_bundle)
    cointegration.register_callbacks(app, _get_bundle)

    return app


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
