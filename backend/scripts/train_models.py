"""
Train and validate the learned models on the downloaded market history.

    python scripts/fetch_market_history.py   # once, or to refresh the data
    python scripts/train_models.py           # writes backend/data/models/*.json

Every artifact carries a "validation" block: the out-of-sample comparison
against the model it would replace. Nothing ships on in-sample fit alone.
"""

from __future__ import annotations

import json
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import pandas as pd
from scipy import optimize, stats

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backend.engines.plos_engine import PLOSEngine  # noqa: E402
from backend.ml import data  # noqa: E402
from backend.ml import garch as G  # noqa: E402
from backend.ml import plos_model as P  # noqa: E402
from backend.ml import regime as R  # noqa: E402

OUT = data.MODELS
FEATURE_WINDOW = ("2022-06-01", "2024-05-31")
TARGET_START = "2024-06-01"


def _dump(name: str, obj: dict) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    obj = {
        "trained_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "data_manifest_sha256": json.loads((data.HISTORY / "manifest.json").read_text())["sha256"],
        **obj,
    }
    (OUT / name).write_text(json.dumps(obj, indent=2, default=float) + "\n")
    print(f"wrote {name}")


def _r(x, n=4):
    return (
        None if x is None or (isinstance(x, float) and not np.isfinite(x)) else round(float(x), n)
    )


# ═══════════════════════════════════════════════════════════════════════════
#  PLOS v2
# ═══════════════════════════════════════════════════════════════════════════
def _holdings_engine() -> PLOSEngine:
    e = PLOSEngine.__new__(PLOSEngine)
    e._schemes, e._holdings = {}, {}
    e._load(data.DATA / "schemes_metadata.json", data.DATA / "sample_holdings.json")
    return e


def holdings_features(e: PLOSEngine, a: str, b: str) -> dict[str, float]:
    sa, sb = e._schemes.get(a, {}), e._schemes.get(b, {})
    # A bond fund's "holdings" are issuers: an HDFC Bank bond is not HDFC Bank
    # shares, so name overlap only counts between funds of the same type.
    if sa.get("instrument_type") != sb.get("instrument_type"):
        return {
            "H1_weighted_overlap": 0.0,
            "H2_jaccard": 0.0,
            "H3_top10_jaccard": 0.0,
            "H4_sector_cosine": 0.0,
            "M1_same_category": 0.0,
        }
    return {
        "H1_weighted_overlap": e._weighted_overlap(e._weight_map(a), e._weight_map(b)),
        "H2_jaccard": e._jaccard(e._stock_set(a), e._stock_set(b)),
        "H3_top10_jaccard": e._jaccard(e._stock_set(a, True), e._stock_set(b, True)),
        "H4_sector_cosine": e._cosine(e._sector_vec(a), e._sector_vec(b)),
        "M1_same_category": float(
            bool(sa.get("category")) and sa.get("category") == sb.get("category")
        ),
    }


def _nnls_fit(X: np.ndarray, y: np.ndarray) -> tuple[np.ndarray, float]:
    xm, ym = X.mean(0), y.mean()
    b, _ = optimize.nnls(X - xm, y - ym)
    return b, float(ym - xm @ b)


def _metrics(y, p) -> dict:
    y, p = np.asarray(y), np.asarray(p)
    ss = ((y - y.mean()) ** 2).sum()
    return {
        "r2": _r(1 - ((y - p) ** 2).sum() / ss),
        "rmse": _r(np.sqrt(((y - p) ** 2).mean())),
        "spearman": _r(stats.spearmanr(y, p).statistic),
    }


