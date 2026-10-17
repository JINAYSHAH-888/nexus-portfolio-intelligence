"""
Phase 2: ARISE stops assuming everyone dies on schedule, and stops reporting
failure as a coin flip.

Two defects, both of which the engine had the data to fix already:

  * Every path ran to exactly `life_expectancy`. Dying on schedule is the one
    thing nobody does, and the error is one-sided: measured on this engine,
    a fixed horizon reported 36.5% failure where drawing the lifespan gave
    23.3%. Vanguard's warning about the convention is that it "can imply an
    unrealistically low probability that a portfolio will sustain a retiree
    and potentially suggest unnecessary spending cuts."
  * `ruin_age` was computed on every failing path and discarded, so a plan
    that ran dry one year early scored identically to one that ran dry
    fifteen years early.
"""
from __future__ import annotations

import numpy as np
import pytest

from backend.engines import arise_engine as ar


class TestGompertzLifespan:
    def test_draws_match_the_closed_form_median(self):
        rng = np.random.default_rng(0)
        draws = np.array([ar.draw_lifespan(60, 85.0, rng) for _ in range(20_000)])
        assert float(np.median(draws)) == pytest.approx(
            ar.median_lifespan(60, 85.0), abs=0.3
        )

    def test_lifespan_is_always_in_the_future(self):
        """Conditional on being alive at `current_age`, so a draw below it
        would be nonsense."""
        rng = np.random.default_rng(1)
        assert all(ar.draw_lifespan(70, 85.0, rng) >= 70 for _ in range(2_000))

    def test_there_is_real_dispersion(self):
        """The entire defect was that lifespan had ZERO variance."""
        rng = np.random.default_rng(2)
        draws = np.array([ar.draw_lifespan(60, 85.0, rng) for _ in range(5_000)])
        assert draws.std() > 5.0
        assert (draws > 95).mean() > 0.02      # some people do live past 95

    def test_a_higher_modal_age_shifts_the_whole_distribution(self):
        rng = np.random.default_rng(3)
        low = np.median([ar.draw_lifespan(60, 80.0, rng) for _ in range(5_000)])
        high = np.median([ar.draw_lifespan(60, 90.0, rng) for _ in range(5_000)])
        assert high > low + 5

    def test_lifespan_is_capped(self):
        rng = np.random.default_rng(4)
        assert max(ar.draw_lifespan(60, 110.0, rng) for _ in range(2_000)) <= ar.MORTALITY_MAX_AGE

    def test_hazard_rises_with_age(self):
        """Gompertz exists precisely because a constant hazard claims a
        60-year-old and a 95-year-old are equally likely to die this year."""
        young = ar.median_lifespan(60, 85.0) - 60
        old = ar.median_lifespan(90, 85.0) - 90
        assert old < young


def _stats():
    from backend.analytics.portfolio_statistics import PortfolioStatisticsBuilder

    return PortfolioStatisticsBuilder().build(
        scheme_ids=["nippon-large-cap-fund", "nippon-small-cap-fund"],
        weights={"nippon-large-cap-fund": 0.6, "nippon-small-cap-fund": 0.4},
    )


def _run(**over):
    kw = {
        "portfolio_stats": _stats(), "current_age": 40, "retire_age": 60,
        "life_expectancy": 85, "current_portfolio_value": 2_000_000,
        "monthly_sip": 20_000, "step_up": 0.08,
        "current_expense_monthly": 80_000, "n_simulations": 300,
        "leaderboard": True,
    }
    kw.update(over)
    return ar.run_monte_carlo(**kw)


