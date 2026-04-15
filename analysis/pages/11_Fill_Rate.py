import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import streamlit as st
import numpy as np
import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots

from data_loader import load_csv_prices, load_csv_trades, compute_wall_mid
from plot_helpers import apply_crosshair, sidebar_data_selector, show_description

st.set_page_config(page_title="Fill Rate", layout="wide")
st.title("Fill Rate vs Edge")
show_description("11_Fill_Rate")

prices_path, trades_path, product = sidebar_data_selector()
if trades_path is None:
    st.warning("No trades file selected."); st.stop()

max_edge = st.sidebar.number_input("Max edge to test", min_value=2, value=15, step=1)

@st.cache_data
def get_data(pp, tp, prod):
    return compute_wall_mid(load_csv_prices(Path(pp), product=prod)), load_csv_trades(Path(tp), product=prod)

@st.cache_data
def fill_rate_curve(pp, tp, prod, me):
    pr, tr = get_data(pp, tp, prod)
    wm_s = pr.set_index("timestamp")["wall_mid"]
    rows = []
    for e in range(1, int(me) + 1):
        bf = af = steps = 0
        for ts_val in pr["timestamp"].unique():
            wm = wm_s.get(ts_val, np.nan)
            if np.isnan(wm): continue
            steps += 1
            for _, t in tr[tr["timestamp"] == ts_val].iterrows():
                if t["price"] >= wm + e: af += 1
                if t["price"] <= wm - e: bf += 1
        total = bf + af
        fr = total / steps if steps > 0 else 0
        rows.append({"Edge": e, "Bid Fills": bf, "Ask Fills": af, "Total Fills": total, "Fill Rate %": round(fr * 100, 2), "EV/step": round(fr * e, 4)})
    return pd.DataFrame(rows)

fr_df = fill_rate_curve(str(prices_path), str(trades_path), product, max_edge)

fig = make_subplots(rows=1, cols=2, subplot_titles=["Fill Rate %", "Expected Value / Step"])
fig.add_trace(go.Bar(x=fr_df["Edge"], y=fr_df["Fill Rate %"], marker_color="#4fc3f7", hovertemplate="edge=%{x}<br>fill=%{y:.2f}%<extra></extra>"), row=1, col=1)
fig.add_trace(go.Bar(x=fr_df["Edge"], y=fr_df["EV/step"], marker_color="#69f0ae", hovertemplate="edge=%{x}<br>EV=%{y:.4f}<extra></extra>"), row=1, col=2)
best_e = fr_df.loc[fr_df["EV/step"].idxmax(), "Edge"]
fig.add_vline(x=best_e, line_dash="dash", line_color="red", annotation_text=f"optimal={best_e}", row=1, col=2)
fig.update_layout(height=400, showlegend=False)
fig.update_xaxes(title_text="Edge", row=1, col=1)
fig.update_xaxes(title_text="Edge", row=1, col=2)
st.plotly_chart(apply_crosshair(fig), use_container_width=True)

st.dataframe(fr_df, use_container_width=True, hide_index=True)
