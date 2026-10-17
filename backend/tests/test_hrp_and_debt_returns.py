"""
Phase 4: a risk-balanced reference allocation -- and the debt-scheme bug that
building it uncovered.

THE BUG, which matters far more than the feature that found it. The unified
fallback chain ran yfinance holdings reconstruction for EVERY scheme, on the
documented assumption that it is "naturally empty for debt schemes (bond
issuers aren't equity tickers)".

That assumption was false. A liquid fund holds certificates of deposit and
commercial paper whose ISSUERS are named "HDFC Bank Limited", "Bank of
Baroda", "Canara Bank" -- and stocks_ticker_map.json matches those names to
EQUITY tickers. Eleven of nippon-liquid-fund's 59 holdings matched, so 45-day
money-market paper was reconstructed as though the fund held bank SHARES.

A liquid fund with a modified duration of 0.12 years reported an annualised
volatility of 17.95%. The true figure is under 1%. That number reached the
risk dashboard, the portfolio covariance, and ARISE through
PortfolioStatisticsBuilder.
"""
from __future__ import annotations

import numpy as np
import pytest

from backend.analytics.hrp import allocation_suggestion, hierarchical_risk_parity


def _two_cluster_cov():
    """A/B calm and correlated; C/D volatile and correlated."""
    corr = np.array([
        [1.00, 0.90, 0.20, 0.20],
        [0.90, 1.00, 0.20, 0.20],
        [0.20, 0.20, 1.00, 0.85],
        [0.20, 0.20, 0.85, 1.00],
    ])
    sd = np.array([0.10, 0.11, 0.28, 0.30])
    return corr * np.outer(sd, sd)


class TestHierarchicalRiskParity:
    def test_weights_sum_to_one_and_are_positive(self):
        w = hierarchical_risk_parity(_two_cluster_cov(), list("ABCD"))
        assert w is not None
        assert sum(w.values()) == pytest.approx(1.0, abs=1e-9)
        assert all(v > 0 for v in w.values())

    def test_the_calm_cluster_receives_more_capital(self):
        w = hierarchical_risk_parity(_two_cluster_cov(), list("ABCD"))
        assert (w["A"] + w["B"]) > (w["C"] + w["D"])

    def test_declines_rather_than_guessing_on_a_single_asset(self):
        """Returning equal weight here would hand back numbers that were
        never computed."""
        assert hierarchical_risk_parity(np.array([[0.04]]), ["A"]) is None

    def test_declines_on_a_degenerate_covariance(self):
        assert hierarchical_risk_parity(np.zeros((3, 3)), list("ABC")) is None

    def test_declines_on_non_finite_input(self):
        cov = _two_cluster_cov()
        cov[0, 0] = np.nan
        assert hierarchical_risk_parity(cov, list("ABCD")) is None

    def test_no_matrix_inversion_is_required(self):
        """The reason HRP is used at all: a near-singular covariance, which
        mean-variance cannot handle, must still produce a stable answer."""
        corr = np.full((4, 4), 0.999)
        np.fill_diagonal(corr, 1.0)
        sd = np.array([0.15, 0.15, 0.15, 0.15])
        w = hierarchical_risk_parity(corr * np.outer(sd, sd), list("ABCD"))
        assert w is not None
        assert sum(w.values()) == pytest.approx(1.0, abs=1e-9)


class TestAllocationSuggestionIsHonest:
    def test_it_is_not_presented_as_advice(self):
        out = allocation_suggestion(_two_cluster_cov(), list("ABCD"))
        joined = " ".join(out["caveats"]).lower()
        assert "not a recommendation" in joined
        assert "tax" in joined                      # omissions are named
        assert any("correlation" in c.lower() for c in out["caveats"])

    def test_delta_against_current_weights(self):
        current = {"A": 0.25, "B": 0.25, "C": 0.25, "D": 0.25}
        out = allocation_suggestion(_two_cluster_cov(), list("ABCD"), current)
        assert set(out["delta"]) == set(current)
        assert sum(out["delta"].values()) == pytest.approx(0.0, abs=1e-6)

    def test_unavailable_suggestions_say_why(self):
        out = allocation_suggestion(np.zeros((2, 2)), ["A", "B"])
        assert out["available"] is False
        assert out["reason"]

    def test_concentration_is_flagged(self):
        """Mixing a near-riskless fund with equity makes pure risk parity
        collapse onto the riskless one. Correct, and useless without a note."""
        corr = np.array([[1.0, 0.1], [0.1, 1.0]])
        sd = np.array([0.002, 0.16])               # liquid fund vs equity
        out = allocation_suggestion(corr * np.outer(sd, sd), ["liquid", "equity"])
        assert out["weights"]["liquid"] > 0.9
        assert "concentration_warning" in out
        assert "risk, not return" in out["concentration_warning"].lower()

    def test_a_balanced_pair_is_not_flagged(self):
        corr = np.array([[1.0, 0.3], [0.3, 1.0]])
        sd = np.array([0.15, 0.16])
        out = allocation_suggestion(corr * np.outer(sd, sd), ["a", "b"])
        assert "concentration_warning" not in out


class TestDebtSchemesAreNotReconstructedFromEquity:
    """THE REGRESSION GUARD for the bug above."""

    def test_a_liquid_fund_does_not_get_equity_volatility(self):
        from backend.core.config import settings
        from backend.engines.risk_engine import RiskEngine

        e = RiskEngine(
            schemes_path=settings.SCHEMES_PATH,
            holdings_path=settings.HOLDINGS_PATH,
            price_period="3y",
        )
        m = e.compute_risk(["nippon-liquid-fund"]).scheme_metrics[0]
        # Equity-like volatility on 45-day money-market paper is the symptom.
        assert m.annualized_volatility < 0.05, (
            f"liquid fund volatility {m.annualized_volatility:.2%} looks like equity; "
            "debt holdings may be matching equity tickers again"
        )

    def test_debt_schemes_skip_the_equity_reconstruction_path(self):
        import json

        from backend.core.async_utils import run_async_safely
        from backend.core.config import settings
        from backend.data_loaders.market_data import MarketDataLoader

        with open(settings.SCHEMES_PATH, encoding="utf-8") as f:
            rows = json.load(f)
        rows = rows if isinstance(rows, list) else list(rows.values())
        scheme = next(r for r in rows if r["id"] == "nippon-liquid-fund")
        assert scheme.get("instrument_type") == "debt"

        with open(settings.HOLDINGS_PATH, encoding="utf-8") as f:
            holdings_blob = json.load(f)
        raw = holdings_blob.get("nippon-liquid-fund", [])
        holdings = raw.get("holdings", []) if isinstance(raw, dict) else raw

        rd = run_async_safely(
            MarketDataLoader().get_scheme_returns_with_fallback(
                {**scheme, "id": "nippon-liquid-fund"}, holdings, period="3y"
            )
        )
        assert rd.source != "holdings_reconstruction"