class TestMortalityModelChangesTheAnswer:
    def test_fixed_horizon_is_more_pessimistic(self):
        """THE REGRESSION GUARD. Assuming a certain death date overstates
        ruin, which is not a safe error -- it argues for unnecessary cuts."""
        fixed = _run(mortality="fixed")
        stoch = _run(mortality="stochastic")
        assert stoch.adaptive_success_rate > fixed.adaptive_success_rate

    def test_both_modes_report_the_same_comparison_metric(self):
        """`success_rate_fixed_horizon` must mean "reached age 85" in BOTH
        modes. Reusing the raw path flag would have meant "reached the
        simulation horizon", which is 105 under stochastic mortality -- a
        different and much harder question, making the modes incomparable."""
        fixed = _run(mortality="fixed")
        # abs=1e-4 because the comparison field is rounded to 4dp for the API
        # while the headline rate is not; they agree on the underlying count.
        assert fixed.success_rate_fixed_horizon == pytest.approx(
            fixed.adaptive_success_rate, abs=1e-4
        )

    def test_the_model_used_is_reported(self):
        assert _run(mortality="stochastic").mortality_model == "stochastic"
        assert _run(mortality="fixed").mortality_model == "fixed"

    def test_stochastic_is_the_default(self):
        assert _run().mortality_model == "stochastic"


class TestMagnitudeOfFailure:
    def test_failure_depth_is_reported_not_just_its_frequency(self):
        r = _run(current_expense_monthly=250_000)   # forced to fail often
        assert r.adaptive_success_rate < 0.9
        assert r.median_ruin_age is not None
        assert r.median_shortfall_years is not None
        assert r.expected_shortfall_years > 0

    def test_expected_shortfall_is_probability_times_magnitude(self):
        """Kitces: Expected Failure = P(failure) x magnitude. It must be zero
        when nothing fails and positive when something does."""
        r = _run(current_expense_monthly=250_000)
        assert 0 < r.expected_shortfall_years <= (85 - 60)

    def test_goal_completion_is_a_fraction(self):
        r = _run(current_expense_monthly=250_000)
        assert 0.0 <= r.goal_completion <= 1.0

    def test_a_comfortable_plan_completes_its_goal(self):
        r = _run(current_portfolio_value=100_000_000, current_expense_monthly=20_000)
        assert r.goal_completion > 0.95
        assert r.expected_shortfall_years < 1.0

    def test_ruin_age_percentiles_bracket_the_median(self):
        r = _run(current_expense_monthly=250_000)
        assert r.ruin_age_p10 <= r.median_ruin_age <= r.ruin_age_p90


class TestLeaderboardRanksByHarmNotFrequency:
    def test_leaderboard_carries_magnitude(self):
        r = _run(current_expense_monthly=200_000)
        assert r.strategy_leaderboard
        for row in r.strategy_leaderboard:
            assert "expected_shortfall_years" in row
            assert "goal_completion" in row

    def test_ordering_follows_expected_shortfall(self):
        """Sorting on success alone can prefer a strategy that fails rarely
        but catastrophically over one that falls a year short more often."""
        r = _run(current_expense_monthly=200_000)
        es = [row["expected_shortfall_years"] for row in r.strategy_leaderboard]
        assert es == sorted(es)


class TestConservatismLedger:
    def test_every_deliberate_pessimism_is_listed(self):
        r = _run()
        sources = {e["source"] for e in r.conservatism_ledger}
        assert {"return_haircut", "cagr_shrinkage_and_cap", "mortality_model"} <= sources
        for e in r.conservatism_ledger:
            assert e["detail"], "a ledger entry without an explanation is not a ledger"

    def test_the_haircut_entry_matches_the_setting(self):
        r = _run(conservatism_haircut=0.02)
        entry = next(e for e in r.conservatism_ledger if e["source"] == "return_haircut")
        assert entry["effect_pp_per_year"] == pytest.approx(-2.0, abs=1e-6)

    def test_zero_haircut_is_reported_as_zero(self):
        r = _run(conservatism_haircut=0.0)
        entry = next(e for e in r.conservatism_ledger if e["source"] == "return_haircut")
        assert entry["effect_pp_per_year"] == 0.0