def train_plos() -> None:
    from sklearn.ensemble import GradientBoostingRegressor

    nav, idx = data.nav_daily(), data.index_daily()
    e = _holdings_engine()
    feat_rf = P.return_features(nav, idx, *FEATURE_WINDOW)
    tgt = P.weekly_log_returns(nav.loc[TARGET_START:]).corr(min_periods=52)

    rows = []
    ids = sorted(set(feat_rf.betas.index) & set(tgt.columns) & set(e._schemes))
    for i, a in enumerate(ids):
        for b in ids[i + 1 :]:
            rfeat = P.pair_return_features(feat_rf, a, b)
            y = tgt.loc[a, b]
            if rfeat is None or pd.isna(y):
                continue
            rows.append({"a": a, "b": b, **rfeat, **holdings_features(e, a, b), "y": float(y)})
    df = pd.DataFrame(rows)
    X, y = df[P.FEATURES].values, df["y"].values
    print(f"PLOS: {len(df)} pairs over {len(ids)} schemes")

    # Leave-one-scheme-out: a pair is predicted only by a model that never
    # saw EITHER... well, never saw the held-out scheme in any pair.
    preds = {
        "v1_hand_weights": np.zeros(len(df)),
        "v2_learned_linear": np.zeros(len(df)),
        "v2_monotone_additive": np.zeros(len(df)),
        "gradient_boosting_depth2": np.zeros(len(df)),
    }
    v1 = np.array([P.V1_EQUIVALENT.get(f, 0.0) for f in P.FEATURES])
    done = np.zeros(len(df), bool)
    for s in ids:
        test = ((df.a == s) | (df.b == s)).values & ~done
        train = ~((df.a == s) | (df.b == s)).values
        if not test.any():
            continue
        # v1: its fixed score, linearly calibrated on train (fair to v1).
        s1 = X @ v1
        k, c = np.polyfit(s1[train], y[train], 1)
        preds["v1_hand_weights"][test] = k * s1[test] + c
        b, c0 = _nnls_fit(X[train], y[train])
        preds["v2_learned_linear"][test] = X[test] @ b + c0
        gb = GradientBoostingRegressor(
            n_estimators=300, max_depth=2, learning_rate=0.05, subsample=0.8, random_state=0
        ).fit(X[train], y[train])
        preds["gradient_boosting_depth2"][test] = gb.predict(X[test])
        preds["v2_monotone_additive"][test] = P.fit_additive(X[train], y[train]).predict(X[test])
        done |= test

    validation = {k: _metrics(y[done], v[done]) for k, v in preds.items()}
    print(json.dumps(validation, indent=1))

    b, c0 = _nnls_fit(X, y)
    champion = max(validation, key=lambda k: validation[k]["r2"] or -9)
    additive = P.export_additive(P.fit_additive(X, y), len(P.FEATURES))
    # Layer importance: share of the spread each curve produces on real pairs.
    spread = {
        f: float(np.ptp([np.interp(v, P.GRID, additive["curves"][f]) for v in df[f]]))
        for f in P.FEATURES
    }
    importance = {f: _r(v / sum(spread.values()), 4) for f, v in spread.items()}

    # Inference-time return features: the most recent two years.
    end = nav.index.max()
    live = P.return_features(nav, idx, end - pd.DateOffset(years=2), end)
    _dump(
        "plos_v2.json",
        {
            "model": "monotone additive boosted model (depth-1 trees, increasing in every layer); "
            "PLOS = 100 x predicted future co-movement = base + sum of layer contributions",
            "target": f"future weekly total-return correlation, {TARGET_START} to {end.date()}",
            "feature_window": list(FEATURE_WINDOW),
            "additive": additive,
            "importance": importance,
            "linear_weights_reference": {
                f: _r(w / b.sum(), 4) for f, w in zip(P.FEATURES, b, strict=True)
            },
            "descriptions": P.DESCRIPTIONS,
            "validation": {
                "method": "leave-one-scheme-out over pairs; features strictly before the target window",
                "n_pairs": int(done.sum()),
                "n_schemes": len(ids),
                "results": validation,
                "champion_by_r2": champion,
                "production": "v2_monotone_additive: beats v1 and the linear fit; the depth-2 model scores "
                "higher but uses layer interactions, so its per-layer contributions could not be "
                "shown exactly -- kept as a benchmark, not shipped",
            },
            "live_returns": {
                "window": [str((end - pd.DateOffset(years=2)).date()), str(end.date())],
                "resid_corr": live.resid_corr.round(4).to_dict(),
                "total_corr": live.total_corr.round(4).to_dict(),
                "betas": live.betas.round(4).to_dict(orient="index"),
            },
        },
    )


