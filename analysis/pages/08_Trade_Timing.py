import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import streamlit as st
import pandas as pd
import plotly.graph_objects as go

from data_loader import load_csv_trades, discover_csv_files
from plot_helpers import apply_crosshair, sidebar_data_selector, show_description

st.set_page_config(page_title="Trade Timing", layout="wide")
st.title("Trade Timing Patterns")
show_description("08_Trade_Timing")

_, trades_path, product = sidebar_data_selector()
if trades_path is None:
    st.warning("No trades file selected."); st.stop()

@st.cache_data
def get_trades(tp, prod):
    return load_csv_trades(Path(tp), product=prod)

trades = get_trades(str(trades_path), product)
top_5 = trades["quantity"].value_counts().head(5).index.tolist()

fig = go.Figure()
for q in top_5:
    subset = trades[trades["quantity"] == q]
    fig.add_trace(go.Histogram(x=subset["timestamp"], nbinsx=50, name=f"qty={int(q)} (n={len(subset)})", opacity=0.6))
fig.update_layout(barmode="overlay", xaxis_title="Timestamp", yaxis_title="Count", title=f"Trade Timing - {product}", height=420)
st.plotly_chart(apply_crosshair(fig), use_container_width=True)

st.subheader("Cross-Day Consistency")
root = Path(__file__).resolve().parent.parent.parent
dirs = discover_csv_files(root)
sel_dir = list(dirs.keys())[0] if dirs else None

if sel_dir:
    trade_files = sorted(dirs[sel_dir]["trades"])
    rows = []
    for tf_name in trade_files:
        t = load_csv_trades(root / sel_dir / tf_name, product=product)
        qty_unique = sorted(t["quantity"].unique().tolist())
        top5 = {int(k): int(v) for k, v in t["quantity"].value_counts().head(5).items()}
        rows.append({"File": tf_name, "Total Trades": len(t), "Unique Quantities": str(qty_unique), "Top 5 (qty: count)": str(top5)})
    st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)
