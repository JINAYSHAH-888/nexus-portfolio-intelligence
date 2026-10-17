"""
Risk's dedicated volatility models (trained in scripts/train_models.py).

The forecast target is realised variance measured from Nifty's daily
open/high/low/close (Garman-Klass) -- far less noisy than a squared daily
return, so models can actually be told apart (Patton 2011).

Candidates, all scored out of sample with QLIKE:
  garch_t       GARCH(1,1), Student-t likelihood         (the current model)
  gjr_t         GJR-GARCH: falls raise volatility more than rises
  har           HAR-RV (Corsi 2009): daily / weekly / monthly realised vol
  har_x         HAR + India VIX + overnight S&P 500 + USD/INR + crude
  gbm_x         gradient-boosted trees on the HAR-X inputs
  combo         equal-weight average of the best econometric and ML forecasts

Forecasts for a PORTFOLIO come from a one-factor map: the portfolio's beta
to Nifty and its own idiosyncratic variance turn the market forecast into
the portfolio's, so the model can use 19 years of index data instead of a
portfolio's few years of history.
"""

from __future__ import annotations

import math

import numpy as np
import pandas as pd
from scipy import optimize, stats

from backend.ml import data

LN2 = math.log(2)


def garman_klass(ohlc: pd.DataFrame) -> pd.Series:
    """Daily variance from OHLC (Garman & Klass 1980), plus the overnight
    gap so the estimate covers close-to-close, not just the session."""
    o, h, l_, c = (np.log(ohlc[k]) for k in ("open", "high", "low", "close"))
    hl = (h - l_) ** 2
    co = (c - o) ** 2
    intraday = 0.5 * hl - (2 * LN2 - 1) * co
    overnight = (o - c.shift(1)) ** 2
    return (intraday + overnight.fillna(0)).clip(lower=1e-8)


def feature_frame() -> pd.DataFrame:
    """Everything known at the close of day t, to forecast t+1..t+h."""
    ohlc = pd.read_csv(data.HISTORY / "nifty_ohlc.csv.gz", index_col=0, parse_dates=True)
    fac = pd.read_csv(data.HISTORY / "factors_daily.csv.gz", index_col=0, parse_dates=True)
    rv = garman_klass(ohlc)
    ret = np.log(ohlc["close"]).diff()
    df = pd.DataFrame({"rv": rv, "ret": ret})
    df["rv_d"] = df.rv
    df["rv_w"] = df.rv.rolling(5).mean()
    df["rv_m"] = df.rv.rolling(22).mean()
    # India VIX (annualised %) -> daily variance; S&P 500 closes AFTER India,
    # so its return dated t is known before India opens on t+1.
    f = fac.reindex(df.index, method="ffill")
    df["vix_var"] = (f["india_vix"] / 100) ** 2 / 252
    spx = np.log(fac["sp500"]).diff().reindex(df.index, method="ffill")
    df["spx_ret"] = spx
    df["usdinr_abs"] = np.log(fac["usdinr"]).diff().abs().reindex(df.index, method="ffill")
    df["crude_abs"] = (
        np.log(fac["crude"]).diff().abs().clip(upper=0.5).reindex(df.index, method="ffill")
    )
    df["neg_ret"] = np.minimum(df.ret, 0)
    return df.dropna()


HAR = ["rv_d", "rv_w", "rv_m"]
HAR_X = HAR + ["vix_var", "spx_ret", "usdinr_abs", "crude_abs", "neg_ret"]


def _logx(X: pd.DataFrame) -> np.ndarray:
    out = X.copy()
    for c in ("rv_d", "rv_w", "rv_m", "vix_var"):
        if c in out:
            out[c] = np.log(out[c])
    if "spx_ret" in out:
        out["spx_ret"] = out["spx_ret"].abs()
    return out.values


def target(df: pd.DataFrame, h: int) -> pd.Series:
    """Mean daily variance over t+1..t+h."""
    return df.rv[::-1].rolling(h).mean()[::-1].shift(-1)


