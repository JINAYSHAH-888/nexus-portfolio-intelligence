"""
PLOS v2 -- learned layer weights and quant layers.

What overlap COSTS an investor is that two funds' returns move together, so
the diversification they appear to add is not there. PLOS v1 scored that with
hand-picked weights over ten layers, two of which were the same computation
and three of which were constant for every equity pair. v2 asks the data:

  target   y = FUTURE weekly total-return correlation of the pair
  features   measured strictly BEFORE the target window

and fits non-negative weights (more of any overlap must never LOWER the
score), validated leave-one-scheme-out against v1 and a gradient-boosted
benchmark. The linear model is kept for production when it is competitive,
because every layer's contribution stays visible -- PLOS is never a black box.

Quant layers (from AMFI NAV history, factors = Nifty 50 and Nifty Midcap 50):
  Q1  residual correlation   co-movement left after removing both factors --
                             shared stock-picking, not shared market
  Q2  factor similarity      how alike the two funds' factor exposures are
  Q3  total correlation      plain co-movement of weekly returns
Holdings layers (factsheet):
  H1  weighted overlap   sum of min(w_a, w_b)      H2  Jaccard on names
  H3  top-10 Jaccard                               H4  sector cosine
Metadata:
  M1  same SEBI category
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np
import pandas as pd

FEATURES = [
    "Q1_residual_corr",
    "Q2_factor_similarity",
    "Q3_total_corr",
    "H1_weighted_overlap",
    "H2_jaccard",
    "H3_top10_jaccard",
    "H4_sector_cosine",
    "M1_same_category",
]
RETURN_FEATURES = FEATURES[:3]

DESCRIPTIONS = {
    "Q1_residual_corr": "Co-movement after removing the market and mid-cap factors (shared stock picking)",
    "Q2_factor_similarity": "Similarity of exposure to the Nifty 50 and Nifty Midcap 50 factors",
    "Q3_total_corr": "Correlation of weekly NAV returns",
    "H1_weighted_overlap": "Share of money in the same stocks: sum of min(weight A, weight B)",
    "H2_jaccard": "Share of stock names held in common (Jaccard)",
    "H3_top10_jaccard": "Overlap of the top-10 conviction holdings",
    "H4_sector_cosine": "Similarity of sector allocation (cosine)",
    "M1_same_category": "Same SEBI category",
}

# PLOS v1's weights mapped onto the v2 layers, for the comparison only.
# (v1's C01 = (r+1)/2 of total correlation; C02 and L03 were both sector
# cosine; L06-L08 were constants for equity pairs and add no ranking signal.)
V1_EQUIVALENT = {
    "Q3_total_corr": 0.30,
    "H4_sector_cosine": 0.17,
    "H2_jaccard": 0.10,
    "H3_top10_jaccard": 0.07,
    "H1_weighted_overlap": 0.07,
    "M1_same_category": 0.07,
}


def weekly_log_returns(nav: pd.DataFrame) -> pd.DataFrame:
    # Weekly, Friday close: foreign-equity funds strike NAV on the previous
    # day's overseas close, so DAILY correlations against Indian funds are
    # biased toward zero by the one-day lag.
    w = nav.resample("W-FRI").last()
    return np.log(w).diff()


@dataclass
class ReturnFeatures:
    resid_corr: pd.DataFrame
    total_corr: pd.DataFrame
    betas: pd.DataFrame  # scheme x [b_nifty, b_mid]
    n_weeks: pd.DataFrame


def return_features(
    nav: pd.DataFrame, idx: pd.DataFrame, start, end, min_weeks: int = 52
) -> ReturnFeatures:
    r = weekly_log_returns(nav.loc[start:end])
    f = weekly_log_returns(idx.loc[start:end])[["nifty50", "nifty_midcap50"]]
    # Midcap factor = midcap minus large cap, so the two factors are not
    # nearly collinear (raw indices correlate ~0.85 weekly).
    f = pd.DataFrame({"mkt": f["nifty50"], "smb": f["nifty_midcap50"] - f["nifty50"]})
    df = r.join(f, how="inner")
    cols = [c for c in r.columns if df[c].notna().sum() >= min_weeks]
    resid, betas = {}, {}
    for c in cols:
        d = df[[c, "mkt", "smb"]].dropna()
        X = np.column_stack([np.ones(len(d)), d["mkt"], d["smb"]])
        b, *_ = np.linalg.lstsq(X, d[c].values, rcond=None)
        resid[c] = pd.Series(d[c].values - X @ b, index=d.index)
        betas[c] = b[1:]
    R = pd.DataFrame(resid)
    raw = df[cols]
    n = raw.notna().astype(int).T @ raw.notna().astype(int)
    return ReturnFeatures(
        resid_corr=R.corr(min_periods=min_weeks),
        total_corr=raw.corr(min_periods=min_weeks),
        betas=pd.DataFrame(betas, index=["b_mkt", "b_smb"]).T,
        n_weeks=n,
    )


def factor_similarity(ba: np.ndarray, bb: np.ndarray) -> float:
    # exp(-distance): identical exposures -> 1; an equity fund (beta ~1) vs a
    # bond fund (beta ~0) -> ~0.37 at distance 1, shrinking from there.
    return float(math.exp(-float(np.linalg.norm(np.asarray(ba) - np.asarray(bb)))))


def pair_return_features(rf: ReturnFeatures, a: str, b: str) -> dict[str, float] | None:
    if a not in rf.betas.index or b not in rf.betas.index:
        return None
    rc = rf.resid_corr.loc[a, b]
    tc = rf.total_corr.loc[a, b]
    if pd.isna(rc) or pd.isna(tc):
        return None
    return {
        "Q1_residual_corr": max(0.0, float(rc)),
        "Q2_factor_similarity": factor_similarity(rf.betas.loc[a].values, rf.betas.loc[b].values),
        "Q3_total_corr": max(0.0, float(tc)),
    }


def score(features: dict[str, float], weights: dict[str, float]) -> tuple[float, dict[str, float]]:
    """Weighted mean over the features PRESENT, weights renormalised -- a
    young fund with no return history is scored on holdings alone rather
    than having the missing layers count as zero overlap."""
    present = {k: w for k, w in weights.items() if k in features and w > 0}
    tot = sum(present.values())
    if tot <= 0:
        return 0.0, {}
    eff = {k: w / tot for k, w in present.items()}
    return float(sum(features[k] * w for k, w in eff.items())), eff


def effective_number_of_funds(
    ids: list[str], weights: dict[str, float], sim: dict[tuple[str, str], float]
) -> float:
    """1 / (w' C w), C = predicted pairwise correlations with ones on the
    diagonal: the squared diversification ratio of Choueifaty & Coignard
    (2008) for equal-volatility funds, read as the effective number of
    independent funds. Identical funds -> 1; independent, equal-weighted -> N.
    (Meucci's minimum-torsion ENB was evaluated and rejected for this label:
    for two near-identical funds plus one independent fund it reports ~2.9,
    because it measures how evenly risk is spread, not redundancy.)"""
    w = np.array([weights.get(i, 0.0) for i in ids], float)
    if w.sum() <= 0:
        w = np.ones(len(ids))
    w = w / w.sum()
    S = np.eye(len(ids))
    for i, a in enumerate(ids):
        for j, b in enumerate(ids):
            if i != j:
                S[i, j] = sim.get((min(a, b), max(a, b)), 0.0)
    return float(1.0 / (w @ S @ w))


def redundancy_clusters(
    ids: list[str], sim: dict[tuple[str, str], float], threshold: float = 0.6
) -> list[list[str]]:
    """Average-linkage clusters of funds whose learned similarity exceeds
    `threshold` -- groups that behave like ONE fund."""
    if len(ids) < 2:
        return [ids]
    from scipy.cluster.hierarchy import fcluster, linkage
    from scipy.spatial.distance import squareform

    D = np.zeros((len(ids), len(ids)))
    for i, a in enumerate(ids):
        for j, b in enumerate(ids):
            if i != j:
                D[i, j] = 1.0 - sim.get((min(a, b), max(a, b)), 0.0)
    labels = fcluster(
        linkage(squareform(D, checks=False), "average"), t=1 - threshold, criterion="distance"
    )
    groups: dict[int, list[str]] = {}
    for sid, lab in zip(ids, labels, strict=True):
        groups.setdefault(int(lab), []).append(sid)
    return sorted(groups.values(), key=len, reverse=True)


# ── Monotone additive model (production) ──────────────────────────────────────
# Boosted depth-1 trees with a monotone-increasing constraint per layer. Depth
# 1 makes the model exactly additive -- f(x) = base + sum_i g_i(x_i) -- so each
# layer's contribution is reported exactly, and monotone means more overlap on
# any layer can never lower the score. Exported as per-layer step curves on a
# grid (plain JSON), never as a pickle.
GRID = np.linspace(0.0, 1.0, 201)


def fit_additive(X: np.ndarray, y: np.ndarray, seed: int = 0):
    from sklearn.ensemble import HistGradientBoostingRegressor

    return HistGradientBoostingRegressor(
        max_depth=1,
        max_iter=400,
        learning_rate=0.05,
        monotonic_cst=[1] * X.shape[1],
        random_state=seed,
    ).fit(X, y)


def export_additive(model, n_features: int) -> dict:
    zero = np.zeros((1, n_features))
    base = float(model.predict(zero)[0])
    curves = []
    for i in range(n_features):
        Xg = np.zeros((len(GRID), n_features))
        Xg[:, i] = GRID
        curves.append((model.predict(Xg) - base).round(5).tolist())
    return {
        "base": round(base, 5),
        "grid_points": len(GRID),
        "curves": dict(zip(FEATURES, curves, strict=True)),
    }


def additive_contributions(features: dict[str, float], art: dict) -> tuple[float, dict[str, float]]:
    """(predicted future correlation in [0,1], {layer: contribution}). A
    missing layer contributes nothing (its curve's value at 0 is 0)."""
    out = {}
    for f, curve in art["curves"].items():
        if f in features:
            x = min(max(float(features[f]), 0.0), 1.0)
            out[f] = float(np.interp(x, GRID, curve))
    pred = art["base"] + sum(out.values())
    return float(min(max(pred, 0.0), 1.0)), out


# ══════════════════════════════════════════════════════════════════════════════
#  PLOS v3 -- quant layers, rolling-origin training, exact Shapley contributions
# ══════════════════════════════════════════════════════════════════════════════
FEATURES_V3 = [
    "Q3_total_corr",
    "Q1_residual_corr",
    "Q2_factor_similarity",
    "Q4_holdings_implied_corr",
    "Q5_style_similarity",
    "H1_weighted_overlap",
    "H2_jaccard",
    "H3_top10_jaccard",
    "H4_sector_cosine",
    "M1_same_category",
]
DESCRIPTIONS.update(
    {
        "Q4_holdings_implied_corr": "Correlation implied by TODAY's holdings: weights x shrunk stock covariance",
        "Q5_style_similarity": "Similarity of sector-style exposures (Sharpe style analysis on 9 Nifty sector indices)",
    }
)
STYLE_INDICES = [
    "bank",
    "it",
    "pharma",
    "fmcg",
    "auto",
    "metal",
    "energy",
    "infra",
    "nifty_midcap100",
]


def _norm_name(s: str) -> str:
    import re

    return re.sub(r"[^a-z0-9]", "", s.lower().rstrip("*"))


def holdings_vectors(
    holdings: dict[str, list[dict]], ticker_map: dict[str, str], tickers: list[str]
) -> tuple[dict[str, pd.Series], dict[str, float]]:
    """Fund -> weight vector over priced stocks (renormalised), plus the share
    of disclosed weight that is priced (coverage)."""
    nm = {_norm_name(k): v for k, v in ticker_map.items()}
    have = set(tickers)
    vecs, cov = {}, {}
    for sid, hs in holdings.items():
        w: dict[str, float] = {}
        tot = 0.0
        for h in hs:
            wt = float(h.get("weight", 0) or 0)
            if wt <= 0:
                continue
            tot += wt
            t = nm.get(_norm_name(h.get("name", "")))
            if t in have:
                w[t] = w.get(t, 0.0) + wt
        if w and tot > 0:
            s = pd.Series(w)
            cov[sid] = float(s.sum() / tot)
            vecs[sid] = s / s.sum()
    return vecs, cov


def holdings_implied_corr(
    vecs: dict[str, pd.Series], stock_weekly: pd.DataFrame, min_weeks: int = 52
) -> pd.DataFrame:
    """rho_ab = w_a' S w_b / sqrt(w_a' S w_a * w_b' S w_b), S = Ledoit-Wolf
    shrunk covariance of weekly stock returns (N ~ 190 stocks vs ~100 weeks:
    the sample covariance is singular, shrinkage is not optional)."""
    from sklearn.covariance import LedoitWolf

    cols = [c for c in stock_weekly if stock_weekly[c].notna().sum() >= min_weeks]
    X = stock_weekly[cols]
    X = X.fillna(X.mean())  # late listings: mean-fill only the missing weeks
    S = LedoitWolf().fit(X.values).covariance_
    idx = {c: i for i, c in enumerate(cols)}
    ids = []
    W = []
    for sid, v in vecs.items():
        v = v[[t for t in v.index if t in idx]]
        if v.sum() <= 0.5:  # under half the fund priced in this window: skip
            continue
        row = np.zeros(len(cols))
        for t, wt in (v / v.sum()).items():
            row[idx[t]] = wt
        ids.append(sid)
        W.append(row)
    W = np.array(W)
    C = W @ S @ W.T
    d = np.sqrt(np.diag(C))
    return pd.DataFrame(C / np.outer(d, d), index=ids, columns=ids)


def style_weights(
    fund_weekly: pd.DataFrame, factor_weekly: pd.DataFrame, min_weeks: int = 52
) -> pd.DataFrame:
    """Sharpe returns-based style analysis: non-negative weights on sector
    indices summing to one, fitted per fund (NNLS with a stiff sum-to-one row)."""
    from scipy.optimize import nnls

    F = factor_weekly[[c for c in STYLE_INDICES if c in factor_weekly]]
    out = {}
    for sid in fund_weekly:
        d = pd.concat([fund_weekly[sid], F], axis=1).dropna()
        if len(d) < min_weeks:
            continue
        A = d[F.columns].values
        y = d[sid].values
        lam = 100.0 * np.abs(A).mean()
        A2 = np.vstack([A, lam * np.ones(A.shape[1])])
        y2 = np.r_[y, lam]
        w, _ = nnls(A2, y2)
        out[sid] = w / w.sum() if w.sum() > 0 else w
    return pd.DataFrame(out, index=F.columns).T


def style_similarity(wa: np.ndarray, wb: np.ndarray) -> float:
    return float(1 - 0.5 * np.abs(np.asarray(wa) - np.asarray(wb)).sum())


def fisher_z(r):
    return np.arctanh(np.clip(r, -0.999, 0.999))


# ── PLOS v3 scoring + exact Shapley ─────────────────────────────────────────
Z_FEATURES = ("Q3_total_corr", "Q1_residual_corr", "Q4_holdings_implied_corr")


def v3_feature_vector(feats: dict[str, float], art: dict) -> np.ndarray:
    """Raw layer readings -> the model's input space: correlations in
    Fisher-z, a missing holdings-implied correlation (foreign funds, no
    priced holdings) replaced by the pair's own return correlation, other
    gaps by the training median."""
    x = []
    for f in FEATURES_V3:
        v = feats.get(f)
        if (v is None or np.isnan(v)) and f == "Q4_holdings_implied_corr":
            v = feats.get("Q3_total_corr")
        if v is None or np.isnan(v):
            v = art["fill"][f]
        elif f in Z_FEATURES:
            v = float(fisher_z(v))
        x.append(float(v))
    return np.array(x)


def v3_predict(art: dict, X: np.ndarray) -> np.ndarray:
    """Predicted future correlation, clipped to [0, 1]."""
    z = (np.atleast_2d(X) - np.array(art["x_mean"])) @ np.array(art["weights"]) + art["z_mean"]
    return np.clip(np.tanh(z), 0.0, 1.0)


def shapley(predict, x: np.ndarray, baseline: np.ndarray) -> tuple[float, np.ndarray]:
    """Exact interventional Shapley values of `predict` at x against a
    baseline point (here: 'no overlap on any layer'). 2^M coalitions,
    M = 10 -> 1,024 evaluations. The contributions sum exactly to
    predict(x) - predict(baseline), even through the tanh/clip."""
    from itertools import product
    from math import factorial

    x = np.asarray(x, float)
    b = np.asarray(baseline, float)
    M = len(x)
    masks = np.array(list(product([0, 1], repeat=M)), dtype=bool)
    pts = np.where(masks, x, b)
    vals = dict(zip((m.tobytes() for m in masks), predict(pts), strict=True))
    phi = np.zeros(M)
    w = [factorial(k) * factorial(M - k - 1) / factorial(M) for k in range(M)]
    for m in masks:
        k = int(m.sum())
        base = vals[m.tobytes()]
        for i in np.where(~m)[0]:
            m2 = m.copy()
            m2[i] = True
            phi[i] += w[k] * (vals[m2.tobytes()] - base)
    return float(vals[np.zeros(M, bool).tobytes()]), phi
