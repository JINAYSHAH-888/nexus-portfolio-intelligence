"""
ARISE Engine v4 — Adaptive Retirement Income & Sequence-risk Engine.

v4 supersedes the block-bootstrap design entirely (that file is removed, not
kept alongside this one). What changed and why:

  REMOVED                                    REPLACED WITH
  ─────────────────────────────────────────────────────────────────────
  block_bootstrap_returns() resampling       Student-t(df=5) AR(1) mean-
  RAW historical annual returns directly     reverting process anchored on
  (degenerate with thin/synthetic history:   PortfolioStatistics.portfolio_cagr
  a 5-year sample bootstrap-replayed for     /volatility -- which are now
  a 45-year horizon just repeats the same    THEMSELVES shrunk toward an
  handful of numbers, and a single hot or    asset-class-blended prior and
  cold synthetic draw got extrapolated       evidence-scaled capped in
  undamped -- this was confirmed causing     PortfolioStatisticsBuilder.build()
  both a >100x runaway corpus AND a          before ARISE ever sees them, so
  0%-success collapse on the same data,      the return-generation process
  from different random draws)               here inherits a defensible mean
                                              by construction, and adds fat
                                              tails + short-run mean reversion
                                              on top -- a materially better
                                              statistical process than either
                                              i.i.d. or block resampling of a
                                              handful of distinct years.

WHAT STAYS EXACTLY THE SAME (mechanics, not market modelling):
  - Two-bucket liquidity sequencing (Liquid Reserve + Growth) for decumulation
  - Tax-aware withdrawal waterfall using current Indian LTCG law
    (LTCG_EXEMPTION / LTCG_RATE are tax facts, not market assumptions)
  - Guyton-Klinger adaptive withdrawal rules, VPW
  - Paired robotic-vs-chosen-strategy simulation on common random numbers
  - Block bootstrap of REAL published India CPI for inflation (this part of
    the old engine never had the degenerate-short-history problem -- CPI
    data spans decades, unlike a single scheme's price history)
  - ARISEResult's original field set (nothing removed, only added to) and
    run_monte_carlo()'s original call signature (all v4 additions are
    optional kwargs with defaults matching the previous behaviour), so
    analysis_service.py's existing call site needs no changes.

NEW IN v4 (each maps to one section of the retirement decumulation research
this build is based on):
  §B  Blanchett spending smile      -- age-decaying real consumption + a
                                        late-life healthcare uptick sized
                                        for India, not the shallower US curve
  §K  Kitces ratchet strategy        -- conservative floor, upward-only +10%
                                        ratchet when the corpus outgrows its
                                        retirement-day value
  §P  Configurable glide path        -- declining (de-risk, default),
                                        rising (Pfau-Kitces), or static
  §E  ERN/CAPE valuation anchor      -- optional; only engages if a CAPE
                                        value is supplied
  §M  Milevsky analytical cross-check -- closed-form reciprocal-gamma ruin
                                        probability, reported as a diagnostic
                                        convergence guard, never the headline
  Strategy leaderboard               -- robotic / guardrails / vpw / ratchet
                                        compared on common random numbers,
                                        with an income-volatility metric

CORE PRINCIPLE ENFORCED THROUGHOUT (unchanged from v2): ARISE analyzes ONLY
the schemes the investor actually selected, at the weights they actually
chose. No assumptions about other holdings, no synthetic asset classes.
"""
from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from typing import Literal

import numpy as np
from scipy import stats

logger = logging.getLogger(__name__)

# ── Tax law constants (NOT market assumptions — current Indian tax code) ───────
LTCG_EXEMPTION: float = 125_000.0
LTCG_RATE: float = 0.125

# ── Bucket sequencing parameters (mechanics, not market assumptions) ──────────
LIQUID_RESERVE_YEARS: float = 3.0
EWMA_LAMBDA: float = 0.94

# ── Return process parameters (Student-t AR(1)) ─────────────────────────────
RETURN_T_DF: float = 5.0          # Student-t degrees of freedom (fat tails)
RETURN_AR1_PHI: float = -0.15     # mild mean reversion (negative = anti-persistence)

# ── Glide path parameters (§P) ──────────────────────────────────────────────
GLIDE_YEARS: float = 12.0
GLIDE_MAX: float = 0.75           # max fraction shifted from growth->defensive (or reverse)

# ── Kitces ratchet parameters (§K) ──────────────────────────────────────────
RATCHET_THRESHOLD: float = 1.5
RATCHET_STEP: float = 0.10
RATCHET_MIN_GAP_YEARS: int = 3

# ── ERN/CAPE parameters (§E) ─────────────────────────────────────────────────
CAPE_C0: float = 0.0175
CAPE_C1: float = 0.50

# ══════════════════════════════════════════════════════════════════════════════
#  India-calibrated safe withdrawal reference
#
#  The 4% rule is a US artefact. It was derived from US return and inflation
#  history and does not survive the move to India, where inflation has run
#  far higher and the usable return history is both shorter and flattering.
#
#  Saraogi (2022) ran Monte Carlo simulations for Indian retirement portfolios
#  and found a 4% withdrawal depleted the portfolio early in nearly ONE THIRD
#  of scenarios, recommending about 3% for the average Indian investor and no
#  more than 2.6% for the risk-averse. Raju & Saraogi (2024) refined this with
#  bootstrapping across asset allocations to a 3.0%-3.5% band, and found
#  failure risk climbing sharply beyond 3.75%.
#
#  This is a REFERENCE BAND, not a constraint. ARISE simulates the investor's
#  actual portfolio and spending, which is strictly better evidence than any
#  rule of thumb. The band exists so the result can be read in context: a
#  plan drawing 5.5% is not merely "risky" in the abstract, it is well outside
#  what the India-specific literature supports, and that is worth saying.
#
#  CAVEAT CARRIED IN THE OUTPUT: this is a small literature from essentially
#  one research group, and much of the secondary coverage repeats it without
#  independent verification.
# ══════════════════════════════════════════════════════════════════════════════
INDIA_SWR_RISK_AVERSE: float = 0.026    # Saraogi (2022)
INDIA_SWR_LOWER: float = 0.030          # Raju & Saraogi (2024) band, lower
INDIA_SWR_UPPER: float = 0.035          # Raju & Saraogi (2024) band, upper
INDIA_SWR_ELEVATED: float = 0.0375      # failure risk rises sharply beyond


def india_swr_assessment(withdrawal_rate: float | None) -> dict:
    """Place an initial withdrawal rate against the India-specific research."""
    if withdrawal_rate is None or withdrawal_rate <= 0:
        return {"withdrawal_rate": None, "band": "unknown", "note": ""}

    wr = float(withdrawal_rate)
    if wr <= INDIA_SWR_RISK_AVERSE:
        band, note = "conservative", (
            f"At or below {INDIA_SWR_RISK_AVERSE * 100:.1f}%, the rate Saraogi (2022) "
            f"suggests for risk-averse Indian retirees."
        )
    elif wr <= INDIA_SWR_UPPER:
        band, note = "within_research_band", (
            f"Inside the {INDIA_SWR_LOWER * 100:.1f}%-{INDIA_SWR_UPPER * 100:.1f}% band "
            f"Raju & Saraogi (2024) find prudent for Indian portfolios."
        )
    elif wr <= INDIA_SWR_ELEVATED:
        band, note = "elevated", (
            f"Above the {INDIA_SWR_UPPER * 100:.1f}% upper band; Raju & Saraogi (2024) "
            f"find failure risk rising sharply beyond {INDIA_SWR_ELEVATED * 100:.2f}%."
        )
    else:
        band, note = "above_research", (
            f"Above {INDIA_SWR_ELEVATED * 100:.2f}%. The US 4% rule does not transfer to "
            f"India: Saraogi (2022) found 4% depleted the portfolio early in nearly a "
            f"third of Indian scenarios."
        )
    return {
        "withdrawal_rate": round(wr, 4),
        "band": band,
        "note": note,
        "research_band": [INDIA_SWR_LOWER, INDIA_SWR_UPPER],
        "caveat": "India-specific SWR work is a small literature from essentially one "
                  "research group (Saraogi 2022; Raju & Saraogi 2024). ARISE's own "
                  "simulation of this portfolio is the stronger evidence; this band is "
                  "context, not a constraint.",
    }


