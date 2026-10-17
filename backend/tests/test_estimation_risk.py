"""
ARISE estimation risk (parameter uncertainty) and honest success reporting.

The bug these guard: every simulated path shared one expected return -- a
figure estimated from a few years of prices -- as if it were known exactly.
On a long horizon that false precision produced 100% "success", a
probability the model has no basis to claim. See arise_engine §E.
"""
from __future__ import annotations

import math
from types import SimpleNamespace

import pytest

from backend.analytics.portfolio_statistics import RETURN_SHRINKAGE_TAU_YEARS
from backend.engines import arise_engine as ae
from backend.schemas.analysis import ARISEResponse


def _stats(n_years=3.0, source="amfi_nav_history", cagr=0.12, vol=0.15):
    return SimpleNamespace(
        portfolio_cagr=cagr, portfolio_volatility=vol, risk_free_rate_proxy=0.06,
        n_years_history=n_years, data_source=source, scheme_ids=["a"], weights={"a": 1.0},
    )


# A long accumulation phase with a healthy SIP: the shape that hit 100%.
YOUNG = dict(current_age=26, retire_age=60, life_expectancy=85, current_portfolio_value=500_000,
             monthly_sip=20_000, step_up=0.10, current_expense_monthly=40_000,
             n_simulations=600, seed=42)


def test_posterior_sd_matches_the_shrinkage_prior():
    # sd(mu | data) = sigma / sqrt(n + tau): the posterior of the SAME normal
    # model whose mean PortfolioStatisticsBuilder already uses.
    for n in (0.0, 3.0, 10.0, 30.0):
        assert ae.expected_return_posterior_sd(0.15, n) == pytest.approx(
            0.15 / math.sqrt(n + RETURN_SHRINKAGE_TAU_YEARS))


def test_less_history_means_more_uncertainty():
    sds = [ae.expected_return_posterior_sd(0.15, n) for n in (0, 3, 10, 30)]
    assert sds == sorted(sds, reverse=True)
    assert sds[0] > sds[-1] * 1.5


@pytest.mark.parametrize("k,n", [(1000, 1000), (0, 1000), (500, 1000), (7, 10)])
def test_success_is_an_estimate_never_a_certainty(k, n):
    est = ae.success_estimate(k, n)
    assert 0.0 < est["estimate"] < 1.0
    assert est["low"] <= est["estimate"] <= est["high"]
    assert est["successes"] == k and est["paths"] == n


def test_a_perfect_simulation_is_reported_as_very_likely_not_certain():
    est = ae.success_estimate(1000, 1000)
    assert est["estimate"] < 1.0
    # ~ the rule of three: the failure rate is very probably under 0.3%.
    assert 0.996 < est["low"] < 1.0


def test_parameter_uncertainty_does_not_move_the_centre():
    # A correctness fix, not a hidden haircut: the drawn expected return is
    # identical with and without it.
    off = ae.run_monte_carlo(portfolio_stats=_stats(), parameter_uncertainty=False, **YOUNG)
    on = ae.run_monte_carlo(portfolio_stats=_stats(), parameter_uncertainty=True, **YOUNG)
    assert on.expected_return_drawn == off.expected_return_drawn
    assert on.parameter_uncertainty_sd > 0 and off.parameter_uncertainty_sd == 0


def test_parameter_uncertainty_tempers_a_near_certain_plan():
    off = ae.run_monte_carlo(portfolio_stats=_stats(), parameter_uncertainty=False, **YOUNG)
    on = ae.run_monte_carlo(portfolio_stats=_stats(), parameter_uncertainty=True, **YOUNG)
    assert on.adaptive_success_rate < off.adaptive_success_rate
    assert on.robotic_success_rate < off.robotic_success_rate
    # ...and outcomes spread wider, because the evidence allows them to.
    assert (on.terminal_p90 - on.terminal_p10) > (off.terminal_p90 - off.terminal_p10)


def test_more_real_history_earns_more_confidence():
    thin = ae.run_monte_carlo(portfolio_stats=_stats(n_years=2.0), **YOUNG)
    rich = ae.run_monte_carlo(portfolio_stats=_stats(n_years=40.0), **YOUNG)
    assert thin.parameter_uncertainty_sd > rich.parameter_uncertainty_sd
    assert thin.success_rate_estimate <= rich.success_rate_estimate


def test_synthetic_history_buys_no_precision():
    synthetic = ae.run_monte_carlo(portfolio_stats=_stats(n_years=3.0, source="synthetic_calibrated"),
                                   n_simulations=50, **{k: v for k, v in YOUNG.items() if k != "n_simulations"})
    prior_only = ae.expected_return_posterior_sd(0.15, 0.0)
    assert synthetic.parameter_uncertainty_sd == pytest.approx(prior_only, abs=1e-4)


def test_headline_fields_survive_the_response_model():
    # An undeclared engine field is silently dropped by ARISEResponse -- the
    # documented failure mode for v4 fields. Pin that these are declared.
    for f in ("success_rate_estimate", "success_rate_low", "success_rate_high",
              "robotic_success_estimate", "parameter_uncertainty_sd"):
        assert f in ARISEResponse.model_fields, f


def test_each_path_draws_exactly_one_lifespan(monkeypatch):
    # Common random numbers: all strategies on a path must meet the SAME
    # death age. Previously it was drawn once per strategy per path.
    calls = {"n": 0}
    real = ae.draw_lifespan

    def counting(*a, **k):
        calls["n"] += 1
        return real(*a, **k)

    monkeypatch.setattr(ae, "draw_lifespan", counting)
    ae.run_monte_carlo(portfolio_stats=_stats(), leaderboard=True,
                       **{**YOUNG, "n_simulations": 40})
    assert calls["n"] == 40


def test_deterministic_for_a_seed():
    a = ae.run_monte_carlo(portfolio_stats=_stats(), **{**YOUNG, "n_simulations": 120})
    b = ae.run_monte_carlo(portfolio_stats=_stats(), **{**YOUNG, "n_simulations": 120})
    assert a.success_rate_estimate == b.success_rate_estimate
    assert a.terminal_p50 == b.terminal_p50
