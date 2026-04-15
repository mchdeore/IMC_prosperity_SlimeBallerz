import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import streamlit as st
import numpy as np
import plotly.graph_objects as go
from plotly.subplots import make_subplots

from data_loader import load_csv_prices, load_csv_trades, compute_wall_mid
from plot_helpers import apply_crosshair, sidebar_data_selector, show_description

st.set_page_config(page_title="Backtest", layout="wide")
st.title("PnL Backtest")
show_description("10_Backtest")

prices_path, trades_path, product = sidebar_data_selector()
if trades_path is None:
    st.warning("No trades file selected."); st.stop()

edge = st.sidebar.number_input("Edge", min_value=0.1, value=2.0, step=0.5)
max_pos = st.sidebar.number_input("Max position", min_value=1, value=50, step=5)
flatten_thresh = st.sidebar.number_input("Flatten threshold", min_value=1, value=40, step=5)
run_grid = st.sidebar.checkbox("Run grid search", False)

@st.cache_data
def get_data(pp, tp, prod):
    return compute_wall_mid(load_csv_prices(Path(pp), product=prod)), load_csv_trades(Path(tp), product=prod)

prices, trades = get_data(str(prices_path), str(trades_path), product)


def backtest(pr, tr, e, mp, ft):
    wm = pr["wall_mid"].values; bb = pr["bid_price_1"].values; ba = pr["ask_price_1"].values; tss = pr["timestamp"].values
    pos = 0; cash = 0.0; nt = 0; pnls = []; positions = []; flattens = []; tal = 0
    for i in range(len(wm)):
        fair = wm[i]
        if np.isnan(fair): pnls.append(cash); positions.append(pos); continue
        if abs(pos) >= mp: tal += 1
        if not np.isnan(ba[i]) and ba[i] <= fair - e and pos < mp:
            q = min(1, mp - pos); cash -= ba[i] * q; pos += q; nt += 1
        if not np.isnan(bb[i]) and bb[i] >= fair + e and pos > -mp:
            q = min(1, mp + pos); cash += bb[i] * q; pos -= q; nt += 1
        for _, t in tr[tr["timestamp"] == tss[i]].iterrows():
            if t["price"] >= fair + e and pos > -mp:
                q = min(int(t["quantity"]), mp + pos)
                if q > 0: cash += (fair + e) * q; pos -= q; nt += 1
            elif t["price"] <= fair - e and pos < mp:
                q = min(int(t["quantity"]), mp - pos)
                if q > 0: cash -= (fair - e) * q; pos += q; nt += 1
        if abs(pos) >= ft:
            cash += pos * fair; flattens.append(tss[i]); pos = 0
        pnls.append(cash + pos * fair); positions.append(pos)
    last = wm[~np.isnan(wm)][-1] if any(~np.isnan(wm)) else 0
    return {"pnl": np.array(pnls), "pos": np.array(positions), "total": cash + pos * last, "n": nt, "flattens": flattens, "tal": tal, "ts": tss}


r = backtest(prices, trades, edge, max_pos, flatten_thresh)
c1, c2, c3, c4, c5 = st.columns(5)
c1.metric("Total PnL", f"{r['total']:.0f}")
c2.metric("Trades", f"{r['n']}")
dd = (r["pnl"] - np.maximum.accumulate(r["pnl"])).min()
c3.metric("Max Drawdown", f"{dd:.0f}")
c4.metric("Flattens", f"{len(r['flattens'])}")
c5.metric("Time@Limit", f"{r['tal'] / max(len(r['ts']), 1) * 100:.1f}%")

fig = make_subplots(rows=2, cols=1, shared_xaxes=True, subplot_titles=["PnL", "Position"], row_heights=[0.6, 0.4])
fig.add_trace(go.Scattergl(x=r["ts"], y=r["pnl"], mode="lines", line=dict(width=1, color="#4fc3f7"), name="PnL", hovertemplate="t=%{x}<br>PnL=%{y:.0f}<extra></extra>"), row=1, col=1)
fig.add_trace(go.Scattergl(x=r["ts"], y=r["pos"], mode="lines", line=dict(width=0.8, color="#ffa726"), name="Position", hovertemplate="t=%{x}<br>pos=%{y}<extra></extra>"), row=2, col=1)
fig.add_hline(y=max_pos, line_dash="dash", line_color="red", row=2, col=1)
fig.add_hline(y=-max_pos, line_dash="dash", line_color="red", row=2, col=1)
fig.add_hline(y=0, line_width=0.5, row=2, col=1)
fig.update_layout(height=500, showlegend=False)
fig.update_xaxes(title_text="Timestamp", row=2, col=1)
st.plotly_chart(apply_crosshair(fig), use_container_width=True)

if run_grid:
    st.subheader("Grid Search")
    grid_edge_max = st.number_input("Grid edge max", min_value=2, value=11, step=1)
    grid_pos_max = st.number_input("Grid pos max", min_value=10, value=55, step=5)
    with st.spinner("Running..."):
        edges = list(range(1, int(grid_edge_max) + 1))
        poss = list(range(10, int(grid_pos_max), 5))
        grid = np.zeros((len(edges), len(poss)))
        for i, e_ in enumerate(edges):
            for j, mp_ in enumerate(poss):
                grid[i, j] = backtest(prices, trades, e_, mp_, mp_)["total"]
        fig_g = go.Figure(go.Heatmap(z=grid, x=[str(p) for p in poss], y=[str(e) for e in edges], colorscale="RdYlGn", text=np.round(grid).astype(int), texttemplate="%{text}", textfont=dict(size=9)))
        fig_g.update_layout(title="PnL Grid", xaxis_title="Max Position", yaxis_title="Edge", height=500)
        st.plotly_chart(apply_crosshair(fig_g), use_container_width=True)
        bi = np.unravel_index(grid.argmax(), grid.shape)
        st.success(f"Best: edge={edges[bi[0]]}, max_pos={poss[bi[1]]}, PnL={grid.max():.0f}")