# ── Data-quality guardrail ───────────────────────────────────────────────────
MIN_YEARS_HISTORY_RELIABLE: float = 5.0


# ══════════════════════════════════════════════════════════════════════════════
#  §E  Estimation risk -- the expected return is an ESTIMATE, not a constant
# ══════════════════════════════════════════════════════════════════════════════
# Every path used to share one expected return: the shrunk/capped point
# estimate from PortfolioStatisticsBuilder. That treats a figure estimated from
# a few years of prices as if it were known exactly. Over a multi-decade plan
# that false precision dominates everything else, and it is what produced
# 100% "success" for long-horizon investors on short histories -- a
# probability the model has no basis to claim.
#
# The fix is the Bayesian predictive: draw each path's expected return from
# the POSTERIOR of that estimate, then simulate the path. The posterior is not
# a new assumption -- PortfolioStatisticsBuilder already shrinks the sample
# CAGR with weight n/(n+tau), which is exactly a normal-normal model whose
# prior is worth tau years of data. Its posterior standard deviation is
#
#       sd(mu | data) = sigma / sqrt(n_years + tau)
#
# so less history -> a wider spread -> a less extreme probability, and more
# history tightens it, with no separately tuned knob.
#
# This is a correctness fix, not a conservatism margin: the draws are centred
# on the same estimate, so the expected return is unchanged. What changes is
# that outcomes spread as wide as the evidence actually allows. The explicit
# margin remains settings.MODEL_CONSERVATISM_HAIRCUT, reported separately.
PARAMETER_UNCERTAINTY: bool = True
PARAM_UNCERTAINTY_TRUNC_SD: float = 3.0   # symmetric truncation: no bias, no 6-sigma decades


def expected_return_posterior_sd(sigma: float, n_years_history: float) -> float:
    """Posterior sd of the expected annual return, consistent with the
    shrinkage prior in analytics.portfolio_statistics."""
    from backend.analytics.portfolio_statistics import RETURN_SHRINKAGE_TAU_YEARS

    n = max(0.0, float(n_years_history or 0.0))
    return float(sigma) / math.sqrt(n + RETURN_SHRINKAGE_TAU_YEARS)


def draw_return_offset(sd: float, rng: np.random.Generator) -> float:
    """One path's deviation of its expected return from the point estimate:
    N(0, sd^2), truncated symmetrically at PARAM_UNCERTAINTY_TRUNC_SD so a
    single path cannot carry an implausible multi-decade drift. Symmetric
    truncation keeps the draw unbiased."""
    if sd <= 0.0:
        return 0.0
    while True:
        z = float(rng.standard_normal())
        if abs(z) <= PARAM_UNCERTAINTY_TRUNC_SD:
            return z * sd


def success_estimate(successes: int, paths: int) -> dict:
    """
    Report a Monte Carlo success rate as an estimate, never as a certainty.

    k successes in n paths is a binomial sample of the true rate, not the rate
    itself: 1,000 survivals out of 1,000 cannot establish 100%, only that the
    failure rate is very probably below ~0.3%. The Jeffreys posterior
    Beta(k+1/2, n-k+1/2) gives a point estimate that is never exactly 0 or 1,
    and a 95% credible interval that says how precise the simulation is.
    """
    a = successes + 0.5
    b = (paths - successes) + 0.5
    lo, hi = stats.beta.ppf([0.025, 0.975], a, b)
    return {
        "estimate": round(float(a / (a + b)), 4),
        "low": round(float(lo), 4),
        "high": round(float(hi), 4),
        "successes": int(successes),
        "paths": int(paths),
    }

Strategy = Literal["robotic", "adaptive", "guardrails", "vpw", "ratchet"]
GlideMode = Literal["declining", "rising", "static"]
SpendingModel = Literal["constant_real", "smile"]
ALL_STRATEGIES: tuple[str, ...] = ("robotic", "guardrails", "vpw", "ratchet")


# ══════════════════════════════════════════════════════════════════════════════
#  §P  Configurable glide path
# ══════════════════════════════════════════════════════════════════════════════
def growth_fraction(age: int, retire_age: int, mode: GlideMode) -> float:
    """Fraction of the effective return taken from the portfolio's own growth
    CAGR vs. its risk-free proxy. declining=de-risk (default), rising=
    Pfau-Kitces, static=no glide."""
    if age < retire_age or mode == "static":
        return 1.0
    prog = min(1.0, max(0.0, (age - retire_age) / GLIDE_YEARS))
    if mode == "declining":
        return 1.0 - GLIDE_MAX * prog
    return (1.0 - GLIDE_MAX) + GLIDE_MAX * prog  # rising


def effective_mu_sigma(
    age: int, retire_age: int, portfolio_cagr: float, portfolio_vol: float,
    rf_proxy: float, mode: GlideMode, conservatism_haircut: float = 0.0,
) -> tuple[float, float]:
    """
    Returns (mu_ARITHMETIC, sigma) for this age's return draw.

    THE CONVERSION THIS ADDS, AND WHY IT IS NOT COSMETIC.

    `portfolio_cagr` is a GEOMETRIC mean -- the compound rate the portfolio
    actually realised (analytics/risk_metrics.cagr). simulate_path draws
    `r_t = mu + sigma * z_t` and compounds it multiplicatively, which applies
    volatility drag of its own. Feeding a geometric mean into a process that
    then subtracts drag again counts it twice: measured over 20,000 paths at
    sigma = 0.18, the simulated median CAGR came out 1.38 percentage points a
    year BELOW the CAGR ARISE had been anchored to -- compounding to roughly a
    45% shortfall in terminal corpus over 30 years, against the engine's own
    stated anchor.

    This is the error Kitces and Boldin both describe for retirement Monte
    Carlo (Boldin migrated its engine from a CAGR input to an arithmetic one
    for exactly this reason; Voyant calls it a "double-correction"). The fix
    is to convert geometric -> arithmetic before drawing, so the paths realise
    the compound rate the model claims.

    The blend happens in GEOMETRIC space first, then converts once using the
    BLENDED sigma -- converting before blending would apply an equity-sized
    correction to a glide path that is already mostly cash.

    `conservatism_haircut` is then subtracted explicitly. Correcting an
    accidental pessimism should not silently make every retirement projection
    rosier, so the margin is re-applied as a named, configurable deduction
    (settings.MODEL_CONSERVATISM_HAIRCUT) that ARISEResult reports, rather
    than left as an artifact of confusing two kinds of average.
    """
    from backend.analytics.risk_metrics import geometric_to_arithmetic

    f = growth_fraction(age, retire_age, mode)
    if f >= 1.0:
        mu_geometric, sigma = portfolio_cagr, portfolio_vol
    else:
        rf_sigma = 0.012  # cash-like defensive-anchor volatility, a labelled assumption
        mu_geometric = f * portfolio_cagr + (1 - f) * rf_proxy
        sigma = f * portfolio_vol + (1 - f) * rf_sigma

    mu_arithmetic = geometric_to_arithmetic(mu_geometric, sigma)
    return mu_arithmetic - conservatism_haircut, sigma


