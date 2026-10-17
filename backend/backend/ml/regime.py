"""
Gaussian hidden Markov regime model for equity returns (ARISE).

Market returns are not one distribution: calm, trending years and turbulent,
crashing ones alternate, and turbulence clusters (2000-03, 2008-09, 2020).
A K-state HMM learns those regimes from 29 years of monthly Sensex returns --
each state's mean and volatility, and how persistent it is.

ARISE does NOT take a return LEVEL from this model. It uses the model to
generate standardised annual shocks (zero mean, unit variance), then applies
the investor's own shrunk mu / sigma exactly as before. What the model adds is
the SHAPE of the risk: crash years arriving together, skew, fat tails.

Fitting is Baum-Welch (EM) with scaled forward-backward passes, several
deterministic restarts, best log-likelihood kept. ~100 lines of numpy so the
production image needs no extra dependency.
"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass

import numpy as np
from scipy import stats


@dataclass
class HMMParams:
    pi: list[float]  # initial state probabilities
    A: list[list[float]]  # transition matrix, rows sum to 1
    mu: list[float]  # per-state monthly log-return mean
    sigma: list[float]  # per-state monthly log-return sd

    def to_dict(self) -> dict:
        return asdict(self)

    @staticmethod
    def from_dict(d: dict) -> HMMParams:
        return HMMParams(pi=d["pi"], A=d["A"], mu=d["mu"], sigma=d["sigma"])


def _emissions(x: np.ndarray, mu: np.ndarray, sigma: np.ndarray) -> np.ndarray:
    return stats.norm.pdf(x[:, None], mu[None, :], sigma[None, :]) + 1e-300


def _forward_backward(x, pi, A, mu, sigma):
    T, K = len(x), len(pi)
    B = _emissions(x, mu, sigma)
    alpha = np.empty((T, K))
    c = np.empty(T)
    alpha[0] = pi * B[0]
    c[0] = alpha[0].sum()
    alpha[0] /= c[0]
    for t in range(1, T):
        alpha[t] = (alpha[t - 1] @ A) * B[t]
        c[t] = alpha[t].sum()
        alpha[t] /= c[t]
    beta = np.empty((T, K))
    beta[-1] = 1.0
    for t in range(T - 2, -1, -1):
        beta[t] = (A @ (B[t + 1] * beta[t + 1])) / c[t + 1]
    return alpha, beta, B, c, float(np.log(c).sum())


def log_likelihood(x: np.ndarray, p: HMMParams) -> float:
    return _forward_backward(
        np.asarray(x), np.array(p.pi), np.array(p.A), np.array(p.mu), np.array(p.sigma)
    )[4]


def filtered_state(x: np.ndarray, p: HMMParams) -> np.ndarray:
    """P(state at the last observation | data) -- today's regime."""
    alpha = _forward_backward(
        np.asarray(x), np.array(p.pi), np.array(p.A), np.array(p.mu), np.array(p.sigma)
    )[0]
    return alpha[-1]


def fit_hmm(
    x: np.ndarray, k: int, n_restarts: int = 12, n_iter: int = 300, seed: int = 7
) -> tuple[HMMParams, float]:
    x = np.asarray(x, dtype=float)
    rng = np.random.default_rng(seed)
    best, best_ll = None, -np.inf
    sd = x.std()
    for _ in range(n_restarts):
        q = np.sort(rng.choice(x, size=k, replace=False))
        mu = q.copy()
        sigma = np.full(k, sd) * rng.uniform(0.6, 1.4, k)
        A = np.full((k, k), 0.1 / max(k - 1, 1))
        np.fill_diagonal(A, 0.9)
        pi = np.full(k, 1.0 / k)
        prev = -np.inf
        for _ in range(n_iter):
            alpha, beta, B, c, ll = _forward_backward(x, pi, A, mu, sigma)
            gamma = alpha * beta
            gamma /= gamma.sum(1, keepdims=True)
            xi = (alpha[:-1, :, None] * A[None] * (B[1:] * beta[1:])[:, None, :]) / c[
                1:, None, None
            ]
            pi = gamma[0]
            A = xi.sum(0) / gamma[:-1].sum(0)[:, None]
            A /= A.sum(1, keepdims=True)
            w = gamma.sum(0)
            mu = (gamma * x[:, None]).sum(0) / w
            # Floor each state's sd at 20% of the sample sd: stops EM
            # collapsing a state onto a single outlier month.
            sigma = np.sqrt((gamma * (x[:, None] - mu) ** 2).sum(0) / w)
            sigma = np.maximum(sigma, 0.2 * sd)
            if ll - prev < 1e-7:
                break
            prev = ll
        if ll > best_ll:
            best_ll = ll
            order = np.argsort(sigma)  # state 0 = calmest, for stable reporting
            best = HMMParams(
                pi=pi[order].tolist(),
                A=A[np.ix_(order, order)].tolist(),
                mu=mu[order].tolist(),
                sigma=sigma[order].tolist(),
            )
    return best, best_ll


