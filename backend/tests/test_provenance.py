"""
Data provenance: what is real, what is assumed, and the three hardcode bugs
this pass fixed.
"""
from __future__ import annotations

import json

import pytest

from backend.core.assumptions import (
    RF_MAX,
    RF_MIN,
    registry,
    resolve_risk_free_rate,
    risk_free_rate_source,
)
from backend.core.config import settings


# ══════════════════════════════════════════════════════════════════════════════
# FIX 1 + 2: one risk-free rate, from real data, that cannot run away
# ══════════════════════════════════════════════════════════════════════════════
class TestRiskFreeRate:
    def test_it_comes_from_the_overnight_funds_real_disclosed_ytm(self):
        """Not a hardcoded 'RBI repo proxy' that goes stale the moment the RBI moves."""
        schemes = {s["id"]: s for s in json.loads(settings.SCHEMES_PATH.read_text())}
        overnight_ytm = schemes["nippon-overnight-fund"]["ytm_decimal"]

        assert resolve_risk_free_rate() == pytest.approx(overnight_ytm)
        assert "disclosed YTM" in risk_free_rate_source()
        assert RF_MIN <= resolve_risk_free_rate() <= RF_MAX

    def test_the_whole_product_uses_ONE_risk_free_rate(self):
        """
        THE BUG: /analysis/risk computed Sharpe against a hardcoded 0.065 while
        ARISE independently derived its own rate. The risk dashboard and the
        retirement engine disagreed about what 'riskless' means.
        """
        from backend.analytics.portfolio_statistics import get_risk_free_rate_proxy
        from backend.analytics.returns_engine import RISK_FREE_RATE as RETURNS_RF
        from backend.engines.risk_engine import RISK_FREE_RATE as RISK_RF

        canonical = resolve_risk_free_rate()
        assert pytest.approx(canonical) == RISK_RF
        assert pytest.approx(canonical) == RETURNS_RF
        assert get_risk_free_rate_proxy([]) == pytest.approx(canonical)

    def test_the_proxy_can_no_longer_be_a_runaway_equity_return(self):
        """
        THE BUG: get_risk_free_rate_proxy() used to return the realised CAGR of
        the lowest-volatility scheme in the selection. That is not a risk-free
        rate -- it is unbounded. It measured 10.05% on one selection and 21.58%
        on another, and ARISE compounds its LIQUID RESERVE at exactly this rate,
        so the 'safe' bucket of a retirement plan was growing at up to 21%/yr.

        It must now be independent of whatever the selected schemes happened to do.
        """
        from backend.analytics.portfolio_statistics import (
            SchemeStatistics,
            get_risk_free_rate_proxy,
        )

        def stat(cagr: float, vol: float) -> SchemeStatistics:
            return SchemeStatistics(
                scheme_id="x", scheme_name="x", weight=1.0, cagr=cagr,
                annualized_volatility=vol, max_drawdown=0.0, sharpe_ratio=0.0,
                sortino_ratio=0.0, correlation_with_portfolio=0.0,
                risk_contribution_pct=0.0, return_contribution_pct=0.0,
                data_points=2500, is_synthetic=False,
            )

        # A scheme that returned 40% with the lowest vol in the selection would
        # previously have BECOME the risk-free rate.
        absurd = [stat(cagr=0.40, vol=0.01), stat(cagr=0.12, vol=0.18)]
        rf = get_risk_free_rate_proxy(absurd)

        assert rf == pytest.approx(resolve_risk_free_rate())
        assert rf <= RF_MAX, "a 'risk-free' rate above 10% is a data error"

    def test_an_operator_can_override_it_but_it_is_still_clamped(self, monkeypatch):
        import backend.core.assumptions as a

        monkeypatch.setattr(settings, "RISK_FREE_RATE", 0.07, raising=False)
        a.resolve_risk_free_rate.cache_clear()
        assert a.resolve_risk_free_rate() == pytest.approx(0.07)

        # An absurd override is clamped rather than silently trusted.
        monkeypatch.setattr(settings, "RISK_FREE_RATE", 0.95, raising=False)
        a.resolve_risk_free_rate.cache_clear()
        assert a.resolve_risk_free_rate() == pytest.approx(RF_MAX)

        monkeypatch.setattr(settings, "RISK_FREE_RATE", None, raising=False)
        a.resolve_risk_free_rate.cache_clear()