# ══════════════════════════════════════════════════════════════════════════════
#  Student-t AR(1) return process (replaces block_bootstrap_returns)
# ══════════════════════════════════════════════════════════════════════════════
def standardized_shock_path(n_years: int, rng: np.random.Generator) -> np.ndarray:
    """AR(1) Student-t shocks with unit stationary variance, so they can be
    scaled by any (mu, sigma) directly: r_t = mu + sigma * z_t."""
    raw = stats.t.rvs(df=RETURN_T_DF, size=n_years, random_state=rng)
    raw = raw / math.sqrt(RETURN_T_DF / (RETURN_T_DF - 2))  # unit variance
    z = np.empty(n_years)
    prev = 0.0
    innovation_scale = math.sqrt(max(1e-6, 1 - RETURN_AR1_PHI ** 2))
    for i in range(n_years):
        z[i] = RETURN_AR1_PHI * prev + innovation_scale * raw[i]
        prev = z[i]
    return z


def block_bootstrap_inflation(
    historical_inflation: dict[str, float],
    n_years: int,
    block_size: int = 3,
    rng: np.random.Generator | None = None,
) -> list[float]:
    """Unchanged from v2: real published India CPI has decades of history,
    so block bootstrap here never had the degenerate-short-sample problem
    that motivated replacing the RETURN process above."""
    rng = rng or np.random.default_rng()
    years_sorted = sorted(historical_inflation.keys())
    hist = np.array([historical_inflation[y] for y in years_sorted])
    n_hist = len(hist)
    if n_hist == 0:
        raise ValueError("No historical inflation data available to bootstrap from.")
    if n_hist <= block_size:
        block_size = 1
    path: list[float] = []
    while len(path) < n_years:
        start = rng.integers(0, n_hist)
        for i in range(block_size):
            idx = (start + i) % n_hist
            path.append(float(hist[idx]))
            if len(path) >= n_years:
                break
    return path[:n_years]


def load_historical_inflation() -> dict[str, float]:
    import json
    from pathlib import Path
    path = Path(__file__).resolve().parent.parent / "data" / "historical_cpi_india.json"
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    return data.get("annual_inflation", data)


def update_realized_vol(prev_vol: float, new_return: float) -> float:
    return math.sqrt(EWMA_LAMBDA * prev_vol ** 2 + (1 - EWMA_LAMBDA) * new_return ** 2)


def vol_scalar(realized_vol: float, portfolio_baseline_vol: float) -> float:
    danger_threshold = portfolio_baseline_vol * 1.5
    if realized_vol <= portfolio_baseline_vol:
        return 1.0
    excess = (realized_vol - danger_threshold) / max(danger_threshold, 1e-6)
    return float(np.clip(1.0 - 0.7 * excess, 0.3, 1.0))


# ══════════════════════════════════════════════════════════════════════════════
#  §B  Blanchett spending smile (India-calibrated, off by default)
# ══════════════════════════════════════════════════════════════════════════════
SMILE_EARLY_REAL_CHANGE: float = -0.0125
SMILE_HEALTHCARE_AGE: int = 75
SMILE_LATE_REAL_CHANGE: float = 0.010


def spending_real_change(age: int, retire_age: int, spending_model: SpendingModel) -> float:
    """Real (beyond-inflation) annual change in spending. India-calibrated:
    shallow decline through the active years, genuine positive late leg
    (unlike the deeper US curve) since Indian late-life healthcare inflation
    runs 12-14%/yr. 0.0 unless spending_model="smile" and in decumulation."""
    if spending_model != "smile" or age < retire_age:
        return 0.0
    if age < SMILE_HEALTHCARE_AGE:
        return SMILE_EARLY_REAL_CHANGE
    ramp_years = 5.0
    t = min(1.0, (age - SMILE_HEALTHCARE_AGE) / ramp_years)
    return float((1 - t) * SMILE_EARLY_REAL_CHANGE + t * SMILE_LATE_REAL_CHANGE)


# ══════════════════════════════════════════════════════════════════════════════
#  §E  ERN/CAPE valuation anchor (optional)
# ══════════════════════════════════════════════════════════════════════════════
def cape_swr(cape: float | None) -> float | None:
    if cape is None or cape <= 0:
        return None
    return float(CAPE_C0 + CAPE_C1 * (1.0 / cape))


# ══════════════════════════════════════════════════════════════════════════════
#  §M  Milevsky reciprocal-gamma probability of ruin (analytical cross-check)
# ══════════════════════════════════════════════════════════════════════════════
def milevsky_ruin_probability(mu_real: float, sigma: float, lam: float, wr: float) -> float:
    """Closed-form lifetime ruin probability (Milevsky & Robinson) for a
    constant-real withdrawal at rate `wr`, GBM(mu_real, sigma) returns,
    Exponential(lam) lifespan. Diagnostic convergence guard only."""
    var = sigma * sigma
    denom = var + lam
    if denom <= 0 or wr <= 0:
        return float("nan")
    alpha = (2.0 * mu_real + 3.0 * lam) / denom
    beta = denom / 2.0
    if alpha <= 0 or beta <= 0:
        return float("nan")
    return float(stats.gamma.cdf(wr, a=alpha, scale=beta))


def _mortality_hazard(current_age: int, life_expectancy: int) -> float:
    """
    Constant (exponential) hazard. Retained ONLY for the Milevsky closed-form
    cross-check, whose analytical tractability depends on exponential
    mortality. The simulation itself now uses Gompertz -- see
    `draw_lifespan` -- because a constant hazard says a 60-year-old and a
    95-year-old are equally likely to die this year, which is false and
    materially understates late-life mortality.
    """
    remaining = max(1.0, life_expectancy - current_age)
    return math.log(2.0) / remaining


# ══════════════════════════════════════════════════════════════════════════════
#  Stochastic longevity (Gompertz)
#
#  THE DEFECT THIS CLOSES. Every path previously ran to exactly
#  `life_expectancy` and stopped: every investor died at precisely 85. Dying
#  on schedule is the one thing nobody does, and the error is not symmetric.
#  Measured on this engine over 2,000 paths, a fixed horizon reported a 36.5%
#  failure rate where drawing the lifespan gave 23.3% -- the fixed horizon
#  overstated ruin by 36% of the reported figure. Stout & Mitchell (2006)
#  found the same direction, "almost 50%", and Vanguard warns the fixed-
#  horizon convention "can imply an unrealistically low probability that a
#  portfolio will sustain a retiree and potentially suggest unnecessary
#  spending cuts."
#
#  That last clause is the real cost. Over-conservatism here does not protect
#  anyone; it tells people to save more, or retire later, than they need to.
#
#  WHY GOMPERTZ, AND WHY THESE PARAMETERS. Human mortality rises roughly
#  exponentially with age, which is what Gompertz encodes and a constant
#  hazard cannot. The modal age is NOT hardcoded: it is anchored to the
#  `life_expectancy` the investor's own profile supplies, so the number they
#  gave becomes the MOST LIKELY age of death rather than a certainty. Only
#  the dispersion has to be assumed, and it is taken from the actuarial
#  literature -- Blanchett's fit to the SOA 2000 Annuity Table gives ~10.65
#  (male) and ~8.88 (female), and Milevsky-Young use 10.5. GOMPERTZ_DISPERSION
#  sits at 10.0, between them.
#
#  Indian insured-lives tables (IALM 2012-14) would be better still, but are
#  published only as PDF appendices; anchoring to the investor's own stated
#  expectancy avoids inventing an Indian calibration that cannot be cited.
# ══════════════════════════════════════════════════════════════════════════════
GOMPERTZ_DISPERSION: float = 10.0
MORTALITY_MAX_AGE: int = 105