# ── GJR-GARCH ───────────────────────────────────────────────────────────────
def _gjr_nll(theta, r):
    mu, lw, a, g, b, nu = theta
    if a < 0 or g < 0 or b < 0 or a + g / 2 + b >= 0.999 or nu <= 2.05:
        return 1e10
    w = math.exp(lw)
    eps = r - mu
    s2 = np.empty(len(r))
    s2[0] = eps.var()
    for t in range(1, len(r)):
        e = eps[t - 1]
        s2[t] = w + (a + g * (e < 0)) * e * e + b * s2[t - 1]
    sc = np.sqrt(s2 * (nu - 2) / nu)
    return float(-stats.t.logpdf(eps, nu, 0, sc).sum())


def fit_gjr(r: np.ndarray) -> dict:
    r = np.asarray(r, float)
    x0 = [r.mean(), math.log(r.var() * 0.05), 0.03, 0.08, 0.88, 6.0]
    res = optimize.minimize(
        _gjr_nll,
        x0,
        args=(r,),
        method="Nelder-Mead",
        options={"maxiter": 5000, "xatol": 1e-8, "fatol": 1e-8},
    )
    mu, lw, a, g, b, nu = res.x
    return {"mu": mu, "omega": math.exp(lw), "alpha": a, "gamma": g, "beta": b, "nu": nu}


def gjr_variance_path(r: np.ndarray, p: dict) -> np.ndarray:
    """sigma^2_t for t = 0..T (last = next-day forecast)."""
    eps = np.asarray(r) - p["mu"]
    s2 = np.empty(len(eps) + 1)
    s2[0] = eps.var()
    for t in range(len(eps)):
        e = eps[t]
        s2[t + 1] = p["omega"] + (p["alpha"] + p["gamma"] * (e < 0)) * e * e + p["beta"] * s2[t]
    return s2


def gjr_multi_step(s2_next: float, p: dict, h: int) -> float:
    """Mean daily variance over the next h days."""
    pers = p["alpha"] + p["gamma"] / 2 + p["beta"]
    lr = p["omega"] / max(1e-12, 1 - pers)
    tot, s2 = 0.0, s2_next
    for _ in range(h):
        tot += s2
        s2 = lr + pers * (s2 - lr)
    return tot / h


# ── EVT tail on standardised residuals ──────────────────────────────────────
def evt_quantile(z: np.ndarray, level: float, threshold_q: float = 0.90) -> tuple[float, float]:
    """Peaks-over-threshold: fit a generalised Pareto to standardised LOSSES
    beyond their 90th percentile; returns (VaR, ES) of the standardised
    loss at `level` (McNeil & Frey 2000)."""
    loss = -np.asarray(z, float)
    u = np.quantile(loss, threshold_q)
    exc = loss[loss > u] - u
    xi, _, beta = stats.genpareto.fit(exc, floc=0)
    n, nu = len(loss), len(exc)
    p = 1 - level
    var = (
        u + beta / xi * ((n / nu * p) ** (-xi) - 1)
        if abs(xi) > 1e-6
        else u - beta * math.log(n / nu * p)
    )
    es = (var + beta - xi * u) / (1 - xi) if xi < 1 else float("nan")
    return float(var), float(es)


def fit_log_linear(d: pd.DataFrame, cols: list[str], y: np.ndarray) -> dict:
    X = np.column_stack([np.ones(len(d)), _logx(d[cols])])
    b, *_ = np.linalg.lstsq(X, np.log(y), rcond=None)
    s2 = float(np.var(np.log(y) - X @ b))
    return {"cols": cols, "coef": b.tolist(), "resid_var": s2}


def predict_log_linear(m: dict, d: pd.DataFrame) -> np.ndarray:
    X = np.column_stack([np.ones(len(d)), _logx(d[m["cols"]])])
    # Lognormal bias correction: E[exp(e)] = exp(s2/2).
    return np.exp(X @ np.array(m["coef"]) + 0.5 * m["resid_var"])


def oos_harx(
    df: pd.DataFrame, h: int = 1, start: str = "2010-06-01", refit: int = 250
) -> pd.Series:
    """One-step-ahead HAR-X variance forecasts, expanding window, refit
    every `refit` days, training only on targets fully observed by then."""
    df = df.assign(y=target(df, h)).dropna(subset=["y"])
    out = pd.Series(np.nan, index=df.index)
    s0 = int(np.searchsorted(df.index, pd.Timestamp(start)))
    for s in range(s0, len(df), refit):
        e = min(s + refit, len(df))
        m = fit_log_linear(df.iloc[: s - h], HAR_X, df.y.values[: s - h])
        out.iloc[s:e] = predict_log_linear(m, df.iloc[s:e])
    return out