# ══════════════════════════════════════════════════════════════════════════════
# FIX 3: the synthetic tier must not hand a liquid fund equity volatility
# ══════════════════════════════════════════════════════════════════════════════
class TestSyntheticAssetClass:
    def test_schemes_map_to_their_real_calibration_bucket(self):
        from backend.data_loaders.market_data import scheme_asset_class

        schemes = {s["id"]: s for s in json.loads(settings.SCHEMES_PATH.read_text())}

        assert scheme_asset_class(schemes["nippon-overnight-fund"]) == "liquid"
        assert scheme_asset_class(schemes["nippon-liquid-fund"]) == "liquid"
        assert scheme_asset_class(schemes["nippon-gilt-fund"]) == "short_dur"
        assert scheme_asset_class(schemes["nippon-large-cap-fund"]) == "large_cap"
        assert scheme_asset_class(schemes["nippon-small-cap-fund"]) == "mid_small"

    def test_a_liquid_fund_is_not_given_equity_volatility(self):
        """
        THE BUG: REAL_CALIBRATED_PARAMS defines four buckets with correctly
        differentiated vols (large_cap 15.8%, mid_small 19.8%, short_dur 2.1%,
        liquid 0.9%), but the classifier only ever chose large_cap or mid_small
        from a ticker-name keyword match. The two DEBT buckets were dead code, so
        a liquid fund reaching this tier got 15.8% vol instead of 0.9% -- a ~17x
        overstatement that propagated into the covariance matrix.
        """
        from backend.data_loaders.market_data import _synthetic_returns

        liquid = _synthetic_returns("nippon-liquid-fund", seed=7, asset_class="liquid")
        equity = _synthetic_returns("nippon-large-cap-fund", seed=7, asset_class="large_cap")

        liquid_vol = liquid.returns.std() * (252 ** 0.5)
        equity_vol = equity.returns.std() * (252 ** 0.5)

        assert liquid_vol < 0.03, f"liquid fund vol {liquid_vol:.2%} is equity-like"
        assert equity_vol > 0.10
        assert equity_vol > liquid_vol * 5

    def test_bare_tickers_still_use_the_keyword_heuristic(self):
        """The fallback must survive for plain stock tickers, which have no metadata."""
        from backend.data_loaders.market_data import _synthetic_returns

        r = _synthetic_returns("RELIANCE.NS", seed=1)
        assert r.returns.std() * (252 ** 0.5) > 0.05  # equity-like, as it should be


# ══════════════════════════════════════════════════════════════════════════════
# The transparency endpoint
# ══════════════════════════════════════════════════════════════════════════════
class TestAssumptionsRegistry:
    def test_every_assumption_declares_its_provenance(self):
        for a in registry():
            assert a.provenance in ("law", "market", "model", "estimate")
            assert a.source and a.rationale

    def test_tax_constants_are_declared_as_law_not_model(self):
        """A statutory rate is not a modelling choice, and must not be presented as one."""
        by_key = {a.key: a for a in registry()}
        for k in ("equity_ltcg_rate", "equity_ltcg_exemption", "specified_mf_cutoff"):
            assert by_key[k].provenance == "law"

    def test_the_sip_default_is_declared_an_estimate(self):
        by_key = {a.key: a for a in registry()}
        assert by_key["default_sip_rate_of_income"].provenance == "estimate"
        assert by_key["synthetic_regime_params"].provenance == "estimate"

    def test_the_risk_free_rate_is_declared_market_derived(self):
        by_key = {a.key: a for a in registry()}
        rf = by_key["risk_free_rate"]
        assert rf.provenance == "market"
        assert rf.value == pytest.approx(resolve_risk_free_rate(), abs=1e-4)


async def test_meta_assumptions_endpoint_is_public(client):
    """
    Unauthenticated on purpose: an investor is entitled to know what the model
    assumes before trusting a retirement projection, and none of it is sensitive.
    """
    r = await client.get("/api/v1/meta/assumptions")
    assert r.status_code == 200

    body = r.json()
    assert set(body["assumptions"]) == {"law", "market", "model", "estimate"}
    assert body["counts"]["law"] >= 4
    assert body["counts"]["market"] >= 5
    assert body["counts"]["model"] >= 8

    keys = {a["key"] for group in body["assumptions"].values() for a in group}
    assert "risk_free_rate" in keys
    assert "arise_return_t_df" in keys
    assert "default_sip_rate_of_income" in keys