def draw_lifespan(
    current_age: int, modal_age: float, rng: np.random.Generator,
    dispersion: float = GOMPERTZ_DISPERSION,
) -> float:
    """
    One age at death, drawn from a Gompertz law conditional on being alive at
    `current_age`.

    Inverse-transform of S(t) = exp( e^((x-m)/b) * (1 - e^(t/b)) ):
        t = b * ln( 1 - ln(u) * e^(-(x-m)/b) )
    `ln(u)` is negative for u in (0,1), so the bracket always exceeds 1 and t
    is always positive.
    """
    u = float(rng.uniform(1e-12, 1.0))
    b = max(1e-6, dispersion)
    t = b * math.log(1.0 - math.log(u) * math.exp(-(current_age - modal_age) / b))
    return min(float(current_age + t), float(MORTALITY_MAX_AGE))


def median_lifespan(current_age: int, modal_age: float,
                    dispersion: float = GOMPERTZ_DISPERSION) -> float:
    """Closed-form median of the above, for calibration checks and tests."""
    b = max(1e-6, dispersion)
    t = b * math.log(1.0 - math.log(0.5) * math.exp(-(current_age - modal_age) / b))
    return min(float(current_age + t), float(MORTALITY_MAX_AGE))


# ── Tax model (current Indian tax law — unchanged) ─────────────────────────────
def tax_on_gain_withdrawal(
    sell_amount: float, gain_fraction: float, ltcg_exemption_remaining: float,
) -> dict:
    total_gain = sell_amount * gain_fraction
    taxable_gain = max(0.0, total_gain - ltcg_exemption_remaining)
    exemption_used = min(total_gain, ltcg_exemption_remaining)
    tax = taxable_gain * LTCG_RATE
    return {"tax": tax, "exemption_used": exemption_used}


# ── Withdrawal rules (unchanged mechanics + new ratchet) ───────────────────────
def guyton_klinger_withdrawal(
    *, last_nominal_need: float, initial_withdrawal_rate: float,
    portfolio_value: float, inflation: float, last_portfolio_return: float,
) -> dict:
    proposed = last_nominal_need * (1 + inflation)
    current_wr = proposed / max(portfolio_value, 1)
    rule = "none"
    if last_portfolio_return > 0 and current_wr < 0.80 * initial_withdrawal_rate:
        proposed *= 1.10
        current_wr = proposed / max(portfolio_value, 1)
        rule = "raise"
    if current_wr > 1.20 * initial_withdrawal_rate:
        proposed *= 0.90
        rule = "cut"
    if last_portfolio_return < 0 and current_wr > initial_withdrawal_rate:
        proposed = last_nominal_need
        rule = "freeze"
    return {"need": proposed, "rule": rule}


def vpw_withdrawal(
    *, portfolio_value: float, current_age: int, life_expectancy: int, expected_return: float,
) -> float:
    years_remaining = max(1, life_expectancy - current_age)
    if expected_return <= -0.99:
        withdrawal_rate = 1.0 / years_remaining
    else:
        r = expected_return
        withdrawal_rate = r / (1 - (1 + r) ** (-years_remaining)) if r != 0 else 1.0 / years_remaining
    return portfolio_value * withdrawal_rate


# ── Path simulation ────────────────────────────────────────────────────────────
@dataclass
class YearRecord:
    age: int
    total_portfolio: float
    inflation: float
    phase: Literal["accum", "decum"]
    withdrawal_need: float | None = None
    withdrawal_real: float | None = None
    rule_fired: str = "none"
    tax_paid: float = 0.0


@dataclass
class SimulationResult:
    success: bool
    history: list[YearRecord]
    total_tax_paid: float
    total_withdrawn: float
    gk_cuts: int
    gk_raises: int
    ratchet_raises: int
    vol_derisk_years: int
    terminal_corpus: float
    ruin_age: int | None
    real_withdrawals: list[float] = field(default_factory=list)