def diebold_mariano(loss_a: np.ndarray, loss_b: np.ndarray, h: int = 1) -> tuple[float, float]:
    """DM test (Newey-West HAC, lag h-1... at least 5): positive stat => A
    has HIGHER loss than B. Returns (stat, two-sided p)."""
    d = np.asarray(loss_a) - np.asarray(loss_b)
    d = d[np.isfinite(d)]
    n = len(d)
    lag = max(h - 1, 5)
    dm = d - d.mean()
    gamma = [np.dot(dm[k:], dm[: n - k]) / n for k in range(lag + 1)]
    var = gamma[0] + 2 * sum((1 - k / (lag + 1)) * gamma[k] for k in range(1, lag + 1))
    stat = d.mean() / math.sqrt(var / n)
    return float(stat), float(2 * (1 - stats.norm.cdf(abs(stat))))


def qlike_series(f, r):
    f = np.maximum(np.asarray(f, float), 1e-12)
    r = np.maximum(np.asarray(r, float), 1e-12)
    return r / f - np.log(r / f) - 1


# ── Blended conditional VaR for a fund / portfolio ───────────────────────────
BLEND = 0.5  # weight on the fund's own GARCH variance (rest: HAR-X mapped by beta)


def blended_var_es(
    r: np.ndarray,
    m: np.ndarray,
    vm: np.ndarray,
    vm_next: float,
    level: float,
    horizon_days: int = 1,
    vm_horizon: float | None = None,
) -> dict:
    """VaR/ES (positive loss fractions) for returns r, given the market's
    returns m and HAR-X variance forecasts vm (aligned to r) plus the
    forecast for the next day. FHS tail at 95%, EVT (GPD) tail at >= 99%."""
    from backend.ml import garch as G

    r = np.asarray(r, float)
    m = np.asarray(m, float)
    vm = np.asarray(vm, float)
    pg = G.fit_garch(r)
    X = np.column_stack([np.ones(len(r)), m])
    b = np.linalg.lstsq(X, r, rcond=None)[0]
    beta, s2e = float(b[1]), float(np.var(r - X @ b))
    gs = G.conditional_vol(r, pg)
    sig_past = np.sqrt(BLEND * gs[:-1] ** 2 + (1 - BLEND) * (beta**2 * vm + s2e))
    z = (r - pg.mu) / sig_past
    # Horizon variance: GARCH term structure for the fund part, the HAR-X
    # h-day forecast (mean daily variance over the horizon) for the market part.
    lr = pg.long_run_vol_daily**2
    s2, g_tot = gs[-1] ** 2, 0.0
    for _ in range(horizon_days):
        g_tot += s2
        s2 = lr + pg.persistence * (s2 - lr)
    m_daily = vm_horizon if vm_horizon is not None else vm_next
    var_h = BLEND * g_tot + (1 - BLEND) * horizon_days * (beta**2 * m_daily + s2e)
    sd = math.sqrt(var_h)
    if level >= 0.99:
        qv, qe = evt_quantile(z, level)
        var, es = -pg.mu * horizon_days + sd * qv, -pg.mu * horizon_days + sd * qe
        tail = "evt"
    else:
        q = np.quantile(z, 1 - level)
        t = z[z <= q]
        var, es = -(pg.mu * horizon_days + sd * q), -(pg.mu * horizon_days + sd * t.mean())
        tail = "fhs"
    return {
        "var": float(var),
        "es": float(es),
        "beta": beta,
        "sd_daily_next": math.sqrt(BLEND * gs[-1] ** 2 + (1 - BLEND) * (beta**2 * vm_next + s2e)),
        "tail": tail,
        "garch": pg,
    }


