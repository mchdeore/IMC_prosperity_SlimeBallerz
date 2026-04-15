import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import streamlit as st
import plotly.graph_objects as go

from data_loader import load_csv_trades
from plot_helpers import apply_crosshair, sidebar_data_selector, show_description

st.set_page_config(page_title="Qty Distribution", layout="wide")
st.title("Trade Quantity Distribution")
show_description("05_Qty_Distribution")

_, trades_path, product = sidebar_data_selector()
if trades_path is None:
    st.warning("No trades file selected."); st.stop()

@st.cache_data
def get_trades(tp, prod):
    return load_csv_trades(Path(tp), product=prod)

trades = get_trades(str(trades_path), product)
qty_list = sorted(trades["quantity"].unique().tolist())

st.markdown(f"**{len(trades)}** trades | quantities: **{qty_list}**")

qty_counts = trades["quantity"].value_counts().sort_index()
col1, col2 = st.columns([2, 1])

with col1:
    fig = go.Figure(go.Bar(x=qty_counts.index.tolist(), y=qty_counts.values.tolist(), marker_color="#4fc3f7", hovertemplate="qty=%{x}<br>count=%{y}<extra></extra>"))
    fig.update_layout(xaxis_title="Trade Quantity", yaxis_title="Count", height=400)
    st.plotly_chart(apply_crosshair(fig), use_container_width=True)

with col2:
    top = trades["quantity"].value_counts().head(10).reset_index()
    top.columns = ["Quantity", "Count"]
    top["Quantity"] = top["Quantity"].astype(int)
    top["Count"] = top["Count"].astype(int)
    top["% of Trades"] = (top["Count"] / len(trades) * 100).round(1)
    st.dataframe(top, use_container_width=True, hide_index=True)
