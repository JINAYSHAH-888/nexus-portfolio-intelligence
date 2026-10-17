"""
The quantitative core: return-space conversions, the risk engine's forward
simulation, benchmark statistics, and ARISE's return process.

These tests exist because four defects in this layer were all INVISIBLE from
the outside -- every one produced a plausible-looking number. A risk report
that is merely wrong in the fourth decimal is harmless; these were wrong by
1-3 percentage points a year, compounding across a 30-year retirement
projection, while looking entirely reasonable on screen. Each class below
pins one of them.
"""
from __future__ import annotations

import math

import numpy as np
import pandas as pd
import pytest

from backend.analytics import risk_metrics as rm
from backend.engines.risk_engine import RiskEngine


class TestReturnSpaceConversions:
    """
    Three quantities were all called "the return" and differ by ~sigma^2/2:
    the log drift, the geometric (compound) mean, and the arithmetic mean.
    Mixing them is what produced two opposite errors in two engines.
    """

    def test_arithmetic_exceeds_geometric_whenever_there_is_volatility(self):
        assert rm.geometric_to_arithmetic(0.12, 0.18) > 0.12

    def test_conversion_round_trips(self):
        g, sigma = 0.12, 0.25
        a = rm.geometric_to_arithmetic(g, sigma)
        assert rm.arithmetic_to_geometric(a, sigma) == pytest.approx(g, abs=1e-9)

    def test_zero_volatility_makes_them_identical(self):
        """With no volatility there is no drag, so the two means coincide --
        the boundary case that proves the correction is volatility-driven."""
        assert rm.geometric_to_arithmetic(0.08, 0.0) == pytest.approx(0.08)
        assert rm.arithmetic_to_geometric(0.08, 0.0) == pytest.approx(0.08)

    def test_exact_form_differs_from_the_first_order_rule_at_real_volatility(self):
        """`g + sigma^2/2` is the familiar approximation and is fine for bonds.
        At the volatility an Indian small-cap fund actually runs, it drifts --
        which is exactly where the correction matters most."""
        g, sigma = 0.12, 0.25
        exact = rm.geometric_to_arithmetic(g, sigma)
        first_order = g + sigma**2 / 2
        assert abs(first_order - exact) > 0.003          # ~36bp apart
        assert exact < first_order                        # and the exact form is lower

    def test_log_drift_contains_the_ito_term_exactly_once(self):
        """exp(log_drift) - 1 must recover the GEOMETRIC rate, not the
        arithmetic one. If it returned the arithmetic rate, a GBM caller
        subtracting sigma^2/2 again would be correct -- and that ambiguity is
        precisely what caused the double correction."""
        a, sigma = 0.1476, 0.25
        m = rm.log_drift_from_arithmetic(a, sigma)
        assert math.exp(m) - 1 == pytest.approx(
            rm.arithmetic_to_geometric(a, sigma), abs=1e-9
        )