def n_params(k: int) -> int:
    return (k - 1) + k * (k - 1) + 2 * k


def stationary(A: np.ndarray) -> np.ndarray:
    w, v = np.linalg.eig(A.T)
    s = np.real(v[:, np.argmin(np.abs(w - 1))])
    return s / s.sum()


def simulate_annual_log_returns(
    p: HMMParams, n_years: int, rng: np.random.Generator, start_probs: np.ndarray | None = None
) -> np.ndarray:
    """Annual log returns = sum of 12 simulated monthly log returns."""
    A = np.array(p.A)
    mu = np.array(p.mu)
    sg = np.array(p.sigma)
    k = len(mu)
    cum = np.cumsum(A, axis=1)
    probs = stationary(A) if start_probs is None else start_probs
    s = rng.choice(k, p=probs)
    n = n_years * 12
    u = rng.random(n)
    z = rng.standard_normal(n)
    out = np.empty(n)
    for t in range(n):
        out[t] = mu[s] + sg[s] * z[t]
        s = int(np.searchsorted(cum[s], u[t]))
        s = min(s, k - 1)
    return out.reshape(n_years, 12).sum(1)


def annual_moments(p: HMMParams, n: int = 200_000, seed: int = 11) -> tuple[float, float]:
    """Unconditional mean / sd of the model's annual log return, by
    simulation (the stationary chain makes it exact up to MC error)."""
    rng = np.random.default_rng(seed)
    a = simulate_annual_log_returns(p, n, rng)
    return float(a.mean()), float(a.std())


class RegimeShockGenerator:
    """Drop-in for arise_engine.standardized_shock_path: zero-mean,
    unit-variance annual shocks carrying the learned regime structure."""

    def __init__(self, params: HMMParams, annual_mean: float, annual_sd: float):
        self.p = params
        self.m = annual_mean
        self.s = annual_sd

    def __call__(self, n_years: int, rng: np.random.Generator) -> np.ndarray:
        a = simulate_annual_log_returns(self.p, n_years, rng)
        return (a - self.m) / self.s


def student_t_iid_loglik(x_train: np.ndarray, x_test: np.ndarray) -> tuple[float, float]:
    """Benchmark: the iid Student-t ARISE used before, fitted on train."""
    df, loc, scale = stats.t.fit(x_train)
    return float(stats.t.logpdf(x_test, df, loc, scale).sum()), float(df)


def holdout_loglik_hmm(p: HMMParams, x_train: np.ndarray, x_test: np.ndarray) -> float:
    """Predictive log-likelihood of the test block given the train block:
    ll(train+test) - ll(train), the HMM's honest one-step-ahead score."""
    full = np.concatenate([x_train, x_test])
    return log_likelihood(full, p) - log_likelihood(x_train, p)


def bic(ll: float, k: int, n: int) -> float:
    return -2 * ll + n_params(k) * math.log(n)


# ── Student-t emission HMM ─────────────────────────────────────────────────────
# Same chain, but each regime's returns are Student-t rather than Gaussian:
# regimes for the persistence of turbulence, t for the tails inside a regime.
# M-step for (mu, scale, nu) is a weighted MLE per state (ECM).


def _t_emissions(x, mu, sc, nu):
    return stats.t.pdf(x[:, None], nu[None, :], mu[None, :], sc[None, :]) + 1e-300