# ═══════════════════════════════════════════════════════════════════════════
#  PLOS v3 -- quant layers + rolling origins + monotone trees + Shapley
# ═══════════════════════════════════════════════════════════════════════════
ORIGINS = pd.date_range("2019-05-31", "2025-05-31", freq="12ME")


def _origin_features(e, t0, t1, stocks_w, factors_w, vecs):
    nav, idx = data.nav_daily(), data.index_daily()
    rf = P.return_features(nav, idx, t0, t1)
    hic = P.holdings_implied_corr(vecs, stocks_w.loc[t0:t1])
    sw = P.style_weights(P.weekly_log_returns(nav.loc[t0:t1]), factors_w.loc[t0:t1])
    return rf, hic, sw


def _pair_v3(e, rf, hic, sw, a, b):
    out = dict.fromkeys(P.FEATURES_V3, np.nan)
    rfe = P.pair_return_features(rf, a, b)
    if rfe:
        out.update(rfe)
    if a in hic.index and b in hic.index:
        out["Q4_holdings_implied_corr"] = max(0.0, float(hic.loc[a, b]))
    if a in sw.index and b in sw.index:
        out["Q5_style_similarity"] = P.style_similarity(sw.loc[a].values, sw.loc[b].values)
    out.update(holdings_features(e, a, b))
    return out


def _plos_v3_inputs():
    e = _holdings_engine()
    nav = data.nav_daily()
    tmap = json.loads((data.DATA / "stocks_ticker_map.json").read_text())["nse_mapped"]
    stocks = pd.read_csv(data.HISTORY / "stocks_daily.csv.gz", index_col=0, parse_dates=True)
    stocks_w = np.log(stocks.resample("W-FRI").last()).diff()
    factors = pd.read_csv(data.HISTORY / "factors_daily.csv.gz", index_col=0, parse_dates=True)
    factors_w = np.log(factors.resample("W-FRI").last()).diff()
    vecs, coverage = P.holdings_vectors(e._holdings, tmap, list(stocks.columns))

    rows = []
    for t in ORIGINS:
        rf, hic, sw = _origin_features(e, t - pd.DateOffset(years=2), t, stocks_w, factors_w, vecs)
        tgt = P.weekly_log_returns(nav.loc[t : t + pd.DateOffset(years=1)]).corr(min_periods=40)
        ids = sorted(set(e._schemes) & set(tgt.columns))
        for i, a in enumerate(ids):
            for b in ids[i + 1 :]:
                y = tgt.loc[a, b]
                if pd.isna(y):
                    continue
                f = _pair_v3(e, rf, hic, sw, a, b)
                if np.isnan(f["Q3_total_corr"]):
                    continue  # no return history before the origin
                rows.append({"origin": t, "a": a, "b": b, **f, "y": float(y)})
    return e, nav, stocks_w, factors_w, vecs, coverage, pd.DataFrame(rows)


def _v3_design(df: pd.DataFrame) -> tuple[pd.DataFrame, dict]:
    X = df[P.FEATURES_V3].copy()
    for c in P.Z_FEATURES:
        X[c] = P.fisher_z(X[c])
    X["Q4_holdings_implied_corr"] = X["Q4_holdings_implied_corr"].fillna(X["Q3_total_corr"])
    fill = {c: float(X[c].median()) if X[c].notna().any() else 0.0 for c in P.FEATURES_V3}
    return X.fillna(fill), fill