class TestMonteCarloDriftIsNotDoubleCorrected:
    """
    THE REGRESSION GUARD FOR DEFECT #2. `_monte_carlo_gbm` previously received
    `r.mean() * 252` computed on daily LOG returns -- already a log drift --
    and subtracted sigma^2/2 from it again. Measured: at sigma = 0.25 the
    median 1-year outcome came out 3.35 percentage points too low, and every
    percentile band with it.
    """

    def test_median_matches_the_compound_rate_the_input_implies(self):
        mu_arith, sigma = 0.1476, 0.25
        mc = RiskEngine._monte_carlo_gbm(mu_arith, sigma, n_paths=40_000)
        expected_geometric = rm.arithmetic_to_geometric(mu_arith, sigma)
        assert float(np.median(mc)) == pytest.approx(expected_geometric, abs=0.012)

    def test_a_second_ito_subtraction_would_be_detected(self):
        """Pins the SIZE of the old error, so reintroducing it fails loudly
        rather than shifting a percentile nobody re-checks."""
        mu_arith, sigma = 0.1476, 0.25
        mc = RiskEngine._monte_carlo_gbm(mu_arith, sigma, n_paths=40_000)
        # What the old code produced: the Ito term applied to a drift that
        # was already logarithmic.
        log_drift = rm.log_drift_from_arithmetic(mu_arith, sigma)
        buggy_median = math.exp(log_drift - 0.5 * sigma**2) - 1
        assert float(np.median(mc)) - buggy_median > 0.03

    def test_volatility_is_preserved_when_tails_are_fattened(self):
        """Student-t shocks must change the TAILS without changing the
        volatility the caller asked for, or every other statistic silently
        shifts too."""
        mu, sigma = 0.10, 0.20
        gauss = RiskEngine._monte_carlo_gbm(mu, sigma, n_paths=40_000, fat_tails=False)
        t_dist = RiskEngine._monte_carlo_gbm(mu, sigma, n_paths=40_000, fat_tails=True)
        assert float(np.std(t_dist)) == pytest.approx(float(np.std(gauss)), rel=0.12)

    def test_fat_tails_produce_a_worse_left_tail(self):
        """The whole point: a 1-in-100 year should look worse under t(5) than
        under a normal, at the same volatility."""
        mu, sigma = 0.10, 0.20
        gauss = RiskEngine._monte_carlo_gbm(mu, sigma, n_paths=60_000, fat_tails=False)
        t_dist = RiskEngine._monte_carlo_gbm(mu, sigma, n_paths=60_000, fat_tails=True)
        assert np.quantile(t_dist, 0.01) < np.quantile(gauss, 0.01)


class TestBetaSurvivesIndexedInput:
    """
    THE REGRESSION GUARD FOR DEFECT #1. Beta was built by putting a
    DatetimeIndexed scheme series and a RangeIndexed benchmark into one
    DataFrame. The indexes UNION, every row lands half-empty, dropna() clears
    the frame, the `len >= 60` guard fails, and beta returns None -- on real
    data, every time, while still working on synthetic data in tests.
    """

    @staticmethod
    def _series(n=400, seed=0):
        idx = pd.bdate_range("2023-01-02", periods=n)
        rng = np.random.default_rng(seed)
        return pd.Series(rng.normal(0.0004, 0.01, n), index=idx)

    def test_datetime_indexed_scheme_against_plain_array_benchmark(self):
        """The EXACT shape that used to fail."""
        scheme = self._series()
        bench_array = np.asarray(self._series(seed=1))
        beta, *_ = RiskEngine._benchmark_metrics(scheme, pd.Series(bench_array), {})
        assert beta is not None

    def test_beta_of_a_series_against_itself_is_one(self):
        s = self._series()
        beta, alpha, r2, *_ = RiskEngine._benchmark_metrics(s, s, {})
        assert beta == pytest.approx(1.0, abs=1e-6)
        assert r2 == pytest.approx(1.0, abs=1e-6)

    def test_double_the_benchmark_has_beta_two(self):
        bench = self._series()
        beta, *_ = RiskEngine._benchmark_metrics(bench * 2, bench, {})
        assert beta == pytest.approx(2.0, rel=1e-6)

    def test_falls_back_to_the_disclosed_beta_when_overlap_is_too_short(self):
        """A factsheet number labelled as such beats silently returning None."""
        short = self._series(n=10)
        beta, _a, r2, *_ = RiskEngine._benchmark_metrics(
            short, self._series(n=10, seed=3), {"beta": 0.88}
        )
        assert beta == 0.88
        assert r2 is None          # marks it as NOT derived here

    def test_capture_ratios_detect_an_asymmetric_fund(self):
        """A fund that takes all the upside and half the downside must not
        report symmetric capture -- the asymmetry is the entire signal, and
        a single beta cannot express it."""
        rng = np.random.default_rng(5)
        bench = pd.Series(rng.normal(0, 0.012, 600), index=pd.bdate_range("2023-01-02", periods=600))
        port = bench.where(bench < 0, bench * 1.0)       # full upside
        port = port.where(bench > 0, bench * 0.5)        # half the downside
        up, down = rm.up_down_capture(port, bench)
        assert up == pytest.approx(1.0, abs=0.01)
        assert down == pytest.approx(0.5, abs=0.01)


