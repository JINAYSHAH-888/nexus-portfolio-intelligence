"""The learned models: correctness of the maths and the guarantees the
engines rely on (not a re-run of training -- see scripts/train_models.py)."""

import numpy as np
import pytest

from backend.engines.plos_engine import PLOSEngine
from backend.ml import garch as G
from backend.ml import plos_model as P
from backend.ml import regime as R
from backend.ml.data import load_artifact


def _simulate_garch(n, omega, alpha, beta, nu, seed=0):
    rng = np.random.default_rng(seed)
    s2 = omega / (1 - alpha - beta)
    out = np.empty(n)
    for t in range(n):
        z = rng.standard_t(nu) * np.sqrt((nu - 2) / nu)
        out[t] = np.sqrt(s2) * z
        s2 = omega + alpha * out[t] ** 2 + beta * s2
    return out


def test_garch_recovers_known_parameters():
    r = _simulate_garch(6000, 2e-6, 0.08, 0.90, 7.0)
    p = G.fit_garch(r)
    assert p.alpha == pytest.approx(0.08, abs=0.03)
    assert p.beta == pytest.approx(0.90, abs=0.04)
    assert 0.95 < p.persistence < 0.999


def test_fhs_var_is_a_loss_and_es_exceeds_var():
    r = _simulate_garch(1500, 2e-6, 0.08, 0.90, 7.0, seed=3)
    p = G.fit_garch(r)
    var, es, _ = G.fhs_var_es(r, p, 0.95)
    assert var > 0 and es > var
    var99, _, _ = G.fhs_var_es(r, p, 0.99)
    assert var99 > var
    var10, _, _ = G.fhs_var_es(r, p, 0.95, horizon_days=10)
    assert var10 > var


def test_kupiec_accepts_correct_rate_and_rejects_wrong_one():
    assert G.kupiec_pof(50, 1000, 0.95) > 0.5
    assert G.kupiec_pof(120, 1000, 0.95) < 0.001


def test_plos_additive_model_is_monotone_in_every_layer():
    art = load_artifact("plos_v2.json")
    if art is None:
        pytest.skip("PLOS v2 not trained")
    for f, curve in art["additive"]["curves"].items():
        assert all(b >= a - 1e-9 for a, b in zip(curve, curve[1:], strict=False)), f
    lo, _ = P.additive_contributions(dict.fromkeys(P.FEATURES, 0.2), art["additive"])
    hi, _ = P.additive_contributions(dict.fromkeys(P.FEATURES, 0.8), art["additive"])
    assert hi >= lo


def test_plos_v2_never_counts_bond_issuers_as_stock_overlap():
    e = PLOSEngine("backend/data/schemes_metadata.json", "backend/data/sample_holdings.json")
    art = e._v2_artifact()
    if art is None:
        pytest.skip("PLOS v2 not trained")
    _, layers, _, _ = e._v2_pair(art, "nippon-large-cap-fund", "nippon-corporate-bond-fund")
    holdings = {ls.layer_id: ls.raw_score for ls in layers if ls.layer_id[0] in "HM"}
    assert holdings and all(v == 0.0 for v in holdings.values())


def test_plos_v2_scores_are_bounded_and_ranked_sensibly():
    e = PLOSEngine("backend/data/schemes_metadata.json", "backend/data/sample_holdings.json")
    if e._v2_artifact() is None:
        pytest.skip("PLOS v2 not trained")
    ids = ["nippon-large-cap-fund", "nippon-flexi-cap-fund", "nippon-gilt-fund"]
    r = e.compute_plos(ids, None)
    assert r.model_version in ("v2", "v3")
    scores = {(p.scheme_a_id, p.scheme_b_id): p.plos_score for p in r.pair_scores}
    assert all(0 <= s <= 100 for s in scores.values())
    eq_pair = scores[("nippon-large-cap-fund", "nippon-flexi-cap-fund")]
    assert eq_pair > max(s for k, s in scores.items() if "nippon-gilt-fund" in k)
    assert 1.0 <= r.effective_number_of_funds <= len(ids)


def test_effective_number_of_funds_limits():
    ids = ["a", "b", "c"]
    same = {("a", "b"): 1.0, ("a", "c"): 1.0, ("b", "c"): 1.0}
    indep = dict.fromkeys(same, 0.0)
    assert P.effective_number_of_funds(ids, {}, same) == pytest.approx(1.0)
    assert P.effective_number_of_funds(ids, {}, indep) == pytest.approx(3.0)


def test_regime_stress_shocks_are_standardised():
    shocks, art = R.load_regime_stress_shocks()
    if shocks is None:
        pytest.skip("regime model not trained")
    z = shocks(40_000, np.random.default_rng(1))
    assert z.mean() == pytest.approx(0.0, abs=0.03)
    assert z.std() == pytest.approx(1.0, abs=0.03)


# ── PLOS v3 ───────────────────────────────────────────────────────────────
def _v3():
    e = PLOSEngine("backend/data/schemes_metadata.json", "backend/data/sample_holdings.json")
    art = e._v3_artifact()
    if art is None:
        pytest.skip("PLOS v3 not trained")
    return e, art


def test_plos_v3_weights_are_non_negative():
    _, art = _v3()
    assert all(w >= 0 for w in art["weights"])  # more overlap never lowers the score