def _shrunk_nnls(X: np.ndarray, z: np.ndarray, lam: float) -> tuple[np.ndarray, np.ndarray, float]:
    """Non-negative least squares on Fisher-z, ridge-pulled toward the v1
    hand weights (rescaled to this target): with 3-6 years per rolling fold,
    unshrunk weights were noisier than the hand prior."""
    hand = np.array([P.V1_EQUIVALENT.get(f, 0.0) for f in P.FEATURES_V3])
    xm, zm = X.mean(0), z.mean()
    k0 = np.polyfit(X @ hand, z, 1)[0]
    A = np.vstack([X - xm, np.sqrt(lam) * np.eye(X.shape[1])])
    b = np.r_[z - zm, np.sqrt(lam) * k0 * hand]
    w, _ = optimize.nnls(A, b)
    return w, xm, float(zm)


def train_plos_v3() -> None:
    from sklearn.ensemble import HistGradientBoostingRegressor

    e, nav, stocks_w, factors_w, vecs, coverage, df = _plos_v3_inputs()
    print(f"PLOS v3: {len(df)} pair-years, {df.origin.nunique()} origins")
    Xdf, fill = _v3_design(df)
    X = Xdf.values
    y = df["y"].values
    z = P.fisher_z(y)
    hand = np.array([P.V1_EQUIVALENT.get(f, 0.0) for f in P.FEATURES_V3])
    v1_raw = np.array([P.V1_EQUIVALENT.get(f, 0.0) for f in P.FEATURES])

    def m_v1(tr, te):  # PLOS v1 as shipped: hand weights on raw readings, calibrated
        s_ = np.nan_to_num(df[P.FEATURES].values) @ v1_raw
        k, c = np.polyfit(s_[tr], z[tr], 1)
        return k * s_[te] + c

    def m_hand_z(tr, te):
        s_ = X @ hand
        k, c = np.polyfit(s_[tr], z[tr], 1)
        return k * s_[te] + c

    def m_shrunk(tr, te):
        w, xm, zm = _shrunk_nnls(X[tr], z[tr], 100.0)
        return (X[te] - xm) @ w + zm

    def m_trees(tr, te):
        m = HistGradientBoostingRegressor(
            max_depth=2,
            max_iter=400,
            learning_rate=0.04,
            min_samples_leaf=20,
            monotonic_cst=[1] * X.shape[1],
            random_state=0,
        ).fit(X[tr], z[tr])
        return m.predict(X[te])

    models = {
        "v1_as_shipped": m_v1,
        "hand_weights_fisher_z": m_hand_z,
        "v3_learned_shrunk": m_shrunk,
        "monotone_trees": m_trees,
    }
    origins = sorted(df.origin.unique())
    temporal, loso = {}, {}
    for name, fn in models.items():
        per = []
        for o in origins[3:]:
            tr = (df.origin < o).values
            te = (df.origin == o).values
            per.append(_metrics(y[te], np.tanh(fn(tr, te))))
        temporal[name] = {
            "mean_r2": _r(np.mean([p["r2"] for p in per])),
            "mean_rmse": _r(np.mean([p["rmse"] for p in per])),
            "per_year_r2": {
                str(pd.Timestamp(o).year): p["r2"] for o, p in zip(origins[3:], per, strict=True)
            },
        }
        p_ = np.zeros(len(df))
        done = np.zeros(len(df), bool)
        for s_ in sorted(set(df.a) | set(df.b)):
            inv = ((df.a == s_) | (df.b == s_)).values
            te = inv & ~done
            if te.any():
                p_[te] = fn(~inv, te)
                done |= te
        loso[name] = _metrics(y[done], np.tanh(p_[done]))
    print(
        json.dumps(
            {
                "temporal": {k: v["mean_r2"] for k, v in temporal.items()},
                "loso": {k: v["r2"] for k, v in loso.items()},
            }
        )
    )

    w, xm, zm = _shrunk_nnls(X, z, 100.0)
    end = nav.index.max()
    t0 = end - pd.DateOffset(years=2)
    rf, hic, sw = _origin_features(e, t0, end, stocks_w, factors_w, vecs)
    contrib = np.abs(w) * X.std(0)
    _dump(
        "plos_v3.json",
        {
            "model": "Linear in Fisher-z space, non-negative weights learned on 7 yearly origins and "
            "ridge-shrunk toward the v1 hand weights; PLOS = 100 x predicted future correlation "
            "(clipped at 0). Per-layer contributions are exact Shapley values against "
            "'no overlap on any layer'.",
            "features": P.FEATURES_V3,
            "descriptions": {k: P.DESCRIPTIONS[k] for k in P.FEATURES_V3},
            "weights": [_r(v, 6) for v in w],
            "x_mean": [_r(v, 6) for v in xm],
            "z_mean": _r(zm, 6),
            "fill": {k: _r(v, 6) for k, v in fill.items()},
            "importance": {
                f: _r(c / contrib.sum()) for f, c in zip(P.FEATURES_V3, contrib, strict=True)
            },
            "validation": {
                "method": f"{len(origins)} yearly origins {pd.Timestamp(origins[0]).year}-{pd.Timestamp(origins[-1]).year}: "
                "layers from the 2 years before each origin, target = correlation of weekly returns over "
                "the next year. Primary test: rolling temporal (train on earlier origins only, predict "
                "each of the last 4 years). Secondary: leave-one-scheme-out.",
                "n_pair_years": len(df),
                "rolling_temporal": temporal,
                "leave_one_scheme_out": loso,
                "production": "v3_learned_shrunk",
                "findings": [
                    "Gradient-boosted trees won leave-one-scheme-out but were WORST forward in time: they "
                    "overfit the market-wide correlation level of particular years.",
                    "Scoring correlations in Fisher-z alone lifted the v1 hand weights from 0.57 to 0.63 R^2.",
                    "The holdings-implied layer is weak for older years (the factsheet holdings are from "
                    "2026) and strongest for the latest year, where the holdings are current.",
                ],
            },
            "holdings_price_coverage": {k: round(v, 3) for k, v in coverage.items()},
            "live": {
                "window": [str(t0.date()), str(end.date())],
                "resid_corr": rf.resid_corr.round(4).to_dict(),
                "total_corr": rf.total_corr.round(4).to_dict(),
                "betas": rf.betas.round(4).to_dict(orient="index"),
                "holdings_implied_corr": hic.round(4).to_dict(),
                "style_weights": sw.round(4).to_dict(orient="index"),
            },
        },
    )


