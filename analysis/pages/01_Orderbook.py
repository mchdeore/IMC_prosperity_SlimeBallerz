import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import streamlit as st
import numpy as np
import plotly.graph_objects as go

from data_loader import load_csv_prices, load_csv_trades, compute_wall_mid, compute_raw_mid, filter_time_range
from plot_helpers import (
    apply_crosshair, sidebar_data_selector, show_description,
    BID_COLORS, ASK_COLORS, TRADE_COLOR, WALL_MID_COLOR, RAW_MID_COLOR,
)

st.set_page_config(page_title="Orderbook", layout="wide")
st.title("Orderbook Viewer")
show_description("01_Orderbook")

prices_path, trades_path, product = sidebar_data_selector()

@st.cache_data
def get_data(pp, tp, prod):
    pr = compute_raw_mid(compute_wall_mid(load_csv_prices(Path(pp), product=prod)))
    tr = load_csv_trades(Path(tp), product=prod) if tp else None
    return pr, tr

prices, trades = get_data(str(prices_path), str(trades_path) if trades_path else None, product)
ts_min, ts_max = int(prices["timestamp"].min()), int(prices["timestamp"].max())
time_range = st.sidebar.slider("Time range", ts_min, ts_max, (ts_min, ts_max), step=100)

normalize = st.sidebar.selectbox("Normalize by", ["None", "wall_mid", "raw_mid", "mid_price"])
show_trades = st.sidebar.checkbox("Show trades", True)
show_wall_mid = st.sidebar.checkbox("Show Wall Mid", True)
show_raw_mid = st.sidebar.checkbox("Show Raw Mid", False)

pf = filter_time_range(prices, time_range[0], time_range[1])
ts = pf["timestamp"].values
baseline = pf[normalize].values if normalize != "None" else np.zeros(len(pf))

fig = go.Figure()
LEVEL_MODES = {1: "lines", 2: "lines+markers", 3: "markers"}
LEVEL_SIZES = {1: None, 2: 3, 3: 3}

for level in [1, 2, 3]:
    mode = LEVEL_MODES[level]
    msize = LEVEL_SIZES[level]
    for side, colors, prefix in [("bid", BID_COLORS, "Bid"), ("ask", ASK_COLORS, "Ask")]:
        pc = f"{side}_price_{level}"
        vc = f"{side}_volume_{level}"
        mask = pf[pc].notna()
        if not mask.any():
            continue
        y = pf.loc[mask, pc].values - baseline[mask]
        vol = pf.loc[mask, vc].values
        marker_cfg = dict(size=msize, color=colors[level - 1]) if msize else None
        fig.add_trace(go.Scattergl(
            x=ts[mask], y=y, mode=mode, connectgaps=False,
            line=dict(color=colors[level - 1], width=1) if "lines" in mode else None,
            marker=marker_cfg,
            name=f"{prefix} L{level}",
            customdata=vol,
            hovertemplate="t=%{x}<br>price=%{y:.2f}<br>vol=%{customdata:.0f}<extra>%{fullData.name}</extra>",
        ))

if show_trades and trades is not None and len(trades) > 0:
    tf = filter_time_range(trades, time_range[0], time_range[1])
    if len(tf) > 0:
        tbl = np.interp(tf["timestamp"].values, ts, baseline)
        fig.add_trace(go.Scattergl(
            x=tf["timestamp"], y=tf["price"].values - tbl, mode="markers",
            marker=dict(size=5, color=TRADE_COLOR, symbol="triangle-up", line=dict(width=0.5, color="black")),
            name="Trades", customdata=tf["quantity"].values,
            hovertemplate="t=%{x}<br>price=%{y:.2f}<br>qty=%{customdata}<extra>Trade</extra>",
        ))

if show_wall_mid:
    fig.add_trace(go.Scattergl(
        x=ts, y=pf["wall_mid"].values - baseline, mode="lines", connectgaps=False,
        line=dict(color=WALL_MID_COLOR, dash="dash", width=1.5), name="Wall Mid",
        hovertemplate="t=%{x}<br>%{y:.2f}<extra>Wall Mid</extra>",
    ))
if show_raw_mid:
    fig.add_trace(go.Scattergl(
        x=ts, y=pf["raw_mid"].values - baseline, mode="lines", connectgaps=False,
        line=dict(color=RAW_MID_COLOR, dash="dash", width=1), name="Raw Mid",
        hovertemplate="t=%{x}<br>%{y:.2f}<extra>Raw Mid</extra>",
    ))

norm_label = f" (norm: {normalize})" if normalize != "None" else ""
fig.update_layout(
    title=f"{product}{norm_label}",
    xaxis_title="Timestamp",
    yaxis_title="Price" if normalize == "None" else f"Price - {normalize}",
    height=600,
)
st.plotly_chart(apply_crosshair(fig), use_container_width=True)

c1, c2, c3, c4 = st.columns(4)
spread = pf["ask_price_1"] - pf["bid_price_1"]
c1.metric("Avg Spread", f"{spread.mean():.2f}")
c2.metric("Wall Mid Std", f"{pf['wall_mid'].std():.2f}")
c3.metric("Wall Mid Range", f"{pf['wall_mid'].min():.0f} - {pf['wall_mid'].max():.0f}")
c4.metric("Trades", f"{len(trades) if trades is not None else 0}")