class TestRiskEngineUsesTheInvestorsWeights:
    """
    THE REGRESSION GUARD FOR DEFECT #3. The risk dashboard computed an
    equal-weight portfolio while ARISE projected the real allocation, so one
    product told the same investor about two different portfolios. Measured on
    real schemes: 11.92% CAGR / 0.459 Sharpe reported for a 90/10 portfolio
    actually delivering 9.82% / 0.329.
    """

    @staticmethod
    def _engine():
        from backend.core.config import settings
        return RiskEngine(
            schemes_path=settings.SCHEMES_PATH,
            holdings_path=settings.HOLDINGS_PATH,
            price_period="3y",
            force_synthetic=True,          # deterministic, no network
        )

    def test_weights_change_the_answer(self):
        e = self._engine()
        ids = ["nippon-large-cap-fund", "nippon-small-cap-fund"]
        equal = e.compute_risk(ids)
        tilted = e.compute_risk(ids, weights={ids[0]: 0.9, ids[1]: 0.1})
        assert tilted.portfolio_volatility != equal.portfolio_volatility

    def test_the_result_says_which_weighting_it_used(self):
        e = self._engine()
        ids = ["nippon-large-cap-fund", "nippon-small-cap-fund"]
        assert e.compute_risk(ids).weighting_scheme == "equal"
        got = e.compute_risk(ids, weights={ids[0]: 0.7, ids[1]: 0.3})
        assert got.weighting_scheme == "investor"
        assert got.weights == {ids[0]: 0.7, ids[1]: 0.3}

    def test_weights_that_do_not_sum_to_one_fall_back_and_admit_it(self):
        """Silently renormalising someone's broken input would present a
        portfolio they never specified as though they had."""
        e = self._engine()
        ids = ["nippon-large-cap-fund", "nippon-small-cap-fund"]
        got = e.compute_risk(ids, weights={ids[0]: 0.9, ids[1]: 0.9})
        assert got.weighting_scheme == "equal"

    def test_weights_for_the_wrong_schemes_fall_back(self):
        e = self._engine()
        ids = ["nippon-large-cap-fund", "nippon-small-cap-fund"]
        got = e.compute_risk(ids, weights={"some-other-fund": 1.0})
        assert got.weighting_scheme == "equal"

    def test_conservatism_is_reported_not_hidden(self):
        e = self._engine()
        res = e.compute_risk(["nippon-large-cap-fund", "nippon-small-cap-fund"])
        assert res.expected_return_after_haircut == pytest.approx(
            res.expected_return_unbiased - res.conservatism_haircut, abs=1e-6
        )


class TestVarQuality:
    """A VaR nobody backtests is a number, not a risk measure."""

    @staticmethod
    def _skewed(n=1500, seed=11):
        rng = np.random.default_rng(seed)
        r = rng.standard_t(4, n) * 0.009
        r[rng.random(n) < 0.02] -= 0.045          # occasional crash days
        return pd.Series(r, index=pd.bdate_range("2020-01-01", periods=n))

    @pytest.mark.parametrize("confidence", [0.05, 0.01])
    def test_cornish_fisher_tracks_the_empirical_quantile_better(self, confidence):
        """
        The real case for Cornish-Fisher, and NOT "it is always more
        conservative" -- at the 95% level high excess kurtosis legitimately
        pulls the quantile toward zero, and the empirical data confirms that
        is correct. What it must do is land closer to the loss the sample
        actually experienced than a Gaussian fitted to the same data.
        """
        from scipy import stats as st

        r = self._skewed()
        empirical = float(np.quantile(r, confidence))
        gaussian = float(r.mean() + r.std() * st.norm.ppf(confidence))
        cf = rm.cornish_fisher_var(r, confidence)
        assert abs(cf - empirical) < abs(gaussian - empirical)

    def test_gaussian_badly_understates_the_99th_percentile_loss(self):
        """Why this matters in rupees: on fat-tailed data the Gaussian 99%
        VaR missed the real one-in-a-hundred day by well over a percentage
        point of DAILY return."""
        from scipy import stats as st

        r = self._skewed()
        gaussian = float(r.mean() + r.std() * st.norm.ppf(0.01))
        assert gaussian - float(np.quantile(r, 0.01)) > 0.01
        assert rm.cornish_fisher_var(r, 0.01) < gaussian

    def test_kupiec_rejects_a_deliberately_wrong_var(self):
        """A '95%' VaR breached far more often than 5% of the time is not
        conservative, it is wrong -- and nothing else in the report reveals
        that, because every other number shares its assumptions."""
        r = self._skewed()
        much_too_shallow = float(np.quantile(r, 0.25))
        result = rm.kupiec_pof_test(r, much_too_shallow, 0.05)
        assert result["passed"] is False

    def test_kupiec_accepts_a_well_calibrated_var(self):
        r = self._skewed()
        honest = float(np.quantile(r, 0.05))
        assert rm.kupiec_pof_test(r, honest, 0.05)["passed"] is True

    def test_short_samples_report_none_rather_than_a_false_verdict(self):
        r = self._skewed(n=50)
        assert rm.kupiec_pof_test(r, -0.02, 0.05)["passed"] is None

    def test_historical_var_includes_the_breach_itself_in_cvar(self):
        r = pd.Series([-0.10, -0.05, -0.02, 0.0, 0.01, 0.02, 0.03, 0.04, 0.05, 0.06])
        var, cvar = rm.historical_var_cvar(r, 0.20)
        assert cvar <= var