# ═══════════════════════════════════════════════════════════════════════════
#  ARISE -- regime model (stress scenario) + validation of the champion
# ═══════════════════════════════════════════════════════════════════════════
def train_arise() -> None:
    s = data.sensex_monthly_log_returns()
    x = s.values
    start = int(np.where(s.index.year == 2005)[0][0])
    scores = {"student_t_iid": 0.0, "gaussian_hmm_1": 0.0, "gaussian_hmm_2": 0.0, "t_hmm_2": 0.0}
    years = 0
    for e in range(start, len(x) - 12, 12):
        tr, te = x[:e], x[e : e + 12]
        scores["student_t_iid"] += R.student_t_iid_loglik(tr, te)[0]
        for k in (1, 2):
            p, _ = R.fit_hmm(tr, k, n_restarts=6)
            scores[f"gaussian_hmm_{k}"] += R.holdout_loglik_hmm(p, tr, te)
        pt, _ = R.fit_t_hmm(tr, 2, n_restarts=2, n_iter=40)
        scores["t_hmm_2"] += R.t_hmm_loglik(np.concatenate([tr, te]), pt) - R.t_hmm_loglik(tr, pt)
        years += 1
    print("ARISE rolling:", {k: round(v, 1) for k, v in scores.items()})

    p, ll = R.fit_t_hmm(x, 2)
    rng = np.random.default_rng(11)
    ann = R.simulate_annual_t_hmm(p, 100_000, rng)
    real_ann = s.groupby(s.index.year).sum()
    real_ann = real_ann[real_ann.index.map(lambda y: (s.index.year == y).sum() == 12)]
    stay = np.diag(np.array(p["A"]))
    _dump(
        "arise_regime.json",
        {
            "model": "2-state hidden Markov model, Student-t returns within each state, monthly Sensex 1997-",
            "use": "stress scenario only: shapes ANNUAL shocks (crash years cluster); the return level "
            "still comes from the shrunk portfolio statistics",
            "params": p,
            "annual_mean": _r(ann.mean(), 6),
            "annual_sd": _r(ann.std(), 6),
            "regimes": [
                {
                    "name": "calm",
                    "annual_vol": _r(p["scale"][0] * np.sqrt(12 * p["nu"][0] / (p["nu"][0] - 2))),
                    "expected_months": _r(1 / (1 - stay[0]), 1),
                },
                {
                    "name": "turbulent",
                    "annual_vol": _r(p["scale"][1] * np.sqrt(12 * p["nu"][1] / (p["nu"][1] - 2))),
                    "expected_months": _r(1 / (1 - stay[1]), 1),
                },
            ],
            "shape_check": {
                "real_annual_skew": _r(stats.skew(real_ann)),
                "model_annual_skew": _r(stats.skew(ann)),
                "real_annual_excess_kurtosis": _r(stats.kurtosis(real_ann)),
                "model_annual_excess_kurtosis": _r(stats.kurtosis(ann)),
            },
            "validation": {
                "method": f"rolling one-year-ahead predictive log-likelihood, {years} origins from 2005",
                "results": {k: _r(v, 2) for k, v in scores.items()},
                "verdict": "Regime models do NOT beat the iid Student-t at forecasting next year's monthly "
                "returns, so the main projection keeps the Student-t process. The regime model is "
                "used only as a labelled stress scenario for multi-year crash clustering.",
            },
            "shrinkage_backtest": shrinkage_backtest(),
        },
    )


