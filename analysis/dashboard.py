import streamlit as st

st.set_page_config(
    page_title="IMC Prosperity Analysis",
    page_icon="📊",
    layout="wide",
)

st.title("IMC Prosperity Analysis Dashboard")
st.markdown(
    """
    Interactive analysis toolkit for IMC Prosperity Round 1 data.
    Select a page from the sidebar to begin.

    **Pages:**
    - **Orderbook Explorer** -- Visualize orderbook depth, fair price estimators, spread & edge
    - **Trade Profiler** -- Detect informed traders and bot patterns
    - **Autocorrelation** -- Classify products as mean-reverting, trending, or random walk
    - **Strategy Lab** -- Backtest market-making strategies and optimize parameters
    """
)
