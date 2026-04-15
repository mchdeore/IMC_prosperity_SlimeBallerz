import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import streamlit as st
import numpy as np
import plotly.graph_objects as go

from data_loader import load_csv_prices, load_csv_trades, compute_wall_mid, filter_time_range
from plot_helpers import apply_crosshair, sidebar_data_selector, show_description, TRADE_COLOR, WALL_MID_COLOR

st.set_page_config(page_title="Informed Detector", layout="wide")
st.title("Informed Trader Detector")
show_description("06_Informed_Detector")

prices_path, trades_path, product = sidebar_data_selector()
if trades_path is None:
    st.warning("No trades file selected."); st.stop()

@st.cache_data
def get_data(pp, tp, prod):
    pr = compute_wall_mid(load_csv_prices(Path(pp), product=prod))
    tr = load_csv_trades(Path(tp), product=prod)
    return pr, tr

prices, trades = get_data(str(prices_path), str(trades_path), product)
qty_list = sorted(trades["quantity"].unique().tolist())
qty_filter = st.sidebar.selectbox("Quantity filter", ["All"] + [int(q) for q in qty_list])

ts_min, ts_max = int(prices["timestamp"].min()), int(prices["timestamp"].max())
time_range = st.sidebar.slider("Time range", ts_min, ts_max, (ts_min, ts_max), step=100)
pf = filter_time_range(prices, time_range[0], time_range[1])
tf = filter_time_range(trades, time_range[0], time_range[1])

ts = pf["timestamp"].values
running_min = pf["wall_mid"].expanding().min()
running_max = pf["wall_mid"].expanding().max()

display = tf if qty_filter == "All" else tf[tf["quantity"] == qty_filter]
label = f" (qty={qty_filter})" if qty_filter != "All" else ""

fig = go.Figure()
fig.add_trace(go.Scattergl(x=ts, y=pf["wall_mid"], mode="lines", connectgaps=False, line=dict(color="#9e9e9e", width=0.8), name="Wall Mid", hovertemplate="t=%{x}<br>%{y:.2f}<extra>Wall Mid</extra>"))
fig.add_trace(go.Scattergl(x=ts, y=running_min, mode="lines", connectgaps=False, line=dict(color="#4fc3f7", dash="dash", width=1), name="Running Min", hovertemplate="t=%{x}<br>%{y:.2f}<extra>Running Min</extra>"))
fig.add_trace(go.Scattergl(x=ts, y=running_max, mode="lines", connectgaps=False, line=dict(color="#ef5350", dash="dash", width=1), name="Running Max", hovertemplate="t=%{x}<br>%{y:.2f}<extra>Running Max</extra>"))
if len(display) > 0:
    fig.add_trace(go.Scattergl(
        x=display["timestamp"], y=display["price"], mode="markers",
        marker=dict(size=5, color=TRADE_COLOR, symbol="triangle-up", line=dict(width=0.5, color="black")),
        name=f"Trades{label}", customdata=display["quantity"].values,
        hovertemplate="t=%{x}<br>price=%{y:.2f}<br>qty=%{customdata}<extra>Trade</extra>",
    ))
fig.update_layout(title=f"Trades vs Daily Extremes - {product}{label}", xaxis_title="Timestamp", yaxis_title="Price", height=550)
st.plotly_chart(apply_crosshair(fig), use_container_width=True)
