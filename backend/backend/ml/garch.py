"""
GARCH(1,1) with Student-t innovations, fitted by maximum likelihood (Risk).

Historical VaR treats every day of the last three years as equally relevant;
in a calm month after a volatile year it overstates risk, and in the first
days of a storm it understates it. GARCH makes tomorrow's variance depend on
recent shocks:

    sigma2_t = omega + alpha * eps_{t-1}^2 + beta * sigma2_{t-1}

so VaR tracks the volatility regime. Validated by rolling one-day-ahead VaR
backtests (Kupiec POF + Christoffersen independence) against historical VaR.
"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass

import numpy as np
from scipy import optimize, stats


@dataclass
class GarchParams:
    mu: float
    omega: float
    alpha: float
    beta: float
    nu: float  # Student-t degrees of freedom

    def to_dict(self) -> dict:
        return asdict(self)

    @property
    def persistence(self) -> float:
        return self.alpha + self.beta

    @property
    def long_run_vol_daily(self) -> float:
        return math.sqrt(self.omega / max(1e-12, 1 - self.persistence))


def _variance_path(eps: np.ndarray, omega: float, alpha: float, beta: float) -> np.ndarray:
    s2 = np.empty(len(eps) + 1)
    s2[0] = eps.var()
    for t in range(len(eps)):
        s2[t + 1] = omega + alpha * eps[t] ** 2 + beta * s2[t]
    return s2


def _nll(theta: np.ndarray, r: np.ndarray) -> float:
    mu, lw, a, b, nu = theta
    omega = math.exp(lw)
    if a < 0 or b < 0 or a + b >= 0.999 or nu <= 2.05:
        return 1e10
    eps = r - mu
    s2 = _variance_path(eps, omega, a, b)[:-1]
    scale = np.sqrt(s2 * (nu - 2) / nu)
    return float(-stats.t.logpdf(eps, nu, 0, scale).sum())


def fit_garch(r: np.ndarray) -> GarchParams:
    r = np.asarray(r, dtype=float)
    v = r.var()
    x0 = np.array([r.mean(), math.log(v * 0.05), 0.08, 0.90, 6.0])
    res = optimize.minimize(
        _nll,
        x0,
        args=(r,),
        method="Nelder-Mead",
        options={"maxiter": 4000, "xatol": 1e-8, "fatol": 1e-8},
    )
    mu, lw, a, b, nu = res.x
    return GarchParams(
        mu=float(mu), omega=float(math.exp(lw)), alpha=float(a), beta=float(b), nu=float(nu)
    )


def conditional_vol(r: np.ndarray, p: GarchParams) -> np.ndarray:
    """sigma_t for t = 1..T+1 (last element = tomorrow's forecast)."""
    return np.sqrt(_variance_path(np.asarray(r) - p.mu, p.omega, p.alpha, p.beta))


def var_es(
    p: GarchParams, sigma: float, level: float = 0.95, horizon_days: int = 1
) -> tuple[float, float]:
    """Loss-positive VaR and ES (as positive fractions) at a horizon, using
    the GARCH variance term structure (mean-reverting toward long-run)."""
    lr = p.long_run_vol_daily**2
    s2 = sigma**2
    tot = 0.0
    for _ in range(horizon_days):
        tot += s2
        s2 = lr + p.persistence * (s2 - lr)
    sd = math.sqrt(tot)
    q = stats.t.ppf(1 - level, p.nu) * math.sqrt((p.nu - 2) / p.nu)
    var = -(p.mu * horizon_days + sd * q)
    # Student-t ES, standardised then scaled.
    a = 1 - level
    t_q = stats.t.ppf(a, p.nu)
    es_std = (
        (stats.t.pdf(t_q, p.nu) / a) * ((p.nu + t_q**2) / (p.nu - 1)) * math.sqrt((p.nu - 2) / p.nu)
    )
    es = -(p.mu * horizon_days) + sd * es_std
    return float(var), float(es)


def kupiec_pof(breaches: int, n: int, level: float) -> float:
    """p-value of Kupiec's proportion-of-failures test."""
    p = 1 - level
    if n == 0:
        return float("nan")
    x = breaches
    phat = x / n

    def ll(q: float) -> float:
        if q <= 0 or q >= 1:
            return 0.0 if (x == 0 and q <= 0) or (x == n and q >= 1) else -1e18
        return (n - x) * math.log(1 - q) + x * math.log(q)

    lr = -2 * (ll(p) - (ll(phat) if 0 < phat < 1 else 0.0))
    return float(1 - stats.chi2.cdf(lr, 1))


def christoffersen_ind(hits: np.ndarray) -> float:
    """p-value of Christoffersen's independence test: do breaches cluster?"""
    h = np.asarray(hits, dtype=int)
    n00 = n01 = n10 = n11 = 0
    for a, b in zip(h[:-1], h[1:], strict=False):
        if a == 0 and b == 0:
            n00 += 1
        elif a == 0 and b == 1:
            n01 += 1
        elif a == 1 and b == 0:
            n10 += 1
        else:
            n11 += 1
    pi0 = n01 / max(n00 + n01, 1)
    pi1 = n11 / max(n10 + n11, 1)
    pi = (n01 + n11) / max(n00 + n01 + n10 + n11, 1)

    def L(p: float, a: int, b: int) -> float:
        return (a * math.log(1 - p) if a and p < 1 else 0.0) + (
            b * math.log(p) if b and p > 0 else 0.0
        )

    lr = -2 * (L(pi, n00 + n10, n01 + n11) - (L(pi0, n00, n01) + L(pi1, n10, n11)))
    return float(1 - stats.chi2.cdf(max(lr, 0.0), 1))


def fhs_var_es(
    r: np.ndarray, p: GarchParams, level: float = 0.95, horizon_days: int = 1
) -> tuple[float, float, float]:
    """Filtered historical simulation: GARCH supplies WHEN volatility is high,
    the investor's own standardised history supplies the SHAPE of the tail
    (no Student-t assumption). Returns (VaR, ES, tomorrow's daily vol)."""
    r = np.asarray(r, dtype=float)
    sig = conditional_vol(r, p)
    z = (r - p.mu) / sig[:-1]
    q = np.quantile(z, 1 - level)
    tail = z[z <= q]
    lr = p.long_run_vol_daily**2
    s2 = sig[-1] ** 2
    tot = 0.0
    for _ in range(horizon_days):
        tot += s2
        s2 = lr + p.persistence * (s2 - lr)
    sd = math.sqrt(tot)
    var = -(p.mu * horizon_days + sd * q)
    es = -(p.mu * horizon_days + sd * (tail.mean() if len(tail) else q))
    return float(var), float(es), float(sig[-1])


def rolling_backtest(
    r: np.ndarray, level: float = 0.95, window: int = 750, refit_every: int = 125
) -> dict:
    """One-day-ahead VaR, out of sample: at each day t only data before t is
    used. Compares GARCH-t, GARCH filtered historical simulation and plain
    historical-simulation VaR on the same days."""
    r = np.asarray(r, dtype=float)
    g_hits, f_hits, h_hits = [], [], []
    p = None
    for t in range(window, len(r)):
        past = r[t - window : t]
        if p is None or (t - window) % refit_every == 0:
            p = fit_garch(past)
        sig = conditional_vol(past, p)[-1]
        g_var, _ = var_es(p, sig, level)
        f_var, _, _ = fhs_var_es(past, p, level)
        h_var = -np.quantile(past, 1 - level)
        g_hits.append(int(-r[t] > g_var))
        f_hits.append(int(-r[t] > f_var))
        h_hits.append(int(-r[t] > h_var))
    n = len(g_hits)
    out = {}
    for name, hits in (("garch_t", g_hits), ("garch_fhs", f_hits), ("historical", h_hits)):
        b = int(sum(hits))
        out[name] = {
            "days": n,
            "breaches": b,
            "breach_rate": round(b / n, 4) if n else None,
            "kupiec_p": round(kupiec_pof(b, n, level), 4),
            "independence_p": round(christoffersen_ind(np.array(hits)), 4),
        }
    out["expected_rate"] = round(1 - level, 4)
    return out