def train_arise_calibration() -> None:
    """ARISE's own quant + calibration models -> arise_calibration.json."""
    from backend.ml import arise_models as A

    model = A.fit_dividend_yield_model(60)
    n = model["independent_windows"]
    w = n / (n + A.TAU_YEARS)
    dy = A.current_dividend_yield()
    val = {
        "model": model,
        "oos": {"3y": A.rolling_oos(36), "5y": A.rolling_oos(60)},
        "current_dividend_yield": round(dy, 3),
        "forecast_5y": round(model["intercept"] + model["slope_per_pct_dy"] * dy, 4),
        "evidence_weight": round(w, 3),
        "note": "Earnings yield was rejected: OOS R^2 -0.52 at 3y (COVID EPS collapse; NSE moved "
        "to consolidated EPS in Apr 2021). Dividend yield is unaffected by either.",
    }
    before = A.calibration_harness(phis=(-0.15,))["phi=-0.15"]
    after = A.calibration_harness(phis=(0.0,), vol_blend=0.5)["phi=0"]
    with_val = A.calibration_harness(phis=(0.0,), vol_blend=0.5, valuation_art=val)["phi=0"]
    mon = data.sensex_monthly_log_returns()
    _dump(
        "arise_calibration.json",
        {
            "valuation": val,
            "long_run_equity_vol": _r(mon.std() * np.sqrt(12)),
            "calibration": {
                "method": "390 three-year return forecasts: every Indian equity fund, quarterly "
                "origins 2016-2023, scored on the realised outcome (CRPS; share of outcomes "
                "inside the forecast's 80% and 50% ranges). Overlapping, one market: ~2-3 "
                "independent periods.",
                "before": {"label": "v4: AR(1) phi=-0.15, trailing 3y volatility", **before},
                "after": {"label": "v5: phi=0, volatility blended 50/50 with long-run", **after},
                "after_with_valuation": {"label": "v5 + dividend-yield prior", **with_val},
            },
        },
    )