class TestAriseRealisesTheCagrItClaims:
    """
    THE REGRESSION GUARD FOR DEFECT #4, and the validation the retirement-
    planning literature prescribes for exactly this: simulate, then confirm
    the realised compound growth matches the intended rate.

    ARISE drew `r_t = portfolio_cagr + sigma*z` and compounded it, applying
    volatility drag to a number that already contained it. Measured over
    20,000 paths at sigma = 0.18: a realised median CAGR 1.38pp/yr BELOW the
    anchor, compounding to roughly a 45% terminal-corpus shortfall over 30
    years -- against the engine's own stated anchor.
    """

    @staticmethod
    def _realised_median(mu, sigma, n_years=30, n_paths=4000, seed=7):
        from backend.engines.arise_engine import standardized_shock_path
        rng = np.random.default_rng(seed)
        out = []
        for _ in range(n_paths):
            z = standardized_shock_path(n_years, rng)
            r = np.maximum(mu + sigma * z, -0.95)
            out.append(np.prod(1 + r) ** (1 / n_years) - 1)
        return float(np.median(out))

    def test_corrected_draw_delivers_the_target_cagr(self):
        from backend.engines.arise_engine import effective_mu_sigma
        target, sigma = 0.12, 0.18
        mu, _ = effective_mu_sigma(40, 60, target, sigma, 0.055, "declining", 0.0)
        assert self._realised_median(mu, sigma) == pytest.approx(target, abs=0.015)

    def test_feeding_the_raw_cagr_would_undershoot(self):
        """Pins the old behaviour as WRONG, so it cannot quietly return."""
        target, sigma = 0.12, 0.18
        assert self._realised_median(target, sigma) < target - 0.008

    def test_haircut_lowers_the_draw_by_exactly_its_own_size(self):
        from backend.engines.arise_engine import effective_mu_sigma
        base, _ = effective_mu_sigma(40, 60, 0.12, 0.18, 0.055, "declining", 0.0)
        cut, _ = effective_mu_sigma(40, 60, 0.12, 0.18, 0.055, "declining", 0.0125)
        assert base - cut == pytest.approx(0.0125, abs=1e-9)

    def test_glide_path_blends_before_converting(self):
        """Post-retirement the portfolio is mostly cash, so the conversion
        must use the BLENDED volatility. Converting first would apply an
        equity-sized correction to a cash-heavy portfolio."""
        from backend.engines.arise_engine import effective_mu_sigma
        young, sig_young = effective_mu_sigma(40, 60, 0.12, 0.18, 0.055, "declining", 0.0)
        old, sig_old = effective_mu_sigma(80, 60, 0.12, 0.18, 0.055, "declining", 0.0)
        assert sig_old < sig_young
        assert old < young
        # The uplift over the blended geometric mean must shrink with sigma.
        assert (old - (0.25 * 0.12 + 0.75 * 0.055)) < (young - 0.12)
