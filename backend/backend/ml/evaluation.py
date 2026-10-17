"""
Scoring rules shared by the three engines' accuracy harnesses.

Every claim that one model is "more accurate" than another in this project
is made with one of these, out of sample:

  crps_ensemble   distribution forecasts (ARISE): lower is better
  pit             where the realised value fell in the forecast distribution;
                  calibrated forecasts give uniform PITs
  qlike           variance forecasts (Risk): robust to noise in the proxy
                  (Patton 2011), lower is better
  fz0_loss        joint VaR/ES scoring function (Fissler-Ziegel FZ0, Patton,
                  Ziegel & Chen 2019), lower is better
  oos_r2          Campbell-Thompson out-of-sample R^2 vs a benchmark forecast
"""

from __future__ import annotations

import numpy as np


def crps_ensemble(samples: np.ndarray, y: float) -> float:
    """CRPS of an ensemble forecast: E|X - y| - 0.5 E|X - X'| (sorted O(n log n))."""
    x = np.sort(np.asarray(samples, dtype=float))
    n = len(x)
    term1 = np.abs(x - y).mean()
    # E|X - X'| via the sorted-sample identity.
    i = np.arange(1, n + 1)
    term2 = (2 * (2 * i - n - 1) * x).sum() / (n * n)
    return float(term1 - 0.5 * term2)


def pit(samples: np.ndarray, y: float) -> float:
    return float((np.asarray(samples) <= y).mean())


def coverage(pits: np.ndarray, lo: float, hi: float) -> float:
    p = np.asarray(pits)
    return float(((p >= lo) & (p <= hi)).mean())


def qlike(forecast_var: np.ndarray, realised_var: np.ndarray) -> float:
    f = np.maximum(np.asarray(forecast_var, float), 1e-12)
    r = np.maximum(np.asarray(realised_var, float), 1e-12)
    return float(np.mean(r / f - np.log(r / f) - 1))


def fz0_loss(y: np.ndarray, var: np.ndarray, es: np.ndarray, alpha: float) -> float:
    """FZ0 loss for (VaR, ES) given as RETURN quantiles (negative numbers for
    losses), alpha = tail probability (0.05 for 95% VaR)."""
    y, v, e = (np.asarray(a, float) for a in (y, var, es))
    e = np.minimum(e, -1e-9)  # FZ0 requires ES < 0
    hit = (y <= v).astype(float)
    return float(np.mean(-hit * (v - y) / (alpha * e) + v / e + np.log(-e) - 1))


def oos_r2(y: np.ndarray, pred: np.ndarray, bench: np.ndarray) -> float:
    y, p, b = (np.asarray(a, float) for a in (y, pred, bench))
    return float(1 - ((y - p) ** 2).sum() / ((y - b) ** 2).sum())


def ks_uniform(pits: np.ndarray) -> float:
    """Kolmogorov-Smirnov distance of PITs from uniform (0 = perfect)."""
    p = np.sort(np.asarray(pits))
    n = len(p)
    i = np.arange(1, n + 1)
    return float(max((i / n - p).max(), (p - (i - 1) / n).max()))
