import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import streamlit as st
import numpy as np
import pandas as pd
import plotly.graph_objects as go

from data_loader import load_csv_prices, compute_wall_mid, compute_raw_mid, filter_time_range
from plot_helpers import apply_crosshair, sidebar_data_selector, show_description, WALL_MID_COLOR, RAW_MID_COLOR, CSV_MID_COLOR

st.set_page_config(page_title="Fair Price", layout="wide")
st.title("Fair Price Comparison")
show_description("02_Fair_Price")

prices_path, _, product = sidebar_data_selector(need_trades=False)

@st.cache_data
def get_prices(pp, prod):
    return compute_raw_mid(compute_wall_mid(load_csv_prices(Path(pp), product=prod)))

prices = get_prices(str(prices_path), product)
ts_min, ts_max = int(prices["timestamp"].min()), int(prices["timestamp"].max())
time_range = st.sidebar.slider("Time range", ts_min, ts_max, (ts_min, ts_max), step=100)
pf = filter_time_range(prices, time_range[0], time_range[1])
ts = pf["timestamp"].values

fig = go.Figure()
for col, color, name, dash in [("wall_mid", WALL_MID_COLOR, "Wall Mid", None), ("raw_mid", RAW_MID_COLOR, "Raw Mid", None), ("mid_price", CSV_MID_COLOR, "CSV Mid", "dot")]:
    fig.add_trace(go.Scattergl(x=ts, y=pf[col], mode="lines", connectgaps=False, line=dict(color=color, width=1.5 if col == "wall_mid" else 1, dash=dash), name=name, hovertemplate="t=%{x}<br>%{y:.2f}<extra>" + name + "</extra>"))
fig.update_layout(title=f"Fair Price Estimators - {product}", xaxis_title="Timestamp", yaxis_title="Price", height=420)
st.plotly_chart(apply_crosshair(fig), use_container_width=True)

st.subheader("Divergence (Raw Mid - Wall Mid)")
div = pf["raw_mid"] - pf["wall_mid"]
fig_d = go.Figure()
fig_d.add_trace(go.Scattergl(x=ts, y=div, mode="lines", connectgaps=False, line=dict(color="#ef5350", width=0.8), name="Divergence", hovertemplate="t=%{x}<br>%{y:.3f}<extra>Div</extra>"))
fig_d.add_hline(y=0, line_width=0.5)
fig_d.update_layout(xaxis_title="Timestamp", yaxis_title="Raw Mid - Wall Mid", height=280)
st.plotly_chart(apply_crosshair(fig_d), use_container_width=True)

dc1, dc2, dc3, dc4 = st.columns(4)
dc1.metric("Mean", f"{div.mean():.3f}")
dc2.metric("Std", f"{div.std():.3f}")
dc3.metric("Max", f"{div.max():.3f}")
dc4.metric("|Div|>1 %", f"{(div.abs() > 1).mean() * 100:.1f}%")