def _fb_generic(x, pi, A, B):
    T, K = B.shape
    alpha = np.empty((T, K))
    c = np.empty(T)
    alpha[0] = pi * B[0]
    c[0] = alpha[0].sum()
    alpha[0] /= c[0]
    for t in range(1, T):
        alpha[t] = (alpha[t - 1] @ A) * B[t]
        c[t] = alpha[t].sum()
        alpha[t] /= c[t]
    beta = np.empty((T, K))
    beta[-1] = 1.0
    for t in range(T - 2, -1, -1):
        beta[t] = (A @ (B[t + 1] * beta[t + 1])) / c[t + 1]
    return alpha, beta, c, float(np.log(c).sum())


def fit_t_hmm(
    x: np.ndarray, k: int = 2, n_restarts: int = 4, n_iter: int = 60, seed: int = 7
) -> tuple[dict, float]:
    from scipy import optimize

    x = np.asarray(x, float)
    rng = np.random.default_rng(seed)
    sd = x.std()
    best, best_ll = None, -np.inf
    for _ in range(n_restarts):
        mu = np.sort(rng.choice(x, k, replace=False))
        sc = sd * rng.uniform(0.5, 1.2, k)
        nu = np.full(k, 6.0)
        A = np.full((k, k), 0.05 / max(k - 1, 1))
        np.fill_diagonal(A, 0.95)
        pi = np.full(k, 1 / k)
        prev = -np.inf
        for _ in range(n_iter):
            B = _t_emissions(x, mu, sc, nu)
            alpha, beta, c, ll = _fb_generic(x, pi, A, B)
            g = alpha * beta
            g /= g.sum(1, keepdims=True)
            xi = (alpha[:-1, :, None] * A[None] * (B[1:] * beta[1:])[:, None, :]) / c[
                1:, None, None
            ]
            pi = g[0]
            A = xi.sum(0) / g[:-1].sum(0)[:, None]
            A /= A.sum(1, keepdims=True)
            for j in range(k):
                w = g[:, j]

                def nll(th, w=w):
                    m, ls, ln = th
                    return -(w * stats.t.logpdf(x, 2.05 + np.exp(ln), m, np.exp(ls))).sum()

                r = optimize.minimize(
                    nll,
                    [mu[j], np.log(sc[j]), np.log(max(nu[j] - 2.05, 0.1))],
                    method="Nelder-Mead",
                    options={"maxiter": 300},
                )
                mu[j] = r.x[0]
                sc[j] = max(np.exp(r.x[1]), 0.15 * sd)
                nu[j] = min(2.05 + np.exp(r.x[2]), 200)
            if ll - prev < 1e-6:
                break
            prev = ll
        if ll > best_ll:
            o = np.argsort(sc)
            best_ll = ll
            best = {
                "pi": pi[o].tolist(),
                "A": A[np.ix_(o, o)].tolist(),
                "mu": mu[o].tolist(),
                "scale": sc[o].tolist(),
                "nu": nu[o].tolist(),
            }
    return best, best_ll


def t_hmm_loglik(x: np.ndarray, p: dict) -> float:
    x = np.asarray(x, float)
    B = _t_emissions(x, np.array(p["mu"]), np.array(p["scale"]), np.array(p["nu"]))
    return _fb_generic(x, np.array(p["pi"]), np.array(p["A"]), B)[3]


def simulate_annual_t_hmm(p: dict, n_years: int, rng: np.random.Generator) -> np.ndarray:
    A = np.array(p["A"])
    mu = np.array(p["mu"])
    sc = np.array(p["scale"])
    nu = np.array(p["nu"])
    k = len(mu)
    cum = np.cumsum(A, 1)
    s = rng.choice(k, p=stationary(A))
    n = n_years * 12
    u = rng.random(n)
    out = np.empty(n)
    for t in range(n):
        out[t] = mu[s] + sc[s] * rng.standard_t(nu[s])
        s = min(int(np.searchsorted(cum[s], u[t])), k - 1)
    return out.reshape(n_years, 12).sum(1)


def load_regime_stress_shocks():
    """Standardised annual shock generator from the trained regime model
    (backend/data/models/arise_regime.json), or None if not trained."""
    from backend.ml.data import load_artifact

    art = load_artifact("arise_regime.json")
    if not art:
        return None, None
    p, m, s = art["params"], art["annual_mean"], art["annual_sd"]

    def shocks(n_years: int, rng: np.random.Generator) -> np.ndarray:
        return (simulate_annual_t_hmm(p, n_years, rng) - m) / s

    return shocks, art
