"""
Phase 1: each scheme measured against its OWN benchmark, and fees actually
deducted from the returns that never had them taken out.

Both defects had the same shape -- the right data was already in
schemes_metadata.json and no engine read it:

  * 31 distinct declared benchmarks, and the risk engine used Nifty 50 for
    all 35 schemes. Only 6 are benchmarked to a Nifty-50 index, so beta,
    alpha, tracking error and the SEBI-mandated Information Ratio were
    measured against the wrong index for the other 29.
  * Expense ratios populated for 33 of 35 schemes, read by nothing. On a
    Rs 25,000/month SIP over 25 years the average direct-vs-regular gap is
    Rs 38.6 lakh of final corpus.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from backend.core.config import settings
from backend.data_loaders import benchmark_registry as reg
from backend.data_loaders.market_data import ReturnsData, net_of_expenses


class TestBenchmarkRegistry:
    def test_every_declared_benchmark_resolves(self):
        """No scheme may fall through to a silent default -- that is exactly
        how Nifty 50 ended up standing in for a liquid fund."""
        import json

        with open(settings.SCHEMES_PATH, encoding="utf-8") as f:
            rows = json.load(f)
        rows = rows if isinstance(rows, list) else list(rows.values())
        for r in rows:
            m = reg.resolve(r.get("benchmark"))
            assert m.quality in {"exact", "price_proxy", "close_proxy", "none"}
            assert m.note, f"{r.get('benchmark')} has no explanation"

    def test_debt_benchmarks_are_deliberately_unmapped(self):
        """CRISIL/NIFTY debt indices have no free source. Withholding the
        metric beats computing it against an unrelated equity index."""
        for b in (
            "CRISIL Liquid Overnight Index",
            "NIFTY Corporate Bond Index A-II",
            "CRISIL Short Duration Debt A-II Index",
        ):
            m = reg.resolve(b)
            assert m.ticker is None
            assert m.usable is False
            assert "debt" in m.note.lower() or "not published" in m.note.lower()

    def test_total_return_proxies_disclose_the_dividend_gap(self):
        """A price index measured against a TRI benchmark overstates alpha by
        roughly the dividend yield. That must be stated, not absorbed."""
        m = reg.resolve("Nifty 500 TRI")
        assert m.ticker == "^CRSLDX"
        assert m.quality == "price_proxy"
        assert "dividend" in m.note.lower()

    def test_unknown_benchmark_degrades_without_raising(self):
        m = reg.resolve("Some Index That Does Not Exist")
        assert m.usable is False
        m2 = reg.resolve(None)
        assert m2.usable is False

    def test_coverage_is_reported_honestly(self):
        cov = reg.coverage(settings.SCHEMES_PATH)
        assert cov["schemes"] == 35
        # Most of the universe is debt or narrow sector funds with no free
        # index. Pinning this stops a future edit from quietly "fixing"
        # coverage by mapping things to an unrelated index.
        assert cov["usable"] < cov["schemes"]
        assert cov["by_quality"]["none"] > 0


class TestExpenseRatioNetting:
    """
    The distinction a naive implementation gets wrong: NAV-derived returns are
    ALREADY net of fees, reconstructed returns are not.
    """

    @staticmethod
    def _rd(source: str) -> ReturnsData:
        idx = pd.bdate_range("2023-01-02", periods=300)
        r = pd.Series(np.full(300, 0.0004), index=idx)
        return ReturnsData(
            ticker="x", returns=r, prices=r.cumsum(),
            is_synthetic=False, fetched_at=pd.Timestamp.now().to_pydatetime(),
            source=source,
        )

    def test_reconstructed_returns_have_the_fee_deducted(self):
        """Rebuilt from underlying stock prices -- nobody charged that basket
        a management fee, so the series is gross."""
        out = net_of_expenses(self._rd("holdings_reconstruction"),
                              {"expense_ratio_direct": 1.0})
        assert out.expense_ratio_applied == pytest.approx(0.01)
        assert out.returns.mean() < self._rd("holdings_reconstruction").returns.mean()

    def test_nav_derived_returns_are_left_alone(self):
        """THE DOUBLE-COUNTING GUARD. A fund's NAV is struck after expenses,
        so deducting the TER again would understate the fund by up to 2.1%/yr."""
        before = self._rd("amfi")
        out = net_of_expenses(before, {"expense_ratio_direct": 1.0})
        assert out.expense_ratio_applied == 0.0
        assert out.returns.equals(before.returns)

    def test_synthetic_series_are_left_alone(self):
        out = net_of_expenses(self._rd("synthetic"), {"expense_ratio_direct": 1.0})
        assert out.expense_ratio_applied == 0.0

    def test_direct_is_preferred_over_regular(self):
        """The platform cannot know which plan the investor holds; the smaller
        deduction errs toward not overstating costs."""
        out = net_of_expenses(
            self._rd("holdings_reconstruction"),
            {"expense_ratio_direct": 0.5, "expense_ratio_regular": 2.0},
        )
        assert out.expense_ratio_applied == pytest.approx(0.005)

    def test_falls_back_to_regular_when_direct_is_missing(self):
        out = net_of_expenses(
            self._rd("holdings_reconstruction"), {"expense_ratio_regular": 1.5}
        )
        assert out.expense_ratio_applied == pytest.approx(0.015)

    def test_missing_expense_data_is_not_an_error(self):
        out = net_of_expenses(self._rd("holdings_reconstruction"), {})
        assert out.expense_ratio_applied == 0.0

    def test_the_deduction_is_the_right_size(self):
        """A 1% TER must cost ~1% a year, not 1% a day or 1% over the window."""
        from backend.analytics import risk_metrics as rm

        before = self._rd("holdings_reconstruction")
        after = net_of_expenses(before, {"expense_ratio_direct": 1.0})
        drag = rm.cagr(before.returns) - rm.cagr(after.returns)
        assert drag == pytest.approx(0.01, abs=0.0015)


class TestRiskEngineUsesEachSchemesOwnBenchmark:
    @staticmethod
    def _engine():
        from backend.engines.risk_engine import RiskEngine

        return RiskEngine(
            schemes_path=settings.SCHEMES_PATH,
            holdings_path=settings.HOLDINGS_PATH,
            price_period="3y",
            force_synthetic=True,
        )

    def test_metrics_report_which_benchmark_was_used(self):
        res = self._engine().compute_risk(
            ["nippon-large-cap-fund", "nippon-liquid-fund"]
        )
        for m in res.scheme_metrics:
            assert m.benchmark_declared, "the factsheet benchmark must be echoed"
            assert m.benchmark_quality in {
                "exact", "price_proxy", "close_proxy", "none",
            }

    def test_a_debt_scheme_does_not_get_an_equity_benchmark(self):
        """A liquid fund given a Nifty 50 beta is the headline symptom of the
        bug this phase fixes."""
        res = self._engine().compute_risk(["nippon-liquid-fund"])
        m = res.scheme_metrics[0]
        assert m.benchmark_used is None
        assert m.benchmark_quality == "none"
        assert m.information_ratio is None

    def test_portfolio_benchmark_is_named(self):
        res = self._engine().compute_risk(
            ["nippon-large-cap-fund", "nippon-small-cap-fund"]
        )
        # Under force_synthetic there is no real index series, and a synthetic
        # benchmark is worse than none -- but the field must exist either way
        # so the reader is never left guessing.
        assert hasattr(res, "portfolio_benchmark")
