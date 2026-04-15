import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import streamlit as st
import numpy as np
import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots
from scipy import stats as sp_stats

from data_loader import load_csv_prices, compute_wall_mid
from plot_helpers import apply_crosshair, sidebar_data_selector, show_description

st.set_page_config(page_title="Frequency", layout="wide")
st.title("Frequency & Distribution Analysis")
show_description("13_Frequency")

prices_path, _, product = sidebar_data_selector(need_trades=False)
rolling_vol_window = st.sidebar.number_input("Rolling vol window", min_value=5, value=50, step=5)

@st.cache_data
def get_mid(pp, prod):
    pr = compute_wall_mid(load_csv_prices(Path(pp), product=prod))
    wm = pr["wall_mid"].dropna()
    return wm.values, pr.loc[wm.index, "timestamp"].values

mid, timestamps = get_mid(str(prices_path), product)
returns = mid[1:] - mid[:-1]
ret_ts = timestamps[1:]

st.markdown(f"**{len(returns)}** returns | mean **{returns.mean():.4f}** | std **{returns.std():.4f}**")

# ── FFT Power Spectrum ────────────────────────────────────────────────────────

st.subheader("Power Spectrum (FFT)")

fft_vals = np.fft.rfft(returns - returns.mean())
power = np.abs(fft_vals) ** 2
freqs = np.fft.rfftfreq(len(returns), d=1.0)
periods = np.where(freqs > 0, 1.0 / freqs, np.nan)

valid = freqs > 0
fig_fft = go.Figure()
fig_fft.add_trace(go.Scattergl(
    x=periods[valid], y=power[valid], mode="lines",
    line=dict(color="#4fc3f7", width=0.8), name="Power",
    hovertemplate="period=%{x:.1f} steps<br>power=%{y:.1f}<extra></extra>",
))
fig_fft.update_layout(
    title="Power Spectral Density",
    xaxis_title="Period (timesteps)", yaxis_title="Power",
    xaxis_type="log", yaxis_type="log", height=380,
)
st.plotly_chart(apply_crosshair(fig_fft), use_container_width=True)

top_n = 5
top_idx = np.argsort(power[valid])[-top_n:][::-1]
top_periods = periods[valid][top_idx]
top_power = power[valid][top_idx]
st.markdown("**Dominant periods:**")
tcols = st.columns(top_n)
for i in range(top_n):
    tcols[i].metric(f"#{i+1}", f"{top_periods[i]:.0f} steps", f"power={top_power[i]:.0f}")

# ── Rolling Volatility ────────────────────────────────────────────────────────

st.subheader("Rolling Volatility")

ret_series = pd.Series(returns)
rolling_vol = ret_series.rolling(rolling_vol_window).std()

fig_vol = go.Figure()
fig_vol.add_trace(go.Scattergl(
    x=ret_ts, y=rolling_vol.values, mode="lines", connectgaps=False,
    line=dict(color="#ffa726", width=0.8), name="Rolling Std",
    hovertemplate="t=%{x}<br>vol=%{y:.4f}<extra></extra>",
))
fig_vol.update_layout(
    title=f"Rolling Std Dev (window={rolling_vol_window})",
    xaxis_title="Timestamp", yaxis_title="Std Dev of Returns", height=320,
)
st.plotly_chart(apply_crosshair(fig_vol), use_container_width=True)

vc1, vc2, vc3 = st.columns(3)
vc1.metric("Mean Vol", f"{rolling_vol.mean():.4f}")
vc2.metric("Max Vol", f"{rolling_vol.max():.4f}")
vc3.metric("Min Vol", f"{rolling_vol.min():.4f}")

# ── Return Distribution ───────────────────────────────────────────────────────

st.subheader("Return Distribution")

fig_dist = make_subplots(rows=1, cols=2, subplot_titles=["Histogram", "QQ Plot"])

fig_dist.add_trace(go.Histogram(
    x=returns, nbinsx=80, marker_color="#4fc3f7", opacity=0.7,
    name="Returns", hovertemplate="return=%{x:.2f}<br>count=%{y}<extra></extra>",
), row=1, col=1)

# Normal overlay
x_norm = np.linspace(returns.min(), returns.max(), 200)
y_norm = sp_stats.norm.pdf(x_norm, returns.mean(), returns.std()) * len(returns) * (returns.max() - returns.min()) / 80
fig_dist.add_trace(go.Scatter(
    x=x_norm, y=y_norm, mode="lines", line=dict(color="#ef5350", width=1.5, dash="dash"), name="Normal",
), row=1, col=1)

# QQ plot
sorted_ret = np.sort(returns)
theoretical = sp_stats.norm.ppf(np.linspace(0.001, 0.999, len(sorted_ret)))
fig_dist.add_trace(go.Scattergl(
    x=theoretical, y=sorted_ret, mode="markers",
    marker=dict(size=2, color="#4fc3f7"), name="QQ",
    hovertemplate="theoretical=%{x:.2f}<br>actual=%{y:.2f}<extra></extra>",
), row=1, col=2)
qq_min = min(theoretical.min(), sorted_ret.min())
qq_max = max(theoretical.max(), sorted_ret.max())
fig_dist.add_trace(go.Scatter(
    x=[qq_min, qq_max], y=[qq_min, qq_max], mode="lines",
    line=dict(color="#ef5350", dash="dash", width=1), showlegend=False,
), row=1, col=2)

fig_dist.update_layout(height=380)
fig_dist.update_xaxes(title_text="Return", row=1, col=1)
fig_dist.update_xaxes(title_text="Theoretical Quantile", row=1, col=2)
fig_dist.update_yaxes(title_text="Count", row=1, col=1)
fig_dist.update_yaxes(title_text="Actual Return", row=1, col=2)
st.plotly_chart(apply_crosshair(fig_dist), use_container_width=True)

dc1, dc2, dc3, dc4 = st.columns(4)
dc1.metric("Skewness", f"{sp_stats.skew(returns):.4f}")
dc2.metric("Kurtosis", f"{sp_stats.kurtosis(returns):.4f}")
dc3.metric("Jarque-Bera p", f"{sp_stats.jarque_bera(returns).pvalue:.4f}")
dc4.metric("Shapiro p", f"{sp_stats.shapiro(returns[:5000]).pvalue:.4f}" if len(returns) > 0 else "N/A")
