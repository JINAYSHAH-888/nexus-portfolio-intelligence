"""
Hierarchical Risk Parity (Lopez de Prado, 2016) -- a risk-balanced reference
allocation for the schemes the investor has already selected.

WHY HRP AND NOT MEAN-VARIANCE
-----------------------------
Classical mean-variance optimisation requires INVERTING the covariance matrix.
With 2-35 schemes estimated over roughly three years of overlapping daily
history, that matrix is close to singular: Indian equity funds are mutually
correlated at 0.85-0.95, so the smallest eigenvalues are tiny and dominated by
estimation noise. Inverting it amplifies exactly that noise, which is why
Markowitz optimisers routinely return 90% in one fund and a short position in
another on data like this -- answers that are precise, unstable, and wrong.

HRP never inverts anything. It clusters the schemes by correlation distance,
reorders the covariance matrix so similar schemes sit adjacent, and then
splits capital recursively down the tree in inverse proportion to each
branch's variance. The result is stable under small changes in the input,
which matters more than theoretical optimality when the input is three years
of noisy history.

WHAT THIS IS AND IS NOT
-----------------------
It is a RISK-BALANCED REFERENCE allocation: what these schemes would look like
weighted so no single cluster dominates the portfolio's risk. It is computed
purely from realised covariance.

It is NOT a recommendation, and the output says so. It contains no view on
expected returns, no account of the investor's goals, taxes, exit loads or
existing cost basis, and no claim that past covariance predicts future
covariance. The platform's job here is to show what balance would look like,
not to tell someone what to buy.
"""
from __future__ import annotations

import logging

import numpy as np

logger = logging.getLogger(__name__)

MIN_ASSETS = 2


def _correlation_from_covariance(cov: np.ndarray) -> np.ndarray:
    sd = np.sqrt(np.diag(cov))
    denom = np.outer(sd, sd)
    with np.errstate(divide="ignore", invalid="ignore"):
        corr = np.where(denom > 0, cov / denom, 0.0)
    np.fill_diagonal(corr, 1.0)
    return np.clip(np.nan_to_num(corr), -1.0, 1.0)


def _quasi_diagonal_order(link: np.ndarray, n: int) -> list[int]:
    """
    Walk the linkage tree and return the leaf order that places correlated
    assets adjacent. This is what lets the recursive split operate on
    coherent groups instead of arbitrary ones.
    """
    import pandas as pd

    link = link.astype(int)
    order = pd.Series([link[-1, 0], link[-1, 1]])
    num_items = n

    while order.max() >= num_items:
        order.index = range(0, order.shape[0] * 2, 2)
        clusters = order[order >= num_items]
        i, j = clusters.index, clusters.values - num_items
        order[i] = link[j, 0]
        extra = pd.Series(link[j, 1], index=i + 1)
        order = pd.concat([order, extra]).sort_index()
        order.index = range(order.shape[0])

    return order.tolist()


def _cluster_variance(cov: np.ndarray, idx: list[int]) -> float:
    """Variance of an inverse-variance-weighted sub-portfolio."""
    sub = cov[np.ix_(idx, idx)]
    diag = np.diag(sub).copy()
    diag[diag <= 0] = 1e-12
    w = 1.0 / diag
    w /= w.sum()
    return float(w @ sub @ w)


