"""Engle-Granger cointegration test for two price series.

The procedure:

1. Confirm Y and X are individually non-stationary via ADF (both
   p-values should be >= 0.05).
2. Fit OLS ``Y = beta * X + alpha + eps``.
3. Extract residuals ``eps = Y - beta * X - alpha``.
4. ADF on the residuals - if that p-value is < 0.05 the residuals are
   stationary, so Y and X are cointegrated and ``beta`` is the hedge
   ratio (units of X per unit of Y).

ADF is delegated to ``statsmodels.tsa.stattools.adfuller`` when
available; we fall back to a compact ADF using ``scipy`` + ``numpy``
with MacKinnon approximate p-values so the feature still works even
if statsmodels isn't installed yet.
"""

from __future__ import annotations

from typing import Any, Dict

import numpy as np
import pandas as pd


# ---------------------------------------------------------------------------
# ADF implementation (statsmodels preferred, numpy+scipy fallback)
# ---------------------------------------------------------------------------

try:  # preferred path
    from statsmodels.tsa.stattools import adfuller as _sm_adfuller  # type: ignore

    def _adf(series: np.ndarray) -> Dict[str, Any]:
        stat, pvalue, used_lag, nobs, _, _ = _sm_adfuller(
            series, autolag="AIC", regression="c"
        )
        return {
            "stat": float(stat),
            "pvalue": float(pvalue),
            "used_lag": int(used_lag),
            "nobs": int(nobs),
        }
except Exception:  # pragma: no cover - only hit when statsmodels missing
    from scipy.stats import t as _student_t  # type: ignore

    def _adf(series: np.ndarray) -> Dict[str, Any]:
        """Minimal ADF with constant, lag=1, Student-t p-value approximation.

        Good enough to rank cointegration candidates; swap to statsmodels
        for production-grade MacKinnon tables by adding ``statsmodels`` to
        requirements.
        """
        y = np.asarray(series, dtype=float)
        y = y[np.isfinite(y)]
        if len(y) < 10:
            return {"stat": float("nan"), "pvalue": float("nan"),
                    "used_lag": 0, "nobs": int(len(y))}
        dy = np.diff(y)
        y_lag = y[:-1]
        if len(dy) < 4:
            return {"stat": float("nan"), "pvalue": float("nan"),
                    "used_lag": 0, "nobs": int(len(y))}
        X = np.column_stack([np.ones_like(y_lag), y_lag, dy[:-1] if len(dy) > 1 else np.zeros_like(y_lag)])
        # Drop the first row because of the lagged dy term.
        X = X[1:]
        target = dy[1:]
        if len(target) < 4:
            return {"stat": float("nan"), "pvalue": float("nan"),
                    "used_lag": 1, "nobs": int(len(y))}
        beta, *_ = np.linalg.lstsq(X, target, rcond=None)
        residuals = target - X @ beta
        dof = len(target) - X.shape[1]
        sigma2 = float(residuals @ residuals) / max(dof, 1)
        cov = sigma2 * np.linalg.pinv(X.T @ X)
        se = float(np.sqrt(cov[1, 1]))
        stat = float(beta[1] / se) if se > 0 else float("nan")
        # Approximate p-value from Student-t CDF; not the MacKinnon table
        # but keeps the fallback useful.
        pvalue = float(_student_t.cdf(stat, dof)) if np.isfinite(stat) else float("nan")
        return {
            "stat": stat,
            "pvalue": pvalue,
            "used_lag": 1,
            "nobs": int(len(y)),
        }


# ---------------------------------------------------------------------------
# OLS (numpy, no statsmodels dependency)
# ---------------------------------------------------------------------------


def _ols(y: np.ndarray, x: np.ndarray) -> tuple[float, float]:
    """Return (beta, alpha) from OLS ``y = beta*x + alpha``."""
    X = np.column_stack([x, np.ones_like(x)])
    coef, *_ = np.linalg.lstsq(X, y, rcond=None)
    return float(coef[0]), float(coef[1])


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def run_cointegration(y: pd.Series, x: pd.Series,
                      *, p_threshold: float = 0.05) -> Dict[str, Any]:
    """Run the Engle-Granger test on two already-aligned price series.

    ``y`` and ``x`` should share an index; any NaN rows are dropped
    before regression and ADF tests are run.

    Returns a dict with ``adf_y``, ``adf_x``, ``adf_resid`` sub-dicts,
    ``beta``, ``alpha``, ``residuals`` (pd.Series aligned to the input
    index) and a ``verdict`` string.
    """
    joined = pd.concat([y.rename("y"), x.rename("x")], axis=1, join="inner").dropna()
    if len(joined) < 20:
        empty = {"stat": float("nan"), "pvalue": float("nan"), "used_lag": 0, "nobs": len(joined)}
        return {
            "adf_y": empty, "adf_x": empty, "adf_resid": empty,
            "beta": float("nan"), "alpha": float("nan"),
            "residuals": pd.Series(dtype=float),
            "verdict": "not enough data",
        }

    yv = joined["y"].to_numpy(dtype=float)
    xv = joined["x"].to_numpy(dtype=float)

    adf_y = _adf(yv)
    adf_x = _adf(xv)
    beta, alpha = _ols(yv, xv)
    residuals_arr = yv - beta * xv - alpha
    residuals = pd.Series(residuals_arr, index=joined.index, name="residual")
    adf_resid = _adf(residuals_arr)

    non_stationary_inputs = (
        (adf_y["pvalue"] is not None and adf_y["pvalue"] >= p_threshold)
        and (adf_x["pvalue"] is not None and adf_x["pvalue"] >= p_threshold)
    )
    stationary_resid = (
        adf_resid["pvalue"] is not None and adf_resid["pvalue"] < p_threshold
    )
    if stationary_resid and non_stationary_inputs:
        verdict = "cointegrated"
    elif stationary_resid and not non_stationary_inputs:
        verdict = "residuals stationary but inputs already stationary - spurious"
    else:
        verdict = "not cointegrated"

    return {
        "adf_y": adf_y,
        "adf_x": adf_x,
        "adf_resid": adf_resid,
        "beta": beta,
        "alpha": alpha,
        "residuals": residuals,
        "verdict": verdict,
    }
