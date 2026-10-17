"""
Phase 3: is the fund actually managed, and is the withdrawal rate defensible
in INDIA rather than in the United States?

Both additions are deliberately conservative about what they claim:

  * Closet-index detection is RETURN-BASED, because true Active Share needs
    benchmark constituent weights this platform does not have. That follows
    ESMA's own 2020 method ("we use three return-based metrics: Tracking
    Error, R2 and Beta") and France's AMF, which built a return-based test
    specifically to avoid depending on holdings data.
  * The safe-withdrawal band comes from India-specific research (Saraogi
    2022; Raju & Saraogi 2024), because the 4% rule is a US artefact that
    Saraogi found depleted Indian portfolios early in nearly a third of
    scenarios.
"""
from __future__ import annotations

import pytest

from backend.analytics import activeness as act
from backend.engines import arise_engine as ar


class TestClosetIndexDetection:
    def test_low_tracking_error_with_high_r2_is_flagged(self):
        out = act.assess(tracking_error=0.02, r_squared=0.97,
                         expense_ratio=0.012, benchmark_quality="price_proxy")
        assert out["verdict"] == act.VERDICT_CLOSET

    def test_high_tracking_error_is_active(self):
        out = act.assess(tracking_error=0.09, r_squared=0.70,
                         expense_ratio=0.012, benchmark_quality="price_proxy")
        assert out["verdict"] == act.VERDICT_ACTIVE

    def test_an_unreliable_benchmark_withholds_the_verdict(self):
        """A close proxy inflates tracking error with the BENCHMARK's
        mismatch, so the deviation cannot be attributed to the manager."""
        for q in ("close_proxy", "none"):
            out = act.assess(tracking_error=0.02, r_squared=0.97,
                             expense_ratio=0.012, benchmark_quality=q)
            assert out["verdict"] == act.VERDICT_UNDETERMINED

    def test_missing_statistics_withhold_the_verdict(self):
        out = act.assess(tracking_error=None, r_squared=None,
                         expense_ratio=0.012, benchmark_quality="price_proxy")
        assert out["verdict"] == act.VERDICT_UNDETERMINED

    def test_cost_per_unit_of_active_risk(self):
        """The number that is actually new: what the investor pays per unit of
        the deviation they are paying to receive."""
        out = act.assess(tracking_error=0.04, r_squared=0.8,
                         expense_ratio=0.012, benchmark_quality="price_proxy")
        assert out["cost_per_unit_active_risk"] == pytest.approx(0.3, abs=1e-6)

    def test_an_expensive_near_index_fund_says_so_in_words(self):
        out = act.assess(tracking_error=0.015, r_squared=0.98,
                         expense_ratio=0.02, benchmark_quality="price_proxy")
        joined = " ".join(out["reasons"]).lower()
        assert "active risk" in joined

    def test_every_verdict_carries_its_caveats(self):
        """ESMA's screens needed holdings-based Active Share and persistence
        across years; this has neither, and must not imply otherwise."""
        out = act.assess(tracking_error=0.02, r_squared=0.97,
                         expense_ratio=0.012, benchmark_quality="price_proxy")
        assert len(out["caveats"]) >= 3
        assert any("signal" in c.lower() for c in out["caveats"])

    def test_price_proxy_discloses_the_dividend_contamination(self):
        out = act.assess(tracking_error=0.02, r_squared=0.97,
                         expense_ratio=0.012, benchmark_quality="price_proxy")
        assert any("dividend" in c.lower() for c in out["caveats"])


class TestSyntheticSchemesAreNotBenchmarked:
    """
    THE REGRESSION GUARD. Correlating a GENERATED scheme series against a real
    index produced confident nonsense: R-squared 0.00 and a 21% tracking error
    on nippon-flexi-cap-fund, which the activeness screen would have published
    as "genuinely active" when it only meant the two series were unrelated.
    """

    def test_a_synthetic_scheme_withholds_benchmark_metrics(self):
        from backend.core.config import settings
        from backend.engines.risk_engine import RiskEngine

        e = RiskEngine(
            schemes_path=settings.SCHEMES_PATH,
            holdings_path=settings.HOLDINGS_PATH,
            price_period="3y",
            force_synthetic=True,          # forces EVERY scheme synthetic
        )
        res = e.compute_risk(["nippon-flexi-cap-fund", "nippon-large-cap-fund"])
        for m in res.scheme_metrics:
            assert m.is_synthetic
            assert m.tracking_error is None
            assert m.information_ratio is None
            assert m.activeness.get("verdict") == act.VERDICT_UNDETERMINED
            assert m.benchmark_quality == "none"


class TestIndiaSafeWithdrawalBand:
    def test_four_percent_is_flagged_as_above_the_research(self):
        """The headline finding: the US 4% rule does not transfer to India."""
        out = ar.india_swr_assessment(0.04)
        assert out["band"] == "above_research"
        assert "4%" in out["note"]

    def test_three_percent_is_inside_the_band(self):
        assert ar.india_swr_assessment(0.032)["band"] == "within_research_band"

    def test_a_very_low_rate_is_conservative(self):
        assert ar.india_swr_assessment(0.02)["band"] == "conservative"

    def test_the_elevated_zone_is_distinguished_from_the_extreme(self):
        """Raju & Saraogi find failure risk rising sharply beyond 3.75%, which
        is not the same claim as 'above all research'."""
        assert ar.india_swr_assessment(0.036)["band"] == "elevated"
        assert ar.india_swr_assessment(0.055)["band"] == "above_research"

    def test_the_band_matches_the_published_research(self):
        out = ar.india_swr_assessment(0.03)
        assert out["research_band"] == [0.030, 0.035]

    def test_the_caveat_names_the_literature_as_thin(self):
        """One research group, heavily repeated in secondary coverage. Saying
        so is the difference between citing research and hiding behind it."""
        out = ar.india_swr_assessment(0.03)
        assert "caveat" in out
        assert "saraogi" in out["caveat"].lower()

    def test_missing_rate_is_handled(self):
        assert ar.india_swr_assessment(None)["band"] == "unknown"
        assert ar.india_swr_assessment(0.0)["band"] == "unknown"
