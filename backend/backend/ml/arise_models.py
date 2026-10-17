"""
ARISE's dedicated models (trained in scripts/train_models.py).

Quant -- valuation-aware equity prior
    Forward 3-5y Nifty total return regressed on the Nifty 50 DIVIDEND yield
    (official NSE series, 1999-). Out of sample the dividend yield beat the
    historical mean by R^2 ~ +0.45 at both 3y and 5y, while the earnings
    yield did not (COVID earnings collapse, and NSE's 2021 switch from
    standalone to consolidated EPS, both distort P/E). With only ~6
    independent 3-year windows in the sample, the forecast is shrunk with
    the same evidence weight ARISE already uses for fund returns,
    n/(n + tau), tau = 10 years -- and applies only to the first five years
    of a projection before reverting to the long-run prior.

Calibration -- the return-shock model
    Three-year fund-return forecasts (every equity fund, quarterly origins
    2016-2023) are scored with CRPS and PIT coverage. That harness showed the
    old AR(1) phi = -0.15 made multi-year ranges too narrow (68.7% inside a
    nominal 80% band); the variance ratios of 29 years of Sensex returns do
    not distinguish mean reversion from noise. ARISE now uses phi = 0.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from backend.ml import data

TAU_YEARS = 10.0  # same evidence weight as portfolio_statistics
VALUATION_YEARS = 5  # horizon over which valuation is allowed to matter
LONG_RUN_EQUITY = 0.11  # the existing labelled long-run assumption


def valuation_frame() -> pd.DataFrame:
    v = pd.read_csv(data.HISTORY / "nifty_valuation_monthly.csv", index_col=0, parse_dates=True)
    v.index = v.index.to_period("M").to_timestamp("M")
    px = np.exp(data.sensex_monthly_log_returns().cumsum())
    px.index = px.index.to_period("M").to_timestamp("M")
    return v.join(px.rename("px"), how="inner")


def forward_total_return(df: pd.DataFrame, months: int) -> pd.Series:
    """Annualised forward log total return ~ price change + dividends paid."""
    yrs = months / 12
    price = np.log(df.px.shift(-months) / df.px)
    divs = df.div_yield.rolling(months).mean().shift(-months) / 100 * yrs
    return (price + divs) / yrs


def fit_dividend_yield_model(months: int = 60) -> dict:
    df = valuation_frame()
    df = df.assign(fwd=forward_total_return(df, months)).dropna()
    X = np.column_stack([np.ones(len(df)), df.div_yield])
    b = np.linalg.lstsq(X, df.fwd.values, rcond=None)[0]
    return {
        "intercept": float(b[0]),
        "slope_per_pct_dy": float(b[1]),
        "hist_mean": float(df.fwd.mean()),
        "n_months": len(df),
        "independent_windows": round(len(df) / months, 1),
    }


def rolling_oos(months: int) -> dict:
    """Expanding-window, look-ahead-free evaluation of the DY forecast."""
    from backend.ml.evaluation import oos_r2

    df = valuation_frame()
    df = df.assign(fwd=forward_total_return(df, months)).dropna()
    ys, p, h = [], [], []
    for t in df.index[df.index >= df.index[0] + pd.DateOffset(years=6)]:
        tr = df[df.index <= t - pd.DateOffset(months=months)]
        if len(tr) < 36:
            continue
        X = np.column_stack([np.ones(len(tr)), tr.div_yield])
        b = np.linalg.lstsq(X, tr.fwd.values, rcond=None)[0]
        ys.append(df.loc[t, "fwd"])
        p.append(b[0] + b[1] * df.loc[t, "div_yield"])
        h.append(tr.fwd.mean())
    ys, p, h = map(np.array, (ys, p, h))
    # The weight on (model - mean) that would have minimised OOS error.
    d = p - h
    w_star = float(np.clip(((ys - h) * d).sum() / (d * d).sum(), 0, 1))
    return {
        "n": len(ys),
        "oos_r2": round(oos_r2(ys, p, h), 4),
        "optimal_weight": round(w_star, 3),
        "independent_windows": round(len(ys) / months, 1),
    }


def equity_prior_adjustment(art: dict, horizon_years: float) -> float:
    """Per-year adjustment to the 11% long-run equity prior over a projection
    of `horizon_years`: the shrunk valuation view applies to the first
    VALUATION_YEARS and is amortised over the whole horizon."""
    if not art:
        return 0.0
    gap = art["forecast_5y"] - art["model"]["hist_mean"]
    w = art["evidence_weight"]
    yrs = max(float(horizon_years), 1.0)
    return float(w * gap * min(VALUATION_YEARS, yrs) / yrs)


def current_dividend_yield() -> float:
    """Last three month-ends, averaged: the latest print alone is noisy."""
    return float(valuation_frame().div_yield.tail(3).mean())


def calibration_harness(
    phis=(-0.15, 0.0),
    horizon: int = 3,
    n_paths: int = 3000,
    valuation_art: dict | None = None,
    seed: int = 0,
    vol_blend: float | None = None,
) -> dict:
    """Score ARISE-style multi-year return forecasts on real funds.

    For every Indian equity fund and quarterly origin 2016-2023, forecast the
    distribution of its next `horizon`-year log return exactly as ARISE
    would (shrunk mean, trailing vol, Student-t(5) shocks with AR(1) phi),
    and score the realised outcome. Forecasts overlap in time and share one
    market, so treat the sample as ~2-3 independent periods, not hundreds."""
    import json

    from scipy import stats as st

    from backend.ml.evaluation import coverage, crps_ensemble, ks_uniform, pit

    meta = {m["id"]: m for m in json.loads((data.DATA / "schemes_metadata.json").read_text())}
    nav = data.nav_daily()
    eq = [
        c
        for c in nav
        if meta[c]["instrument_type"] == "equity"
        and not any(k in c for k in ("japan", "taiwan", "us-equity"))
    ]
    wk = np.log(nav[eq].resample("W-FRI").last()).diff()
    rng = np.random.default_rng(seed)
    df_t = 5.0
    raw = st.t.rvs(df_t, size=(n_paths, horizon), random_state=rng) / np.sqrt(df_t / (df_t - 2))

    def shocks(phi):
        z = np.empty_like(raw)
        prev = np.zeros(n_paths)
        scale = np.sqrt(1 - phi**2)
        for i in range(horizon):
            z[:, i] = phi * prev + scale * raw[:, i]
            prev = z[:, i]
        return z.sum(1)

    Z = {phi: shocks(phi) for phi in phis}
    vf = valuation_frame()
    mon = data.sensex_monthly_log_returns()
    rows = []
    for t in pd.date_range("2016-03-31", "2023-06-30", freq="QE"):
        long_run_vol = float(mon[:t].std() * np.sqrt(12))
        adj = 0.0
        if valuation_art:
            dy = float(vf.div_yield[:t].tail(3).mean())
            f = (
                valuation_art["model"]["intercept"]
                + valuation_art["model"]["slope_per_pct_dy"] * dy
            )
            adj = valuation_art["evidence_weight"] * (f - valuation_art["model"]["hist_mean"])
        for f_ in eq:
            s = nav[f_].dropna()
            if s.index[0] > t - pd.DateOffset(years=3) or s.index[-1] < t + pd.DateOffset(
                years=horizon
            ):
                continue
            past = s[:t]
            n = min((t - past.index[0]).days / 365.25, 10)
            trail = (
                np.log(
                    past.iloc[-1] / past[past.index >= t - pd.Timedelta(days=n * 365.25)].iloc[0]
                )
                / n
            )
            sig = wk[f_][:t].dropna().iloc[-156:].std() * np.sqrt(52)
            if vol_blend is not None:
                sig = float(np.sqrt(vol_blend * sig**2 + (1 - vol_blend) * long_run_vol**2))
            w = n / (n + TAU_YEARS)
            mu = w * trail + (1 - w) * (np.log(1 + LONG_RUN_EQUITY) + adj)
            y = np.log(s[: t + pd.DateOffset(years=horizon)].iloc[-1] / past.iloc[-1])
            for phi, z in Z.items():
                samp = horizon * mu + sig * z
                rows.append({"phi": phi, "crps": crps_ensemble(samp, y), "pit": pit(samp, y)})
    d = pd.DataFrame(rows)
    out = {}
    for phi, g in d.groupby("phi"):
        out[f"phi={phi:g}"] = {
            "n": len(g),
            "crps": round(g.crps.mean(), 4),
            "coverage_80": round(coverage(g.pit, 0.1, 0.9), 3),
            "coverage_50": round(coverage(g.pit, 0.25, 0.75), 3),
            "mean_pit": round(g.pit.mean(), 3),
            "ks": round(ks_uniform(g.pit.values), 3),
        }
    return out