def _recursive_bisection(cov: np.ndarray, order: list[int]) -> np.ndarray:
    """Split capital down the tree, inversely to each side's variance."""
    weights = np.ones(len(order))
    clusters = [order]

    while clusters:
        # Bisect every cluster with more than one member.
        clusters = [
            c[k:m]
            for c in clusters
            for k, m in ((0, len(c) // 2), (len(c) // 2, len(c)))
            if len(c) > 1
        ]
        for i in range(0, len(clusters), 2):
            left, right = clusters[i], clusters[i + 1]
            v_left = _cluster_variance(cov, left)
            v_right = _cluster_variance(cov, right)
            # The riskier side gets proportionally less.
            alpha = 1.0 - v_left / (v_left + v_right) if (v_left + v_right) > 0 else 0.5
            for idx in left:
                weights[order.index(idx)] *= alpha
            for idx in right:
                weights[order.index(idx)] *= 1.0 - alpha
    return weights


def hierarchical_risk_parity(
    cov: np.ndarray, labels: list[str]
) -> dict[str, float] | None:
    """
    Risk-balanced weights for `labels`, summing to 1.0.

    Returns None when the input cannot support an allocation (fewer than two
    schemes, a degenerate covariance matrix, or a clustering failure) rather
    than falling back to equal weight silently -- a caller that receives
    weights should know they were actually computed.
    """
    n = len(labels)
    if n < MIN_ASSETS or cov.shape != (n, n):
        return None
    if not np.all(np.isfinite(cov)) or np.allclose(np.diag(cov), 0):
        return None

    try:
        from scipy.cluster.hierarchy import linkage
        from scipy.spatial.distance import squareform

        corr = _correlation_from_covariance(cov)
        # Lopez de Prado's correlation distance: d = sqrt(0.5 * (1 - rho)),
        # which is a proper metric, unlike (1 - rho) on its own.
        dist = np.sqrt(np.clip(0.5 * (1.0 - corr), 0.0, 1.0))
        np.fill_diagonal(dist, 0.0)
        dist = (dist + dist.T) / 2.0          # enforce exact symmetry

        link = linkage(squareform(dist, checks=False), method="single")
        order = _quasi_diagonal_order(link, n)
        if sorted(order) != list(range(n)):
            logger.warning("HRP quasi-diagonalisation produced an invalid ordering.")
            return None

        raw = _recursive_bisection(cov, order)
        total = raw.sum()
        if not np.isfinite(total) or total <= 0:
            return None
        raw = raw / total
        return {labels[order[i]]: float(raw[i]) for i in range(n)}
    except Exception as e:
        logger.warning("HRP failed (%s: %s); no suggested allocation.", type(e).__name__, e)
        return None


def allocation_suggestion(
    cov: np.ndarray, labels: list[str], current_weights: dict[str, float] | None = None
) -> dict:
    """
    HRP weights plus the context needed to read them responsibly.

    `current_weights` is echoed with the per-scheme delta so the investor can
    see what would actually change, rather than being handed a fresh set of
    numbers with no reference point.
    """
    weights = hierarchical_risk_parity(cov, labels)
    if weights is None:
        return {
            "available": False,
            "reason": "Not enough schemes, or covariance could not support a "
                      "stable allocation.",
            "weights": {},
        }

    ordered = {k: round(v, 4) for k, v in sorted(
        weights.items(), key=lambda kv: -kv[1]
    )}
    out: dict = {
        "available": True,
        "method": "hierarchical_risk_parity",
        "weights": ordered,
        "basis": "Realised covariance over the analysis window, Ledoit-Wolf shrunk.",
        "caveats": [
            "A RISK-BALANCED REFERENCE, not a recommendation. It contains no view "
            "on expected returns.",
            "Ignores taxes, exit loads, your existing cost basis and your goals -- "
            "all of which matter for whether moving is worth it.",
            "Estimated from roughly three years of history. Correlations change, "
            "and they change most in the falls this is meant to protect against.",
            "HRP is chosen over mean-variance because it never inverts the "
            "covariance matrix, which is unstable for highly-correlated schemes "
            "on short histories -- not because it is optimal.",
        ],
    }
    # ── Degeneracy warning ────────────────────────────────────────────────
    # HRP balances RISK and knows nothing about return. Mix a liquid fund
    # (volatility ~0.2%) with equity (~15%) and it will put nearly everything
    # in the liquid fund, because that genuinely does minimise risk
    # contribution -- and would also have the investor retire on cash returns.
    # The number is correct; presenting it without this note would not be.
    top_id, top_w = next(iter(ordered.items()))
    if top_w > 0.60:
        out["concentration_warning"] = (
            f"{top_w * 100:.0f}% of this reference allocation sits in a single scheme "
            f"({top_id}). Risk parity balances RISK, not return, so it concentrates "
            f"in the lowest-volatility holding whenever one is much calmer than the "
            f"rest -- typically a liquid or debt fund. That minimises short-term "
            f"fluctuation and would also forgo the growth a long-horizon goal "
            f"depends on. Read it as a risk reference, not a plan."
        )

    if current_weights:
        out["current_weights"] = {k: round(v, 4) for k, v in current_weights.items()}
        out["delta"] = {
            k: round(ordered.get(k, 0.0) - current_weights.get(k, 0.0), 4)
            for k in set(ordered) | set(current_weights)
        }
    return out
