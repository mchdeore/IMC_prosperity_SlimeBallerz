import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import streamlit as st
import numpy as np
import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots

from data_loader import load_csv_prices, compute_wall_mid
from plot_helpers import apply_crosshair, sidebar_data_selector, show_description

st.set_page_config(page_title="Autocorrelation", layout="wide")
st.title("Autocorrelation Analysis")
show_description("09_Autocorrelation")

prices_path, _, product = sidebar_data_selector(need_trades=False)
horizons = st.sidebar.multiselect("Return horizons", [1, 2, 5, 10, 20], default=[1, 2, 5, 10])
max_lag = st.sidebar.number_input("Max lag", min_value=1, value=20, step=1)
n_sims = st.sidebar.number_input("MC simulations", min_value=50, value=500, step=50)
rolling_window = st.sidebar.number_input("Rolling window", min_value=10, value=200, step=10)


def autocorrelation(series, ml):
    n = len(series); mean = np.mean(series); var = np.var(series)
    if var == 0: return np.zeros(ml)
    return np.array([np.mean((series[:n - lag] - mean) * (series[lag:] - mean)) / var for lag in range(1, ml + 1)])


@st.cache_data
def get_mid(pp, prod):
    pr = compute_wall_mid(load_csv_prices(Path(pp), product=prod))
    wm = pr["wall_mid"].dropna()
    return wm.values, pr["timestamp"].values[:len(wm)]

mid, timestamps = get_mid(str(prices_path), product)
st.markdown(f"**{len(mid)}** points | range **{mid.min():.1f}** - **{mid.max():.1f}**")

if horizons:
    st.subheader("AC by Return Horizon")
    fig_ac = make_subplots(rows=1, cols=len(horizons), subplot_titles=[f"H={h}" for h in horizons], shared_yaxes=True)
    for h_idx, horizon in enumerate(horizons):
        col = h_idx + 1
        returns = mid[horizon:] - mid[:-horizon]
        ac = autocorrelation(returns, max_lag)
        mc_acs = np.zeros((n_sims, max_lag))
        step_std = np.std(np.diff(mid))
        for sim in range(n_sims):
            rw = np.cumsum(np.random.randn(len(mid)) * step_std)
            mc_acs[sim] = autocorrelation(rw[horizon:] - rw[:-horizon], max_lag)
        lower = np.percentile(mc_acs, 2.5, axis=0)
        upper = np.percentile(mc_acs, 97.5, axis=0)
        lags = np.arange(1, max_lag + 1)
        fig_ac.add_trace(go.Scatter(x=np.concatenate([lags, lags[::-1]]), y=np.concatenate([upper, lower[::-1]]), fill="toself", fillcolor="rgba(180,180,180,0.2)", line=dict(width=0), name="95% CI" if h_idx == 0 else None, showlegend=h_idx == 0), row=1, col=col)
        fig_ac.add_trace(go.Bar(x=lags, y=ac, marker_color="#4fc3f7", name="Actual" if h_idx == 0 else None, showlegend=h_idx == 0, hovertemplate="lag=%{x}<br>AC=%{y:.4f}<extra></extra>"), row=1, col=col)
        fig_ac.add_hline(y=0, line_width=0.5, row=1, col=col)
    fig_ac.update_layout(height=380)
    fig_ac.update_xaxes(title_text="Lag", row=1, col=1)
    fig_ac.update_yaxes(title_text="AC", row=1, col=1)
    st.plotly_chart(apply_crosshair(fig_ac), use_container_width=True)

    lag1_cols = st.columns(len(horizons))
    for i, h in enumerate(horizons):
        ret = mid[h:] - mid[:-h]
        ac1 = autocorrelation(ret, 1)[0]
        lag1_cols[i].metric(f"Lag-1 (H={h})", f"{ac1:+.4f}")

st.subheader("Rolling Lag-1 AC")
returns_1 = pd.Series(mid[1:] - mid[:-1])
rolling_ac = returns_1.rolling(rolling_window).apply(lambda x: x.autocorr(lag=1) if len(x) > 1 else np.nan, raw=False)
fig_r = go.Figure()
fig_r.add_trace(go.Scattergl(x=timestamps[1:], y=rolling_ac.values, mode="lines", connectgaps=False, line=dict(width=0.8, color="#4fc3f7"), name="Rolling AC", hovertemplate="t=%{x}<br>AC=%{y:.4f}<extra></extra>"))
ref = 1 / np.sqrt(rolling_window)
fig_r.add_hline(y=0, line_width=0.5)
fig_r.add_hline(y=ref, line_dash="dash", line_color="#666")
fig_r.add_hline(y=-ref, line_dash="dash", line_color="#666")
fig_r.update_layout(title=f"Rolling AC (w={rolling_window})", xaxis_title="Timestamp", yaxis_title="Lag-1 AC", height=340)
st.plotly_chart(apply_crosshair(fig_r), use_container_width=True)