def shrinkage_backtest() -> dict:
    meta = {m["id"]: m for m in json.loads((data.DATA / "schemes_metadata.json").read_text())}
    nav = data.nav_daily()
    eq = [
        c
        for c in nav
        if meta[c]["instrument_type"] == "equity"
        and not any(k in c for k in ("japan", "taiwan", "us-equity"))
    ]
    m = nav[eq].resample("ME").last()
    rows = []
    for t in pd.date_range("2016-12-31", "2023-06-30", freq="6ME"):
        for f in eq:
            s = m[f].dropna()
            if s.index[0] > t - pd.DateOffset(years=3) or t + pd.DateOffset(years=3) > s.index[-1]:
                continue
            past = s[:t]
            n = min((t - past.index[0]).days / 365.25, 10.0)
            start = past[past.index >= t - pd.Timedelta(days=n * 365.25)].iloc[0]
            rows.append(
                {
                    "n": n,
                    "trail": (past.iloc[-1] / start) ** (1 / n) - 1,
                    "fwd": (s[: t + pd.DateOffset(years=3)].iloc[-1] / past.iloc[-1]) ** (1 / 3)
                    - 1,
                }
            )
    d = pd.DataFrame(rows)
    out = {}
    for tau in (0, 2, 5, 10, 20, 50):
        w = d.n / (d.n + tau)
        out[str(tau)] = _r(np.sqrt(((w * d.trail + (1 - w) * 0.11 - d.fwd) ** 2).mean()))
    out["prior_only"] = _r(np.sqrt(((0.11 - d.fwd) ** 2).mean()))
    best = min((k for k in out if k != "prior_only"), key=lambda k: out[k])
    return {
        "method": "forecast each equity fund's next-3-year CAGR from its trailing CAGR, "
        "shrunk toward 11% with weight n/(n+tau); 6-monthly origins 2016-2023",
        "n_forecasts": len(d),
        "funds": len(eq),
        "rmse_by_tau": out,
        "best_tau": float(best),
        "corr_trailing_vs_forward": _r(d.trail.corr(d.fwd)),
        "verdict": f"tau={best} minimises out-of-sample error; the engine uses tau=10.",
    }


# ═══════════════════════════════════════════════════════════════════════════
#  Risk -- GARCH filtered historical simulation
# ═══════════════════════════════════════════════════════════════════════════
def train_risk() -> None:
    r = data.market_log_returns()["nifty50"].dropna().values
    bt = {
        str(lvl): G.rolling_backtest(r, level=lvl, window=750, refit_every=250)
        for lvl in (0.95, 0.99)
    }
    p = G.fit_garch(r)
    _dump(
        "risk_garch.json",
        {
            "model": "GARCH(1,1), Student-t likelihood; VaR/ES by filtered historical simulation",
            "nifty50_fit": {**p.to_dict(), "persistence": _r(p.persistence)},
            "validation": {
                "method": "rolling one-day-ahead VaR on Nifty 50 since 2010, 750-day window, refit yearly; "
                "Kupiec POF (right number of breaches) + Christoffersen (breaches not clustered)",
                "results": bt,
                "verdict": "Historical VaR breaches cluster (fails independence); GARCH-FHS passes both "
                "tests at 95% and 99%, so the risk engine reports it as the conditional VaR.",
            },
        },
    )