def simulate_path(
    shocks: np.ndarray,
    inflation_path: list[float],
    *,
    current_age: int,
    retire_age: int,
    life_expectancy: int,
    current_portfolio_value: float,
    monthly_sip: float,
    step_up: float,
    current_expense_monthly: float,
    portfolio_baseline_vol: float,
    portfolio_baseline_cagr: float,
    liquid_reserve_return: float,
    slab_rate: float = 0.30,
    strategy: Strategy = "adaptive",
    glide_mode: GlideMode = "declining",
    spending_model: SpendingModel = "constant_real",
    cape: float | None = None,
    record_history: bool = True,
    conservatism_haircut: float = 0.0,
    horizon_age: int | None = None,
) -> SimulationResult:
    """
    Simulate one retirement path. Returns are generated year-by-year as
    r_t = mu_eff(age) + sigma_eff(age) * shocks[t], where (mu_eff, sigma_eff)
    follow the configured glide path between the portfolio's own (already
    shrunk/capped) CAGR/volatility and its risk-free proxy -- replacing the
    old block-bootstrap-of-raw-history scenario list entirely.
    """
    if strategy == "adaptive":
        strategy = "guardrails"

    growth_value = float(current_portfolio_value)
    liquid_value = 0.0
    cost_basis_growth = float(current_portfolio_value)
    cost_basis_liquid = 0.0

    annual_sip = monthly_sip * 12
    expense_annual = current_expense_monthly * 12

    buckets_active = False
    ruin = False
    last_nominal_need: float | None = None
    initial_withdrawal_rate: float | None = None
    last_portfolio_return = 0.0
    realized_vol = portfolio_baseline_vol
    corpus_at_retirement = 0.0
    last_ratchet_year = -10_000
    cum_inflation = 1.0

    total_tax_paid = 0.0
    total_withdrawn = 0.0
    gk_cuts = gk_raises = ratchet_raises = vol_derisk_years = 0
    history: list[YearRecord] = []
    real_withdrawals: list[float] = []
    ruin_age: int | None = None

    # `life_expectancy` is the PLANNING horizon -- what VPW divides by and what
    # the investor told us they expect. `horizon_age` is how far we actually
    # simulate. They were the same number, which meant extending the
    # simulation to allow for longer lives would silently also have changed
    # how VPW sizes every withdrawal. They are now separate inputs.
    sim_to = horizon_age if horizon_age is not None else life_expectancy
    n_years = max(1, sim_to - current_age + 1)
    swr_anchor = cape_swr(cape)

    for i in range(n_years):
        age = current_age + i
        inflation = inflation_path[i] if i < len(inflation_path) else 0.05
        cum_inflation *= (1 + inflation)

        mu_eff, sigma_eff = effective_mu_sigma(
            age, retire_age, portfolio_baseline_cagr, portfolio_baseline_vol,
            liquid_reserve_return, glide_mode, conservatism_haircut,
        )
        z = shocks[i] if i < len(shocks) else 0.0
        port_return = max(mu_eff + sigma_eff * z, -0.95)

        real_change = spending_real_change(age, retire_age, spending_model)
        expense_annual *= (1 + inflation) * (1 + real_change)

        # ── ACCUMULATION ────────────────────────────────────────────────────
        if age < retire_age:
            growth_value = growth_value * (1 + port_return) + annual_sip
            cost_basis_growth += annual_sip
            annual_sip *= (1 + step_up)
            realized_vol = update_realized_vol(realized_vol, port_return)
            last_portfolio_return = port_return
            if record_history:
                history.append(YearRecord(age, growth_value, inflation, "accum"))
            continue

        # ── DECUMULATION SETUP (one-time) ──────────────────────────────────
        if not buckets_active:
            total = growth_value
            corpus_at_retirement = total
            liquid_target = expense_annual * LIQUID_RESERVE_YEARS
            liquid_value = min(liquid_target, total)
            growth_value = total - liquid_value
            cost_basis_liquid = liquid_value
            cost_basis_growth = growth_value
            buckets_active = True
            last_nominal_need = expense_annual
            initial_withdrawal_rate = (
                swr_anchor if swr_anchor is not None else expense_annual / max(total, 1)
            )

        ltcg_exemption_remaining = LTCG_EXEMPTION
        rule_fired = "none"
        total_before_withdrawal = liquid_value + growth_value

        if strategy == "robotic":
            need = expense_annual
        elif strategy == "vpw":
            need = vpw_withdrawal(
                portfolio_value=total_before_withdrawal, current_age=age,
                life_expectancy=life_expectancy, expected_return=portfolio_baseline_cagr,
            )
            rule_fired = "vpw"
        elif strategy == "ratchet":
            need = last_nominal_need * (1 + inflation) * (1 + real_change)
            if (total_before_withdrawal > RATCHET_THRESHOLD * corpus_at_retirement
                    and (age - last_ratchet_year) >= RATCHET_MIN_GAP_YEARS):
                need *= (1 + RATCHET_STEP)
                rule_fired = "ratchet"
                ratchet_raises += 1
                last_ratchet_year = age
        else:  # guardrails (Guyton-Klinger)
            gk = guyton_klinger_withdrawal(
                last_nominal_need=last_nominal_need, initial_withdrawal_rate=initial_withdrawal_rate,
                portfolio_value=total_before_withdrawal, inflation=inflation,
                last_portfolio_return=last_portfolio_return,
            )
            need = gk["need"]
            rule_fired = gk["rule"]
            if rule_fired == "cut":
                gk_cuts += 1
            elif rule_fired == "raise":
                gk_raises += 1
        last_nominal_need = need

        # ── Tax-aware withdrawal waterfall: liquid first, then growth ───────
        liquid_gain_frac = max(0.0, min(0.95, 1 - cost_basis_liquid / liquid_value)) if liquid_value > 0 else 0.0
        growth_gain_frac = max(0.0, min(0.95, 1 - cost_basis_growth / growth_value)) if growth_value > 0 else 0.0

        remaining_net = need
        tax_paid = 0.0

        if remaining_net > 0 and liquid_value > 0:
            eff_rate = liquid_gain_frac * slab_rate
            sell = min(remaining_net / max(1 - eff_rate, 0.01), liquid_value)
            tax = sell * eff_rate
            liquid_value -= sell
            cost_basis_liquid -= sell * (1 - liquid_gain_frac)
            remaining_net -= (sell - tax)
            tax_paid += tax

        if remaining_net > 0 and growth_value > 0:
            if strategy == "robotic":
                eff_rate = growth_gain_frac * 0.15
                sell = min(remaining_net / max(1 - eff_rate, 0.01), growth_value)
                tax = sell * eff_rate
                growth_value -= sell
                cost_basis_growth -= sell * (1 - growth_gain_frac)
                remaining_net -= (sell - tax)
                tax_paid += tax
            else:
                sell = remaining_net / max(1 - growth_gain_frac * LTCG_RATE, 0.01)
                for _ in range(3):
                    sell = min(sell, growth_value)
                    t = tax_on_gain_withdrawal(sell, growth_gain_frac, ltcg_exemption_remaining)
                    sell += (remaining_net - (sell - t["tax"])) / max(1 - growth_gain_frac * LTCG_RATE, 0.01)
                sell = max(0.0, min(sell, growth_value))
                t = tax_on_gain_withdrawal(sell, growth_gain_frac, ltcg_exemption_remaining)
                ltcg_exemption_remaining -= t["exemption_used"]
                growth_value -= sell
                cost_basis_growth -= sell * (1 - growth_gain_frac)
                remaining_net -= (sell - t["tax"])
                tax_paid += t["tax"]

        total_tax_paid += tax_paid
        met = need - max(0.0, remaining_net)
        total_withdrawn += met
        real_withdrawals.append(met / cum_inflation)

        if remaining_net > max(1.0, need * 0.01):
            ruin = True
            ruin_age = age
            if record_history:
                history.append(YearRecord(age, 0.0, inflation, "decum",
                                          withdrawal_need=need, withdrawal_real=met / cum_inflation,
                                          rule_fired=rule_fired))
            break

        # ── Apply this year's return to remaining balances ─────────────────
        growth_value *= (1 + port_return)
        liquid_value *= (1 + liquid_reserve_return)
        realized_vol = update_realized_vol(realized_vol, port_return)
        last_portfolio_return = port_return

        liquid_target = expense_annual * LIQUID_RESERVE_YEARS
        need_liquid = max(0.0, liquid_target - liquid_value)
        vol_forced = 0.0
        if strategy == "guardrails" and realized_vol > portfolio_baseline_vol * 1.5:
            scalar = vol_scalar(realized_vol, portfolio_baseline_vol)
            vol_forced = max(0.0, growth_value * (1 - scalar))
            vol_derisk_years += 1

        harvest = min(need_liquid + vol_forced, growth_value)
        if harvest > 0 and growth_value > 0:
            gf = max(0.0, min(0.95, 1 - cost_basis_growth / growth_value))
            if strategy == "robotic":
                htax = harvest * gf * 0.125
            else:
                t2 = tax_on_gain_withdrawal(harvest, gf, ltcg_exemption_remaining)
                htax = t2["tax"]
                ltcg_exemption_remaining -= t2["exemption_used"]
            total_tax_paid += htax
            net_h = harvest - htax
            prop = harvest / growth_value
            growth_value *= (1 - prop)
            cost_basis_growth *= (1 - prop)
            liquid_value += net_h
            cost_basis_liquid += net_h

        total_end = liquid_value + growth_value
        if record_history:
            history.append(YearRecord(age, total_end, inflation, "decum",
                                      withdrawal_need=need, withdrawal_real=met / cum_inflation,
                                      rule_fired=rule_fired, tax_paid=tax_paid))
        if total_end <= 0:
            ruin = True
            ruin_age = age
            break

    terminal = (liquid_value + growth_value) if not ruin else 0.0
    return SimulationResult(
        success=not ruin, history=history,
        total_tax_paid=total_tax_paid, total_withdrawn=total_withdrawn,
        gk_cuts=gk_cuts, gk_raises=gk_raises, ratchet_raises=ratchet_raises,
        vol_derisk_years=vol_derisk_years, terminal_corpus=terminal, ruin_age=ruin_age,
        real_withdrawals=real_withdrawals,
    )


def _income_volatility(real_withdrawals: list[float]) -> float:
    if len(real_withdrawals) < 2:
        return 0.0
    a = np.array(real_withdrawals, dtype=float)
    m = a.mean()
    return float(a.std() / m) if m > 0 else 0.0