def fund_backtest(levels=(0.95, 0.99), start: str = "2014-01-01", window: int = 750) -> dict:
    """Rolling one-day VaR/ES on every Indian equity fund's NAV, out of
    sample. Methods: the previous per-fund GARCH-FHS, HAR-X mapped by beta,
    and the production blend (FHS at 95%, EVT at 99%)."""
    import json

    from backend.ml import garch as G
    from backend.ml.evaluation import fz0_loss

    meta = {x["id"]: x for x in json.loads((data.DATA / "schemes_metadata.json").read_text())}
    nav = data.nav_daily()
    df = feature_frame()
    vm_next = oos_harx(df, 1).shift(1)  # aligned to the day it forecasts
    eq = [
        c
        for c in nav
        if meta[c]["instrument_type"] == "equity"
        and not any(k in c for k in ("japan", "taiwan", "us-equity"))
    ]
    methods = ("garch_fhs", "harx_factor", "blend")
    out = {lvl: {k: [] for k in methods} for lvl in levels}
    for f in eq:
        r = np.log(nav[f].dropna()).diff().dropna()
        j = pd.concat(
            [r.rename("r"), df.ret.rename("m"), vm_next.rename("vm")], axis=1, join="inner"
        ).dropna()
        if len(j) < window + 250:
            continue
        R, M, VM = j.r.values, j.m.values, j.vm.values
        s0 = max(window, int(np.searchsorted(j.index, pd.Timestamp(start))))
        rec = {lvl: {k: ([], [], []) for k in methods} for lvl in levels}
        pg = beta = s2e = None
        for t in range(s0, len(j)):
            past = slice(t - window, t)
            if pg is None or (t - s0) % 250 == 0:
                pg = G.fit_garch(R[past])
                X = np.column_stack([np.ones(window), M[past]])
                b = np.linalg.lstsq(X, R[past], rcond=None)[0]
                beta, s2e = b[1], np.var(R[past] - X @ b)
            sp = np.sqrt(beta**2 * VM[past] + s2e)
            sp_t = math.sqrt(beta**2 * VM[t] + s2e)
            gs = G.conditional_vol(R[past], pg)
            bp = np.sqrt(BLEND * gs[:-1] ** 2 + (1 - BLEND) * sp**2)
            bt = math.sqrt(BLEND * gs[-1] ** 2 + (1 - BLEND) * sp_t**2)
            z_h, z_b = R[past] / sp, (R[past] - pg.mu) / bp
            for lvl in levels:
                a = 1 - lvl
                gv, ge, _ = G.fhs_var_es(R[past], pg, lvl)
                q = np.quantile(z_h, a)
                hv, he = -sp_t * q, -sp_t * z_h[z_h <= q].mean()
                if lvl >= 0.99:
                    qv, qe = evt_quantile(z_b, lvl)
                    bv, be = -pg.mu + bt * qv, -pg.mu + bt * qe
                else:
                    qb = np.quantile(z_b, a)
                    bv, be = -(pg.mu + bt * qb), -(pg.mu + bt * z_b[z_b <= qb].mean())
                for k, (v, e) in zip(methods, ((gv, ge), (hv, he), (bv, be)), strict=True):
                    rec[lvl][k][0].append(R[t])
                    rec[lvl][k][1].append(v)
                    rec[lvl][k][2].append(e)
        for lvl in levels:
            for k, (y, v, e) in rec[lvl].items():
                y, v, e = map(np.array, (y, v, e))
                hits = (-y > v).astype(int)
                out[lvl][k].append(
                    {
                        "n": len(y),
                        "rate": hits.mean(),
                        "kupiec": G.kupiec_pof(int(hits.sum()), len(y), lvl),
                        "ind": G.christoffersen_ind(hits),
                        "fz0": fz0_loss(y, -v, -e, 1 - lvl),
                    }
                )
    summ = {}
    for lvl in levels:
        summ[str(lvl)] = {}
        for k, rows in out[lvl].items():
            d = pd.DataFrame(rows)
            summ[str(lvl)][k] = {
                "funds": len(d),
                "days": int(d.n.sum()),
                "mean_breach_rate": round(float(d.rate.mean()), 4),
                "kupiec_pass": int((d.kupiec >= 0.05).sum()),
                "independence_pass": int((d.ind >= 0.05).sum()),
                "mean_fz0": round(float(d.fz0.mean()), 4),
            }
    return summ
