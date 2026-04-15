import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import streamlit as st
import numpy as np
import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots

from data_loader import load_csv_prices, load_csv_trades, compute_wall_mid, discover_csv_files
from plot_helpers import apply_crosshair, sidebar_data_selector, show_description

st.set_page_config(page_title="Aggressiveness", layout="wide")
st.title("Aggressiveness Comparison")
show_description("12_Aggressiveness")

prices_path, trades_path, product = sidebar_data_selector()
if trades_path is None:
    st.warning("No trades file selected."); st.stop()

max_pos_input = st.sidebar.number_input("Max position for presets", min_value=1, value=50, step=5)
flatten_input = st.sidebar.number_input("Flatten threshold for presets", min_value=1, value=40, step=5)

@st.cache_data
def get_data(pp, tp, prod):
    return compute_wall_mid(load_csv_prices(Path(pp), product=prod)), load_csv_trades(Path(tp), product=prod)


def sim_inventory(pr, tr, e, mp, ft):
    wm = pr["wall_mid"].values; tss = pr["timestamp"].values
    pos = 0; cash = 0.0; positions = []; pnls = []; flattens = []; tal = 0
    for i in range(len(wm)):
        fair = wm[i]
        if np.isnan(fair): positions.append(pos); pnls.append(cash); continue
        if abs(pos) >= mp: tal += 1
        for _, t in tr[tr["timestamp"] == tss[i]].iterrows():
            if t["price"] >= fair + e and pos > -mp:
                q = min(int(t["quantity"]), mp + pos)
                if q > 0: cash += (fair + e) * q; pos -= q
            elif t["price"] <= fair - e and pos < mp:
                q = min(int(t["quantity"]), mp - pos)
                if q > 0: cash -= (fair - e) * q; pos += q
        if abs(pos) >= ft:
            cash += pos * fair; flattens.append(tss[i]); pos = 0
        positions.append(pos); pnls.append(cash + pos * fair)
    last = wm[~np.isnan(wm)][-1] if any(~np.isnan(wm)) else 0
    return {"total": cash + pos * last, "flattens": len(flattens), "tal": tal, "n": len(tss), "pos": np.array(positions)}


prices, trades = get_data(str(prices_path), str(trades_path), product)

configs = [
    {"Setting": "Conservative", "edge": 4, "max_pos": max_pos_input, "flatten": flatten_input},
    {"Setting": "Moderate", "edge": 3, "max_pos": max_pos_input, "flatten": flatten_input},
    {"Setting": "Aggressive", "edge": 2, "max_pos": max_pos_input, "flatten": flatten_input},
    {"Setting": "Very Aggressive", "edge": 1, "max_pos": max_pos_input, "flatten": flatten_input},
]

rows = []
for cfg in configs:
    r = sim_inventory(prices, trades, cfg["edge"], cfg["max_pos"], cfg["flatten"])
    rows.append({
        "Setting": cfg["Setting"], "Edge": cfg["edge"],
        "PnL": round(r["total"]), "Flattens": r["flattens"],
        "Avg |Pos|": round(np.mean(np.abs(r["pos"])), 1),
        "Time@Limit %": round(r["tal"] / max(r["n"], 1) * 100, 1),
    })
comp = pd.DataFrame(rows)
st.dataframe(comp, use_container_width=True, hide_index=True)

fig = make_subplots(rows=1, cols=3, subplot_titles=["PnL", "Flattens", "Time@Limit %"])
fig.add_trace(go.Bar(x=comp["Setting"], y=comp["PnL"], marker_color="#69f0ae", hovertemplate="%{x}<br>PnL=%{y}<extra></extra>"), row=1, col=1)
fig.add_trace(go.Bar(x=comp["Setting"], y=comp["Flattens"], marker_color="#ffa726", hovertemplate="%{x}<br>flattens=%{y}<extra></extra>"), row=1, col=2)
fig.add_trace(go.Bar(x=comp["Setting"], y=comp["Time@Limit %"], marker_color="#ef5350", hovertemplate="%{x}<br>%{y:.1f}%<extra></extra>"), row=1, col=3)
fig.update_layout(height=340, showlegend=False)
st.plotly_chart(apply_crosshair(fig), use_container_width=True)

st.subheader("Cross-Day Stability")
root = Path(__file__).resolve().parent.parent.parent
dirs = discover_csv_files(root)
sel_dir = list(dirs.keys())[0] if dirs else None
edge_sel = st.number_input("Edge for stability check", min_value=0.1, value=2.0, step=0.5)

if sel_dir:
    price_files = sorted(dirs[sel_dir]["prices"])
    trade_files = sorted(dirs[sel_dir]["trades"])
    stab_rows = []
    for pf_name, tf_name in zip(price_files, trade_files):
        pr = compute_wall_mid(load_csv_prices(root / sel_dir / pf_name, product=product))
        tr = load_csv_trades(root / sel_dir / tf_name, product=product)
        r = sim_inventory(pr, tr, edge_sel, max_pos_input, flatten_input)
        stab_rows.append({"File": pf_name, "PnL": round(r["total"])})
    if stab_rows:
        sdf = pd.DataFrame(stab_rows)
        sdf.loc[len(sdf)] = {"File": "Avg", "PnL": round(sdf["PnL"].mean())}
        sdf.loc[len(sdf)] = {"File": "Std", "PnL": round(sdf["PnL"].iloc[:-1].std())}
        st.dataframe(sdf, use_container_width=True, hide_index=True)