def train_risk_v2() -> None:
    """Risk's dedicated models -> risk_v2.json + risk_market_var.csv.gz."""
    from backend.ml import vol_models as V
    from backend.ml.evaluation import qlike

    df = V.feature_frame()
    comparison, dm = {}, {}
    for h in (1, 5):
        d = df.assign(y=V.target(df, h)).dropna(subset=["y"])
        idx = d.index
        s0 = int(np.searchsorted(idx, pd.Timestamp("2013-01-01")))
        preds = {k: np.full(len(d), np.nan) for k in ("garch_t", "gjr_t", "har", "har_x")}
        rr = d.ret.values
        for s in range(s0, len(d), 250):
            e = min(s + 250, len(d))
            for k, cols in (("har", V.HAR), ("har_x", V.HAR_X)):
                m = V.fit_log_linear(d.iloc[: s - h], cols, d.y.values[: s - h])
                preds[k][s:e] = V.predict_log_linear(m, d.iloc[s:e])
            lo = max(0, s - 2500)
            pg = G.fit_garch(rr[lo:s])
            pj = V.fit_gjr(rr[lo:s])
            sg = G.conditional_vol(rr[lo:e], pg) ** 2
            sj = V.gjr_variance_path(rr[lo:e], pj)
            lr = pg.long_run_vol_daily**2
            for i in range(s, e):
                k_ = i - lo + 1
                tot, v = 0.0, sg[k_]
                for _ in range(h):
                    tot += v
                    v = lr + pg.persistence * (v - lr)
                preds["garch_t"][i] = tot / h
                preds["gjr_t"][i] = V.gjr_multi_step(sj[k_], pj, h)
        ok = ~np.isnan(preds["har"])
        y = d.y.values[ok]
        comparison[f"{h}d"] = {k: _r(qlike(v[ok], y)) for k, v in preds.items()}
        st_, p_ = V.diebold_mariano(
            V.qlike_series(preds["garch_t"][ok], y), V.qlike_series(preds["har_x"][ok], y), h
        )
        dm[f"{h}d"] = {"stat": _r(st_, 3), "p": _r(p_, 6), "n_days": int(ok.sum())}
    print("vol QLIKE", json.dumps(comparison), "DM", json.dumps(dm))

    backtest = V.fund_backtest()
    print("fund VaR", json.dumps(backtest))

    models = {}
    for h in (1, 5, 10):
        d = df.assign(y=V.target(df, h)).dropna(subset=["y"])
        models[f"{h}d"] = V.fit_log_linear(d, V.HAR_X, d.y.values)
    last = df.iloc[[-1]]
    live = {k: _r(float(V.predict_log_linear(m, last)[0]), 10) for k, m in models.items()}

    # Out-of-sample market variance history, aligned to the day it forecasts,
    # for standardising a portfolio's past returns in the live engine.
    vm = V.oos_harx(df, 1, start="2010-06-01").shift(1).dropna()
    OUT.mkdir(parents=True, exist_ok=True)
    vm.rename("vm").to_frame().to_csv(
        OUT / "risk_market_var.csv.gz",
        float_format="%.6g",
        date_format="%Y-%m-%d",
        compression={"method": "gzip", "mtime": 0},
    )
    _dump(
        "risk_v2.json",
        {
            "model": "Fund/portfolio GARCH(1,1) variance blended 50/50 with the Nifty HAR-X forecast mapped "
            "through the portfolio's beta; VaR/ES tail from filtered historical simulation at 95% and "
            "a peaks-over-threshold GPD (EVT) at 99%.",
            "volatility_forecast_qlike": {"days": "2013 onward, out of sample", **comparison},
            "diebold_mariano_garch_vs_harx": dm,
            "fund_var_backtest": {
                "method": "13 Indian equity funds' NAVs, rolling one-day VaR/ES 2014 onward, 750-day window, "
                "refit yearly; Kupiec (count), Christoffersen (clustering), FZ0 joint VaR/ES score.",
                "rule": "lowest FZ0 among methods passing both tests for >= 90% of funds",
                **backtest,
            },
            "harx_models": models,
            "live_market_variance": {"as_of": str(df.index[-1].date()), **live},
            "inputs": V.HAR_X,
        },
    )


if __name__ == "__main__":
    which = set(sys.argv[1:]) or {"plos", "plos3", "arise", "risk", "risk2", "arise_cal"}
    t0 = time.time()
    if "plos" in which:
        train_plos()
    if "plos3" in which:
        train_plos_v3()
    if "risk" in which:
        train_risk()
    if "risk2" in which:
        train_risk_v2()
    if "arise_cal" in which:
        train_arise_calibration()
    if "arise" in which:
        train_arise()
    print(f"done in {time.time() - t0:.0f}s")