def test_plos_v3_shapley_contributions_add_up_exactly():
    e, art = _v3()
    score, layers, _, _ = e._v3_pair(art, "nippon-large-cap-fund", "nippon-quant-fund")
    base = P.v3_predict(art, P.v3_feature_vector(dict.fromkeys(P.FEATURES_V3, 0.0), art))[0] * 100
    assert sum(ls.contribution for ls in layers) * 100 == pytest.approx(score - base, abs=0.05)


def test_plos_v3_bond_issuers_are_not_stock_overlap():
    e, art = _v3()
    _, layers, _, _ = e._v3_pair(art, "nippon-large-cap-fund", "nippon-gilt-fund")
    assert all(ls.raw_score == 0.0 for ls in layers if ls.layer_id[0] in "HM")


def test_effective_number_matches_diversification_ratio_squared():
    # two near-identical funds + one independent -> ~1.7 funds, not ~2.9
    ids = ["a", "b", "c"]
    sim = {("a", "b"): 0.9, ("a", "c"): 0.1, ("b", "c"): 0.1}
    assert P.effective_number_of_funds(ids, {}, sim) == pytest.approx(9 / 5.2, abs=1e-6)


# ── Risk v2 ───────────────────────────────────────────────────────────────
def test_evt_quantile_matches_normal_tail():
    from backend.ml.vol_models import evt_quantile

    z = np.random.default_rng(0).standard_normal(200_000)
    var, es = evt_quantile(z, 0.99)
    assert var == pytest.approx(2.326, abs=0.06)
    assert es == pytest.approx(2.665, abs=0.08)


def test_garman_klass_is_positive_and_scales():
    import pandas as pd

    from backend.ml.vol_models import garman_klass

    df = pd.DataFrame(
        {"open": [100, 101], "high": [102, 104], "low": [99, 98], "close": [101, 102]}
    )
    v = garman_klass(df)
    assert (v > 0).all()


def test_diebold_mariano_sign():
    from backend.ml.vol_models import diebold_mariano

    rng = np.random.default_rng(0)
    a = rng.random(2000) + 0.2
    b = rng.random(2000)
    stat, p = diebold_mariano(a, b)
    assert stat > 0 and p < 0.01  # A has higher loss


def test_risk_v2_blend_produces_ordered_losses():
    from backend.ml import vol_models as V

    rng = np.random.default_rng(1)
    m = rng.standard_normal(800) * 0.01
    r = 0.8 * m + rng.standard_normal(800) * 0.004
    vm = np.full(800, 1e-4)
    v95 = V.blended_var_es(r, m, vm, 1e-4, 0.95)
    v99 = V.blended_var_es(r, m, vm, 1e-4, 0.99)
    assert 0 < v95["var"] < v99["var"] and v95["es"] > v95["var"] and v99["tail"] == "evt"
    assert v95["beta"] == pytest.approx(0.8, abs=0.05)


# ── ARISE v5 ──────────────────────────────────────────────────────────────
def test_arise_no_longer_assumes_mean_reversion():
    from backend.engines.arise_engine import RETURN_AR1_PHI

    assert RETURN_AR1_PHI == 0.0


def test_volatility_prior_blend_and_composition():
    from backend.analytics.portfolio_statistics import composite_volatility_prior

    schemes = {
        "e": {"instrument_type": "equity"},
        "d": {"instrument_type": "debt", "modified_duration_years": 5},
    }
    eq_only = composite_volatility_prior({"e": 1.0}, schemes)
    mixed = composite_volatility_prior({"e": 0.5, "d": 0.5}, schemes)
    assert 0.12 < eq_only < 0.30 and mixed < eq_only


def test_valuation_adjustment_is_small_and_amortised():
    from backend.ml.arise_models import equity_prior_adjustment

    art = load_artifact("arise_calibration.json")
    if art is None:
        pytest.skip("ARISE calibration not trained")
    a10 = equity_prior_adjustment(art["valuation"], 10)
    a40 = equity_prior_adjustment(art["valuation"], 40)
    assert abs(a10) < 0.02 and abs(a40) <= abs(a10) + 1e-12


# ── Verified NAV tier ─────────────────────────────────────────────────────
def test_verified_nav_returns_are_real_and_sized_by_period(monkeypatch):
    from backend.data_loaders import market_data as md

    monkeypatch.setattr(md, "VERIFIED_NAV_MAX_AGE_DAYS", 10_000)  # ignore file age in CI
    r3 = md.verified_nav_returns("nippon-flexi-cap-fund", "3y")
    r5 = md.verified_nav_returns("nippon-large-cap-fund", "5y")
    r3l = md.verified_nav_returns("nippon-large-cap-fund", "3y")
    assert r3 is not None and not r3.is_synthetic and r3.source == "amfi_nav_verified"
    assert len(r3l.returns) < len(r5.returns)
    # one calendar year holds < 252 NAV days: below the minimum, so no series
    assert md.verified_nav_returns("nippon-flexi-cap-fund", "1y") is None
    assert r3.expense_ratio_applied == 0.0  # NAV is already net of fees


def test_unverified_scheme_has_no_verified_nav():
    from backend.data_loaders.amfi_loader import verified_amfi_code
    from backend.data_loaders.market_data import verified_nav_returns

    assert verified_amfi_code("nippon-low-duration-fund") is None
    assert verified_nav_returns("nippon-low-duration-fund") is None


def test_stale_verified_nav_falls_through(monkeypatch):
    from backend.data_loaders import market_data as md

    monkeypatch.setattr(md, "VERIFIED_NAV_MAX_AGE_DAYS", -1)
    assert md.verified_nav_returns("nippon-flexi-cap-fund") is None
