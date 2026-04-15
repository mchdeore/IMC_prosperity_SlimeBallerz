import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import streamlit as st
import numpy as np
import plotly.graph_objects as go
import plotly.express as px

from data_loader import load_csv_prices, load_csv_trades, compute_wall_mid, merge_trades_with_prices
from plot_helpers import apply_crosshair, sidebar_data_selector, show_description

st.set_page_config(page_title="Trade Edge", layout="wide")
st.title("Trade Edge by Quantity")
show_description("07_Trade_Edge")

prices_path, trades_path, product = sidebar_data_selector()
if trades_path is None:
    st.warning("No trades file selected."); st.stop()

@st.cache_data
def get_data(pp, tp, prod):
    pr = compute_wall_mid(load_csv_prices(Path(pp), product=prod))
    tr = load_csv_trades(Path(tp), product=prod)
    return merge_trades_with_prices(pr, tr)

merged = get_data(str(prices_path), str(trades_path), product)
merged["edge"] = merged["price"] - (merged["wall_mid"] if "wall_mid" in merged.columns else merged["mid_price"])
qty_list = sorted(merged["quantity"].unique().tolist())

col_a, col_b = st.columns(2)
with col_a:
    fig = go.Figure()
    fig.add_trace(go.Scattergl(
        x=merged["timestamp"], y=merged["edge"], mode="markers",
        marker=dict(size=4, color=merged["quantity"], colorscale="Viridis", showscale=True, colorbar=dict(title="Qty")),
        hovertemplate="t=%{x}<br>edge=%{y:.2f}<br>qty=%{marker.color:.0f}<extra></extra>",
    ))
    fig.add_hline(y=0, line_dash="dash", line_color="orange")
    fig.update_layout(title="Edge Over Time", xaxis_title="Timestamp", yaxis_title="Price - Wall Mid", height=450)
    st.plotly_chart(apply_crosshair(fig), use_container_width=True)

with col_b:
    if len(qty_list) <= 20:
        fig_box = px.box(merged, x="quantity", y="edge", labels={"edge": "Price - Wall Mid", "quantity": "Quantity"}, title="Edge Distribution by Quantity", template="plotly_dark")
        fig_box.add_hline(y=0, line_dash="dash", line_color="orange")
        fig_box.update_layout(height=450)
        st.plotly_chart(apply_crosshair(fig_box), use_container_width=True)
    else:
        st.info(f"Too many unique quantities ({len(qty_list)}) for box plot.")