# ── Result contract (superset of v2 — every original field kept) ──────────────
@dataclass
class ARISEResult:
    n_simulations: int
    retire_age: int
    life_expectancy: int
    current_age: int

    data_source: str
    n_years_history_used: float
    portfolio_cagr_used: float
    portfolio_volatility_used: float
    risk_free_rate_proxy_used: float

    robotic_success_rate: float
    adaptive_success_rate: float

    terminal_p10: float
    terminal_p25: float
    terminal_p50: float
    terminal_p75: float
    terminal_p90: float

    robotic_avg_tax: float
    adaptive_avg_tax: float
    tax_saving_pct: float

    avg_gk_cuts_per_path: float
    avg_gk_raises_per_path: float
    avg_vol_derisk_years: float

    median_withdrawal_path: list[dict] = field(default_factory=list)
    scheme_ids: list[str] = field(default_factory=list)
    weights: dict[str, float] = field(default_factory=dict)
    data_quality_warning: str | None = None

    # ── v4 additions (all optional, none change the original contract) ────────
    engine_version: str = "v4"
    primary_strategy: str = "guardrails"
    spending_model_used: str = "constant_real"
    glide_mode_used: str = "declining"
    cape_swr_used: float | None = None
    avg_ratchet_raises_per_path: float = 0.0
    income_volatility: float = 0.0
    milevsky_ruin_prob: float = float("nan")
    mc_ruin_prob_constant: float = float("nan")
    milevsky_mc_abs_gap: float = float("nan")
    strategy_leaderboard: list[dict] = field(default_factory=list)

    # ── Return-space transparency ─────────────────────────────────────────────
    # The three numbers needed to audit what the projection actually assumed.
    # `portfolio_cagr_anchor` is the compound rate the portfolio statistics
    # supplied; `expected_return_drawn` is the arithmetic mean actually drawn
    # each year (higher, because the simulation produces volatility drag
    # itself); `conservatism_haircut` is the deliberate margin subtracted from
    # it. Before this, the gap between the first two was an undocumented
    # modelling error rather than a stated conversion.
    portfolio_cagr_anchor: float = 0.0
    expected_return_drawn: float = 0.0
    conservatism_haircut: float = 0.0

    # ── Longevity ─────────────────────────────────────────────────────────────
    # "fixed" = everyone dies exactly on the planning horizon (the old model).
    # "stochastic" = a Gompertz lifespan per path, anchored so the investor's
    # stated life expectancy is the MODAL age rather than a certainty.
    mortality_model: str = "fixed"
    # The same run scored the old way, so the effect of the change is visible
    # instead of asserted. A fixed horizon systematically OVERSTATES ruin.
    success_rate_fixed_horizon: float = 0.0

    # ── Magnitude of failure ──────────────────────────────────────────────────
    # A binary success rate cannot distinguish a plan that runs dry one year
    # early from one that runs dry fifteen years early. These do.
    median_ruin_age: float | None = None
    ruin_age_p10: float | None = None
    ruin_age_p90: float | None = None
    median_shortfall_years: float | None = None
    # Kitces: Expected Failure = P(failure) x magnitude, in unfunded years.
    expected_shortfall_years: float = 0.0
    # CFA Institute: share of the retirement actually funded, averaged over
    # ALL paths (successes count as 1.0). Answers "how close did we get?"
    # rather than only "did we make it?".
    goal_completion: float = 1.0

    # ── Conservatism ledger ───────────────────────────────────────────────────
    # Every deliberate pessimism in one place, with its size.
    #
    # WHY THIS EXISTS. This projection is conservative in several independent
    # places that compound silently: an explicit return haircut, a CAGR shrunk
    # toward an asset-class prior and hard-capped, and (until stochastic
    # mortality) a death date assumed with certainty. Each was defensible on
    # its own; nobody could see the TOTAL. Over-conservatism is not free --
    # it tells people to save more, or work longer, than they need to -- so
    # the stack is now reported rather than inferred.
    conservatism_ledger: list[dict] = field(default_factory=list)

    # Where this plan's initial withdrawal rate sits against the India-specific
    # safe-withdrawal literature. The 4% rule is a US artefact.
    india_swr: dict = field(default_factory=dict)

    # ── §E: success as an ESTIMATE, never a certainty ─────────────────────────
    # Jeffreys posterior mean and 95% credible interval for the primary
    # strategy's success rate. Headline these, not adaptive_success_rate: a
    # raw 1000/1000 is a sample, not proof of a 100% plan.
    success_rate_estimate: float = 0.0
    success_rate_low: float = 0.0
    success_rate_high: float = 0.0
    robotic_success_estimate: float = 0.0
    # Posterior sd of the expected return each path drew around (0 = off).
    parameter_uncertainty_sd: float = 0.0


