import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import streamlit as st
import numpy as np
import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots

from data_loader import load_csv_prices, load_csv_trades, compute_wall_mid, compute_raw_mid, filter_time_range
from plot_helpers import apply_crosshair, sidebar_data_selector, show_description, BID_COLORS, ASK_COLORS, TRADE_COLOR, WALL_MID_COLOR

st.set_page_config(page_title="Product Compare", layout="wide")
st.title("Product Comparison")
show_description("04_Product_Compare")

prices_path, trades_path, _ = sidebar_data_selector()

@st.cache_data
def load_all(pp, tp):
    pr = compute_raw_mid(compute_wall_mid(load_csv_prices(Path(pp))))
    products = sorted(pr["product"].unique().tolist())
    tr = load_csv_trades(Path(tp)) if tp else None
    return pr, tr, products

all_prices, all_trades, products = load_all(str(prices_path), str(trades_path) if trades_path else None)

if len(products) < 2:
    st.info("Only one product in this file. Need at least two for comparison.")
    st.stop()

ts_min = int(all_prices["timestamp"].min())
ts_max = int(all_prices["timestamp"].max())
time_range = st.sidebar.slider("Time range", ts_min, ts_max, (ts_min, ts_max), step=100)
show_trades = st.sidebar.checkbox("Show trades", True)

LEVEL_MODES = {1: "lines", 2: "lines+markers", 3: "markers"}
fig = make_subplots(rows=1, cols=min(len(products), 2), subplot_titles=products[:2])

for idx, prod in enumerate(products[:2]):
    col = idx + 1
    pr = filter_time_range(all_prices[all_prices["product"] == prod], time_range[0], time_range[1])
    ts = pr["timestamp"].values
    for level in [1, 2, 3]:
        mode = LEVEL_MODES[level]
        msize = 3 if level > 1 else None
        for side, colors in [("bid", BID_COLORS), ("ask", ASK_COLORS)]:
            pc, vc = f"{side}_price_{level}", f"{side}_volume_{level}"
            m = pr[pc].notna()
            if m.any():
                fig.add_trace(go.Scattergl(
                    x=ts[m], y=pr.loc[m, pc], mode=mode, connectgaps=False,
                    line=dict(color=colors[level - 1], width=0.8) if "lines" in mode else None,
                    marker=dict(size=msize, color=colors[level - 1]) if msize else None,
                    showlegend=False,
                    hovertemplate="t=%{x}<br>%{y:.2f}<extra></extra>",
                ), row=1, col=col)
    if show_trades and all_trades is not None:
        tr = filter_time_range(all_trades[all_trades["symbol"] == prod] if "symbol" in all_trades.columns else all_trades[all_trades["product"] == prod], time_range[0], time_range[1])
        if len(tr) > 0:
            fig.add_trace(go.Scattergl(x=tr["timestamp"], y=tr["price"], mode="markers", marker=dict(size=4, color=TRADE_COLOR, symbol="triangle-up"), showlegend=False, hovertemplate="t=%{x}<br>%{y:.2f}<extra>Trade</extra>"), row=1, col=col)
    fig.add_trace(go.Scattergl(x=ts, y=pr["wall_mid"], mode="lines", connectgaps=False, line=dict(color=WALL_MID_COLOR, dash="dash", width=1), showlegend=False, hovertemplate="t=%{x}<br>%{y:.2f}<extra>Wall Mid</extra>"), row=1, col=col)

fig.update_layout(height=480)
st.plotly_chart(apply_crosshair(fig), use_container_width=True)
