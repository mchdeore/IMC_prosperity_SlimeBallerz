import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import streamlit as st
import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots

from data_loader import load_csv_prices, compute_wall_mid, compute_spread, filter_time_range
from plot_helpers import apply_crosshair, sidebar_data_selector, show_description

st.set_page_config(page_title="Spread & Edge", layout="wide")
st.title("Spread & Edge Analysis")
show_description("03_Spread_Edge")

prices_path, _, product = sidebar_data_selector(need_trades=False)

@st.cache_data
def get_prices(pp, prod):
    return compute_spread(compute_wall_mid(load_csv_prices(Path(pp), product=prod)))

pf = get_prices(str(prices_path), product)
ts_min, ts_max = int(pf["timestamp"].min()), int(pf["timestamp"].max())
time_range = st.sidebar.slider("Time range", ts_min, ts_max, (ts_min, ts_max), step=100)
pf = filter_time_range(pf, time_range[0], time_range[1])
ts = pf["timestamp"].values

fig = make_subplots(rows=2, cols=1, shared_xaxes=True, subplot_titles=["Spread", "Edge"], row_heights=[0.5, 0.5])
fig.add_trace(go.Scattergl(x=ts, y=pf["spread"], mode="lines", connectgaps=False, line=dict(width=0.8, color="#4fc3f7"), name="Spread", hovertemplate="t=%{x}<br>%{y:.2f}<extra>Spread</extra>"), row=1, col=1)
mean_s = pf["spread"].mean()
fig.add_hline(y=mean_s, line_dash="dash", line_color="red", annotation_text=f"mean={mean_s:.1f}", row=1, col=1)
fig.add_trace(go.Scattergl(x=ts, y=pf["buy_edge"], mode="lines", connectgaps=False, line=dict(width=0.8, color="#69f0ae"), name="Buy Edge", hovertemplate="t=%{x}<br>%{y:.2f}<extra>Buy Edge</extra>"), row=2, col=1)
fig.add_trace(go.Scattergl(x=ts, y=pf["sell_edge"], mode="lines", connectgaps=False, line=dict(width=0.8, color="#ef5350"), name="Sell Edge", hovertemplate="t=%{x}<br>%{y:.2f}<extra>Sell Edge</extra>"), row=2, col=1)
fig.add_hline(y=0, line_width=0.5, row=2, col=1)
fig.update_layout(height=520)
st.plotly_chart(apply_crosshair(fig), use_container_width=True)

c1, c2, c3, c4 = st.columns(4)
c1.metric("Avg Spread", f"{pf['spread'].mean():.2f}")
c2.metric("Avg Buy Edge", f"{pf['buy_edge'].mean():.2f}")
c3.metric("Avg Sell Edge", f"{pf['sell_edge'].mean():.2f}")
c4.metric("Min Spread", f"{pf['spread'].min():.2f}")