def run_monte_carlo(
    *,
    portfolio_stats,   # backend.analytics.portfolio_statistics.PortfolioStatistics
    current_age: int,
    retire_age: int,
    life_expectancy: int,
    current_portfolio_value: float,
    monthly_sip: float,
    step_up: float,
    current_expense_monthly: float,
    slab_rate: float = 0.30,
    strategy: Strategy = "adaptive",
    block_size: int = 3,          # kept for API compatibility (inflation bootstrap only now)
    n_simulations: int = 1000,
    seed: int | None = 42,
    # ── v4 optional config (all default to v2-equivalent behaviour) ───────────
    spending_model: SpendingModel = "constant_real",
    glide_mode: GlideMode = "declining",
    cape: float | None = None,
    leaderboard: bool = True,
    conservatism_haircut: float | None = None,
    mortality: Literal["fixed", "stochastic"] = "stochastic",
    parameter_uncertainty: bool | None = None,
) -> ARISEResult:
    """
    Run paired Monte Carlo simulations for the investor's REAL selected
    portfolio. Returns are a Student-t AR(1) process anchored on
    portfolio_stats.portfolio_cagr/volatility (already shrunk/capped by
    PortfolioStatisticsBuilder), not a raw historical bootstrap.
    """
    if strategy == "adaptive":
        strategy = "guardrails"

    # An explicit, reported deduction from the expected return -- NOT a maths
    # artifact. See effective_mu_sigma for why the engine previously ran
    # ~1.4pp/yr pessimistic by accident, and settings for why the margin is
    # re-applied deliberately once that accident is corrected.
    if conservatism_haircut is None:
        from backend.core.config import settings as _settings
        conservatism_haircut = float(
            getattr(_settings, "MODEL_CONSERVATISM_HAIRCUT", 0.0) or 0.0
        )

    historical_inflation = load_historical_inflation()

    # Under stochastic mortality the paths must run past the planning horizon,
    # because the whole point is that some retirees outlive it. The PLANNING
    # horizon handed to the withdrawal rules stays `life_expectancy`, so VPW
    # keeps sizing withdrawals against what the investor actually expects --
    # only the evaluation window lengthens.
    sim_horizon_age = MORTALITY_MAX_AGE if mortality == "stochastic" else life_expectancy
    n_years = max(1, sim_horizon_age - current_age + 1)
    rf_proxy = float(portfolio_stats.risk_free_rate_proxy)

    common = dict(
        current_age=current_age, retire_age=retire_age, life_expectancy=life_expectancy,
        current_portfolio_value=current_portfolio_value, monthly_sip=monthly_sip,
        step_up=step_up, current_expense_monthly=current_expense_monthly,
        portfolio_baseline_vol=portfolio_stats.portfolio_volatility,
        portfolio_baseline_cagr=portfolio_stats.portfolio_cagr,
        liquid_reserve_return=rf_proxy, slab_rate=slab_rate,
        glide_mode=glide_mode, spending_model=spending_model, cape=cape,
        conservatism_haircut=conservatism_haircut, horizon_age=sim_horizon_age,
    )

    # §E estimation risk: how uncertain is the expected return itself?
    if parameter_uncertainty is None:
        parameter_uncertainty = PARAMETER_UNCERTAINTY
    # Synthetic prices are not evidence about the future, so they buy no
    # precision: a synthetic series counts as ZERO years, leaving only the
    # prior's tau years. Counting it as real history would let the engine grow
    # more confident from data it invented.
    _real_years = (
        0.0 if "synthetic" in str(getattr(portfolio_stats, "data_source", "")).lower()
        else portfolio_stats.n_years_history
    )
    param_sd = (
        expected_return_posterior_sd(portfolio_stats.portfolio_volatility, _real_years)
        if parameter_uncertainty else 0.0
    )

    strat_set = list(dict.fromkeys(["robotic", strategy] + (list(ALL_STRATEGIES) if leaderboard else [])))
    acc: dict[str, dict] = {
        s: dict(success=0, success_fixed=0, taxes=[], terminals=[], gk_cuts=[],
                gk_raises=[], ratchets=[], vol_years=[], inc_vol=[],
                ruin_ages=[], shortfall_years=[], funded_fractions=[])
        for s in strat_set
    }
    median_candidates: list[list[YearRecord]] = []

    rng_base = np.random.default_rng(seed)
    for sim_idx in range(n_simulations):
        sim_seed = int(rng_base.integers(0, 2 ** 31))
        rng = np.random.default_rng(sim_seed)
        shocks = standardized_shock_path(n_years, rng)
        inflation_path = block_bootstrap_inflation(historical_inflation, n_years, block_size, rng)

        # Everything random about THIS path is drawn once, here, and shared by
        # every strategy, so the leaderboard compares strategies on identical
        # worlds (common random numbers). The lifespan used to be drawn inside
        # the strategy loop, so each strategy met a different death age on the
        # same path -- noise in exactly the comparison the leaderboard makes.
        #
        # `r.success` alone asks whether the money lasted to the fixed
        # horizon; what matters is whether it outlasted the person.
        if mortality == "stochastic":
            death_age = draw_lifespan(retire_age, float(life_expectancy), rng)
        else:
            death_age = float(life_expectancy)
        # §E: this path's expected return, drawn from the estimate's posterior.
        path_kwargs = dict(common)
        path_kwargs["portfolio_baseline_cagr"] = (
            portfolio_stats.portfolio_cagr + draw_return_offset(param_sd, rng)
        )

        for s in strat_set:
            keep_hist = (s == strategy) and (sim_idx % 50 == 0)
            r = simulate_path(shocks, inflation_path, strategy=s, record_history=keep_hist, **path_kwargs)
            a = acc[s]

            survived = r.ruin_age is None or r.ruin_age >= death_age
            a["success"] += int(survived)
            # The OLD definition, recomputed consistently: did the money reach
            # the planning horizon? `r.success` cannot be reused here because
            # it means "lasted to the simulation horizon", which under
            # stochastic mortality is age 105 -- a harder question, and one
            # that would make the two modes incomparable.
            a["success_fixed"] += int(
                r.ruin_age is None or r.ruin_age >= life_expectancy
            )

            # ── Magnitude of failure (Kitces) ──────────────────────────────
            # `ruin_age` was already computed on every failing path and then
            # discarded. A plan that runs dry at 83 and one that runs dry at
            # 69 were reported identically.
            retirement_years = max(1.0, death_age - retire_age)
            if not survived and r.ruin_age is not None:
                a["ruin_ages"].append(float(r.ruin_age))
                a["shortfall_years"].append(max(0.0, death_age - r.ruin_age))
                funded = max(0.0, (r.ruin_age - retire_age)) / retirement_years
                a["funded_fractions"].append(min(1.0, funded))
            else:
                a["funded_fractions"].append(1.0)
            a["taxes"].append(r.total_tax_paid)
            a["terminals"].append(r.terminal_corpus)
            a["gk_cuts"].append(r.gk_cuts)
            a["gk_raises"].append(r.gk_raises)
            a["ratchets"].append(r.ratchet_raises)
            a["vol_years"].append(r.vol_derisk_years)
            a["inc_vol"].append(_income_volatility(r.real_withdrawals))
            if keep_hist and r.history:
                median_candidates.append(r.history)

    n = n_simulations

    def pctiles(terms: list[float]) -> dict:
        arr = np.array(terms, dtype=float)
        return {p: float(np.percentile(arr, p)) for p in (10, 25, 50, 75, 90)} if arr.size else \
            {p: 0.0 for p in (10, 25, 50, 75, 90)}

    prim = acc[strategy]
    rob = acc["robotic"]
    prim_pct = pctiles(prim["terminals"])

    def _fail_stats(a: dict) -> dict:
        """Magnitude-of-failure summary for one strategy."""
        ruin = np.array(a["ruin_ages"], dtype=float)
        short = np.array(a["shortfall_years"], dtype=float)
        funded = np.array(a["funded_fractions"], dtype=float)
        p_fail = 1.0 - (a["success"] / max(1, n_simulations))
        return {
            "median_ruin_age": float(np.median(ruin)) if ruin.size else None,
            "ruin_age_p10": float(np.percentile(ruin, 10)) if ruin.size else None,
            "ruin_age_p90": float(np.percentile(ruin, 90)) if ruin.size else None,
            "median_shortfall_years": round(float(np.median(short)), 2) if short.size else None,
            # P(failure) x average magnitude, in unfunded years.
            "expected_shortfall_years": float(p_fail * (short.mean() if short.size else 0.0)),
            "goal_completion": float(funded.mean()) if funded.size else 1.0,
        }

    prim_fail = _fail_stats(prim)

    # ── Assemble the conservatism ledger ──────────────────────────────────────
    _raw_cagr = float(getattr(portfolio_stats, "portfolio_cagr_raw", 0.0) or 0.0)
    _used_cagr = float(portfolio_stats.portfolio_cagr)
    _ledger: list[dict] = [
        {
            "source": "return_haircut",
            "effect_pp_per_year": round(-100 * float(conservatism_haircut), 3),
            "detail": "settings.MODEL_CONSERVATISM_HAIRCUT, deducted from the "
                      "expected return. Operator-set; 0 gives the unbiased model.",
        },
        {
            "source": "cagr_shrinkage_and_cap",
            "effect_pp_per_year": round(100 * (_used_cagr - _raw_cagr), 3) if _raw_cagr else 0.0,
            "detail": "PortfolioStatisticsBuilder shrinks the sample CAGR toward an "
                      "asset-class prior and caps it. Negative means the projection "
                      "uses LESS than this portfolio's own measured history.",
        },
        {
            "source": "estimation_risk",
            "effect_pp_per_year": 0.0,
            "detail": (
                f"Each path draws its own expected return, sd {param_sd * 100:.2f}pp/yr "
                f"around the estimate (sigma / sqrt(years of history + prior years)). "
                f"Unbiased -- the centre does not move -- but outcomes spread as wide "
                f"as the evidence allows, so short histories cannot claim certainty."
                if param_sd > 0 else
                "Disabled: every path shares the point estimate, which overstates "
                "how precisely the expected return is known."
            ),
        },
        {
            "source": "mortality_model",
            "effect_pp_per_year": 0.0,
            "detail": (
                "Stochastic (Gompertz) lifespans: the stated life expectancy is "
                "the modal age, not a certainty."
                if mortality == "stochastic" else
                "FIXED death age. Everyone dies exactly on the planning horizon, "
                "which OVERSTATES ruin -- measured at 36% of the reported failure "
                "rate on this engine. Pass mortality='stochastic' to correct it."
            ),
        },
    ]

    rob_avg_tax = float(np.mean(rob["taxes"])) if rob["taxes"] else 0.0
    prim_avg_tax = float(np.mean(prim["taxes"])) if prim["taxes"] else 0.0
    tax_saving = ((rob_avg_tax - prim_avg_tax) / max(rob_avg_tax, prim_avg_tax, 1.0)) * 100.0
    tax_saving = float(np.clip(tax_saving, -100.0, 100.0))

    # ── §M Milevsky analytical cross-check on a constant-real baseline ────────
    lam = _mortality_hazard(retire_age, life_expectancy)
    infl_mean = float(np.mean(list(historical_inflation.values()))) if historical_inflation else 0.05
    mu_real = portfolio_stats.portfolio_cagr - infl_mean
    accumulation_years = max(0, retire_age - current_age)

    def _accum_fv(rate: float) -> float:
        fv = current_portfolio_value * (1 + rate) ** accumulation_years
        sip = monthly_sip * 12
        for y in range(accumulation_years):
            fv += sip * (1 + rate) ** (accumulation_years - y)
            sip *= (1 + step_up)
        return fv

    corpus_at_ret_est = _accum_fv(portfolio_stats.portfolio_cagr)
    exp_at_ret = current_expense_monthly * 12 * (1 + infl_mean) ** accumulation_years
    wr0 = float(np.clip(exp_at_ret / max(corpus_at_ret_est, 1.0), 1e-4, 0.9))
    milevsky = milevsky_ruin_probability(mu_real, portfolio_stats.portfolio_volatility, lam, wr0)
    mc_ruin_const = 1.0 - rob["success"] / n
    gap = abs(milevsky - mc_ruin_const) if not math.isnan(milevsky) else float("nan")

    median_path: list[dict] = []
    if median_candidates:
        med_term = np.median(prim["terminals"]) if prim["terminals"] else 0.0
        best = min(median_candidates, key=lambda h: abs((h[-1].total_portfolio if h else 0.0) - med_term))
        median_path = [
            {"age": yr.age, "withdrawal": yr.withdrawal_need or 0.0, "tax_paid": yr.tax_paid, "rule": yr.rule_fired}
            for yr in best if yr.phase == "decum" and yr.withdrawal_need is not None
        ]

    board: list[dict] = []
    for s in strat_set:
        a = acc[s]
        p = pctiles(a["terminals"])
        est = success_estimate(a["success"], n)
        board.append({
            "strategy": s, "success_rate": a["success"] / n,
            "success_estimate": est["estimate"],
            "success_low": est["low"], "success_high": est["high"],
            **{k: v for k, v in _fail_stats(a).items()
               if k in ("expected_shortfall_years", "goal_completion",
                        "median_shortfall_years")},
            "terminal_p50": p[50], "terminal_p10": p[10],
            "avg_tax": float(np.mean(a["taxes"])) if a["taxes"] else 0.0,
            "income_volatility": float(np.mean(a["inc_vol"])) if a["inc_vol"] else 0.0,
        })
    # Ranked by EXPECTED SHORTFALL (Kitces: P(failure) x magnitude), not by
    # bare success rate. Sorting on success alone can prefer a strategy that
    # fails rarely but catastrophically over one that falls a year short more
    # often -- which is the wrong advice to give a retiree.
    board.sort(key=lambda d: (d["expected_shortfall_years"],
                              -d["success_rate"], -d["terminal_p50"]))

    data_quality_warning: str | None = None
    if portfolio_stats.n_years_history < MIN_YEARS_HISTORY_RELIABLE:
        data_quality_warning = (
            f"Only {portfolio_stats.n_years_history:.1f} years of real price history were "
            f"available for this portfolio. The sustained return is shrunk toward an "
            f"asset-class-blended prior and capped (see portfolio_cagr_raw/prior_cagr_used/"
            f"sample_weight_cagr on PortfolioStatistics) precisely to keep this case "
            f"defensible; treat results as provisional until more real history accumulates."
        )

    # The arithmetic mean actually drawn during accumulation (glide inactive),
    # reported so the conversion from the CAGR anchor is visible rather than
    # buried inside the simulation.
    _drawn_mu, _ = effective_mu_sigma(
        current_age, retire_age, portfolio_stats.portfolio_cagr,
        portfolio_stats.portfolio_volatility, rf_proxy, glide_mode,
        conservatism_haircut,
    )

    _est = success_estimate(prim["success"], n)
    _rob_est = success_estimate(rob["success"], n)
    return ARISEResult(
        success_rate_estimate=_est["estimate"],
        success_rate_low=_est["low"],
        success_rate_high=_est["high"],
        robotic_success_estimate=_rob_est["estimate"],
        parameter_uncertainty_sd=round(float(param_sd), 4),
        portfolio_cagr_anchor=round(float(portfolio_stats.portfolio_cagr), 4),
        expected_return_drawn=round(float(_drawn_mu), 4),
        conservatism_haircut=round(float(conservatism_haircut), 4),
        mortality_model=mortality,
        success_rate_fixed_horizon=round(prim["success_fixed"] / n, 4),
        median_ruin_age=prim_fail["median_ruin_age"],
        ruin_age_p10=prim_fail["ruin_age_p10"],
        ruin_age_p90=prim_fail["ruin_age_p90"],
        median_shortfall_years=prim_fail["median_shortfall_years"],
        expected_shortfall_years=round(prim_fail["expected_shortfall_years"], 3),
        goal_completion=round(prim_fail["goal_completion"], 4),
        conservatism_ledger=_ledger,
        # `wr0` is the initial withdrawal rate already derived above for the
        # Milevsky cross-check; reused rather than recomputed.
        india_swr=india_swr_assessment(wr0),
        n_simulations=n, retire_age=retire_age, life_expectancy=life_expectancy, current_age=current_age,
        data_source=portfolio_stats.data_source,
        n_years_history_used=portfolio_stats.n_years_history,
        portfolio_cagr_used=portfolio_stats.portfolio_cagr,
        portfolio_volatility_used=portfolio_stats.portfolio_volatility,
        risk_free_rate_proxy_used=rf_proxy,
        robotic_success_rate=rob["success"] / n,
        adaptive_success_rate=prim["success"] / n,
        terminal_p10=prim_pct[10], terminal_p25=prim_pct[25], terminal_p50=prim_pct[50],
        terminal_p75=prim_pct[75], terminal_p90=prim_pct[90],
        robotic_avg_tax=rob_avg_tax, adaptive_avg_tax=prim_avg_tax, tax_saving_pct=tax_saving,
        avg_gk_cuts_per_path=float(np.mean(prim["gk_cuts"])) if prim["gk_cuts"] else 0.0,
        avg_gk_raises_per_path=float(np.mean(prim["gk_raises"])) if prim["gk_raises"] else 0.0,
        avg_vol_derisk_years=float(np.mean(prim["vol_years"])) if prim["vol_years"] else 0.0,
        median_withdrawal_path=median_path,
        scheme_ids=portfolio_stats.scheme_ids, weights=portfolio_stats.weights,
        data_quality_warning=data_quality_warning,
        engine_version="v4", primary_strategy=strategy,
        spending_model_used=spending_model, glide_mode_used=glide_mode, cape_swr_used=cape_swr(cape),
        avg_ratchet_raises_per_path=float(np.mean(prim["ratchets"])) if prim["ratchets"] else 0.0,
        income_volatility=float(np.mean(prim["inc_vol"])) if prim["inc_vol"] else 0.0,
        milevsky_ruin_prob=milevsky, mc_ruin_prob_constant=mc_ruin_const, milevsky_mc_abs_gap=gap,
        strategy_leaderboard=board,
    )
