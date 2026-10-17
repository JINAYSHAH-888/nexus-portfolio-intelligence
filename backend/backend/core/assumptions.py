"""
THE ASSUMPTIONS REGISTRY.

Every number in this system comes from exactly one of four places. This module
names them, so nobody has to grep six files to find out what the model assumes.

  LAW        A statutory fact. Hardcoded is CORRECT -- an API cannot tell you
             what the LTCG rate is. Changes only when the Finance Act changes.
  MARKET     Fetched live (yfinance / AMFI / Alpha Vantage) or derived from real
             disclosed factsheet data. Never invented.
  MODEL      A modelling choice (Student-t df, shrinkage strength, glide slope).
             Hardcoded is unavoidable -- there is no API for "degrees of freedom".
             But it must be VISIBLE and reviewable, not buried.
  ESTIMATE   A stand-in for data we don't have. The dangerous category. Every one
             of these must be labelled in the API response.

Exposed at GET /api/v1/meta/assumptions so a reviewer, an auditor, or a
compliance officer can read the entire assumption set of the product in one
request, rather than trusting a README.

THE RISK-FREE RATE (the reason this module exists)
--------------------------------------------------
Before this, the product used TWO different risk-free rates simultaneously:

  * risk_engine / returns_engine: a hardcoded RISK_FREE_RATE = 0.065, described
    as an "RBI repo rate proxy". The repo rate has since moved; the constant did
    not. Every Sharpe ratio on the risk dashboard was computed against a stale
    number.
  * portfolio_statistics.get_risk_free_rate_proxy(): the realised CAGR of the
    lowest-volatility scheme in the investor's selection. That is not a
    risk-free rate at all -- it is a realised return, and it can be arbitrarily
    large. Measured in this repo it produced 10.05% and, on one selection,
    21.58%. ARISE feeds that value straight into `liquid_reserve_return`, so the
    "safe" bucket of the retirement plan was compounding at up to 21% a year,
    materially inflating success rates.

`resolve_risk_free_rate()` below replaces both with a single, sane source:
the OVERNIGHT FUND'S OWN DISCLOSED YTM from the real factsheet (5.39%). That is
an actual, sourced, near-riskless yield the investor could genuinely access --
which is exactly what a risk-free proxy is supposed to be. It is bounded to a
plausible band so no future data glitch can ever make it 21% again, and an
operator can override it explicitly via settings.RISK_FREE_RATE.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from functools import lru_cache
from typing import Any, Literal

from backend.core.config import settings


def _benchmark_coverage() -> int:
    """Schemes with a usable benchmark proxy. Computed, not asserted, so the
    number cannot drift away from the registry."""
    try:
        from backend.data_loaders.benchmark_registry import coverage

        return int(coverage(settings.SCHEMES_PATH)["usable"])
    except Exception:  # pragma: no cover - provenance must never break a request
        return 0

logger = logging.getLogger(__name__)

Provenance = Literal["law", "market", "model", "estimate"]

# A risk-free rate outside this band is a data error, not a market event. India's
# overnight/repo rate has not left it in modern history. Clamping here is what
# stops a bad NAV series or a synthetic draw from silently handing ARISE a 21%
# "risk-free" return.
RF_MIN, RF_MAX = 0.02, 0.10
RF_FALLBACK = 0.065  # last resort only; logged loudly when used

# The scheme whose disclosed YTM defines the risk-free proxy, best first.
RF_SOURCE_SCHEMES = ("nippon-overnight-fund", "nippon-liquid-fund")


@dataclass(frozen=True)
class Assumption:
    key: str
    value: Any
    unit: str
    provenance: Provenance
    source: str
    rationale: str


@lru_cache(maxsize=1)
def _schemes() -> list[dict]:
    with open(settings.SCHEMES_PATH, encoding="utf-8") as f:
        return json.load(f)


@lru_cache(maxsize=1)
def resolve_risk_free_rate() -> float:
    """
    The single risk-free rate for the whole product.

    1. settings.RISK_FREE_RATE, if an operator set one explicitly.
    2. The overnight fund's real disclosed YTM (then the liquid fund's).
    3. RF_FALLBACK, logged as an error -- this should never happen in a
       correctly-loaded deployment.

    Always clamped to [RF_MIN, RF_MAX].
    """
    override = getattr(settings, "RISK_FREE_RATE", None)
    if override is not None:
        return _clamp(float(override), "settings.RISK_FREE_RATE")

    by_id = {s["id"]: s for s in _schemes()}
    for sid in RF_SOURCE_SCHEMES:
        scheme = by_id.get(sid)
        ytm = (scheme or {}).get("ytm_decimal")
        if ytm is not None:
            return _clamp(float(ytm), f"{sid}.ytm_decimal")

    logger.error(
        "No disclosed YTM found on %s -- falling back to a hardcoded %.3f "
        "risk-free rate. Run scripts/normalize_scheme_metadata.py.",
        RF_SOURCE_SCHEMES,
        RF_FALLBACK,
    )
    return RF_FALLBACK


def _clamp(rate: float, source: str) -> float:
    if not (RF_MIN <= rate <= RF_MAX):
        clamped = min(max(rate, RF_MIN), RF_MAX)
        logger.warning(
            "Risk-free rate from %s was %.4f, outside the plausible band "
            "[%.2f, %.2f]. Clamped to %.4f. A 'risk-free' rate far outside this "
            "band is a data error, and left unclamped it would inflate ARISE's "
            "liquid-reserve compounding.",
            source,
            rate,
            RF_MIN,
            RF_MAX,
            clamped,
        )
        return clamped
    return rate


def risk_free_rate_source() -> str:
    if getattr(settings, "RISK_FREE_RATE", None) is not None:
        return "settings.RISK_FREE_RATE (operator override)"
    by_id = {s["id"]: s for s in _schemes()}
    for sid in RF_SOURCE_SCHEMES:
        if (by_id.get(sid) or {}).get("ytm_decimal") is not None:
            return f"{sid} disclosed YTM (real factsheet data)"
    return "hardcoded fallback (DEGRADED -- schemes metadata is not normalised)"


def registry() -> list[Assumption]:
    """Every assumption in the system, in one list."""
    from backend.analytics import activeness as _activeness
    from backend.analytics import debt_returns_model as dm
    from backend.analytics import portfolio_accounting as pa
    from backend.analytics import portfolio_statistics as ps
    from backend.engines import arise_engine as ar
    from backend.engines import filter_engine as fe
    from backend.services import analysis_service as asvc

    rf = resolve_risk_free_rate()

    return [
        # ── MARKET ────────────────────────────────────────────────────────────
        Assumption(
            "risk_free_rate", round(rf, 4), "decimal/yr", "market",
            risk_free_rate_source(),
            "Single source of truth for Sharpe/Sortino and ARISE's liquid-reserve "
            "return. Taken from the overnight fund's own disclosed YTM -- a real, "
            f"near-riskless yield -- and clamped to [{RF_MIN}, {RF_MAX}].",
        ),
        Assumption(
            "stock_prices", "yfinance (NSE, delayed)", "-", "market",
            "yfinance -> parquet cache",
            "Live. No API key required.",
        ),
        Assumption(
            "scheme_nav_history", "AMFI via mfapi.in", "-", "market",
            "amfi_loader (name-validated)",
            "Live. Falls back to the factsheet-disclosed NAV, clearly labelled stale.",
        ),
        Assumption(
            "fundamentals_pe_roe_de", "Alpha Vantage -> yfinance", "-", "market",
            "alpha_vantage_loader",
            "Live. Optional API key; degrades to yfinance.",
        ),
        Assumption(
            "inflation_path", "historical India CPI", "decimal/yr", "market",
            "data/historical_cpi_india.json (real published CPI)",
            "Block-bootstrapped by ARISE. Real published data, not a flat assumption.",
        ),
        Assumption(
            "scheme_metadata", "35 real Nippon factsheets", "-", "market",
            "data/schemes_metadata.json (June 2026 vintage)",
            "AUM, NAV, expense ratio, std_deviation, beta, duration, YTM, sector "
            "allocation, holdings. Real disclosed data -- but a SNAPSHOT: refreshed "
            "only by re-running the factsheet pipeline, not by an API.",
        ),

        # ── LAW ───────────────────────────────────────────────────────────────
        Assumption(
            "equity_ltcg_rate", float(pa.EQUITY_LTCG_RATE), "decimal", "law",
            "Finance Act 2024, w.e.f. 23 Jul 2024",
            "Statutory. An API cannot tell you the LTCG rate.",
        ),
        Assumption(
            "equity_ltcg_exemption", float(pa.EQUITY_LTCG_EXEMPTION), "INR/yr", "law",
            "Finance Act 2024", "Annual AGGREGATE, not per-scheme.",
        ),
        Assumption(
            "equity_stcg_rate", float(pa.EQUITY_STCG_RATE), "decimal", "law",
            "Finance Act 2024", "Raised from 15% in Jul 2024.",
        ),
        Assumption(
            "specified_mf_cutoff", pa.SPECIFIED_MF_CUTOFF.isoformat(), "date", "law",
            "s.50AA, Finance Act 2023",
            "Debt units acquired on/after this date are ALWAYS slab-rate.",
        ),

        # ── MODEL ─────────────────────────────────────────────────────────────
        Assumption(
            "arise_return_t_df", ar.RETURN_T_DF, "df", "model",
            "arise_engine.RETURN_T_DF",
            "Student-t degrees of freedom. Fat tails; 5 is the standard choice for "
            "equity returns. There is no API for this.",
        ),
        Assumption(
            "arise_ar1_phi", ar.RETURN_AR1_PHI, "coefficient", "model",
            "arise_engine.RETURN_AR1_PHI",
            "Mild mean reversion (negative = anti-persistence). The damping that "
            "stops a single lucky draw compounding undamped for 25 years.",
        ),
        Assumption(
            "arise_liquid_reserve_years", ar.LIQUID_RESERVE_YEARS, "years", "model",
            "arise_engine.LIQUID_RESERVE_YEARS",
            "Size of the liquid bucket in the two-bucket decumulation strategy.",
        ),
        # ── Added with the benchmark / fee / longevity / allocation work ──────
        Assumption(
            "benchmark_coverage", _benchmark_coverage(), "schemes", "model",
            "data_loaders/benchmark_registry.py",
            "How many of the 35 schemes can be measured against their OWN declared "
            "benchmark. Previously ALL of them were measured against Nifty 50, which "
            "only 6 actually declare. The rest are now withheld rather than computed "
            "against an unrelated index: CRISIL/NIFTY debt indices have no free "
            "source, and the equity proxies available are PRICE indices standing in "
            "for TOTAL RETURN benchmarks, which overstates alpha by roughly the "
            "dividend yield. Each scheme reports which proxy it got and how good it is.",
        ),
        Assumption(
            "expense_ratio_netting", "direct plan TER, gross series only", "-", "model",
            "data_loaders/market_data.net_of_expenses",
            "Expense ratios were present for 33 of 35 schemes and read by nothing, so "
            "projections were gross of fees. They are now deducted -- but ONLY from "
            "holdings-reconstructed series. A NAV-derived series is already net "
            "(NAV is struck after expenses), so deducting again would double-count by "
            "up to 2.1%/yr. The DIRECT-plan figure is used because the platform cannot "
            "know which plan an investor holds and direct is the smaller deduction.",
        ),
        Assumption(
            "gompertz_dispersion", ar.GOMPERTZ_DISPERSION, "years", "model",
            "arise_engine.GOMPERTZ_DISPERSION",
            "Spread of the lifespan distribution. The MODAL age is not hardcoded -- it "
            "is the investor's own stated life expectancy, so their number becomes the "
            "most likely age of death rather than a certainty. Only the dispersion is "
            "assumed, from Blanchett's fit to the SOA 2000 Annuity Table (~10.65 male, "
            "~8.88 female) and Milevsky-Young (10.5). Indian IALM 2012-14 tables would "
            "be better but are published only as PDF appendices.",
        ),
        Assumption(
            "mortality_max_age", ar.MORTALITY_MAX_AGE, "years", "model",
            "arise_engine.MORTALITY_MAX_AGE",
            "Simulation horizon under stochastic mortality. Paths must run past the "
            "planning horizon because the entire point is that some retirees outlive "
            "it. A FIXED death age overstates ruin -- measured at 36% of the reported "
            "failure rate on this engine.",
        ),
        Assumption(
            "india_swr_band", [ar.INDIA_SWR_LOWER, ar.INDIA_SWR_UPPER],
            "decimal/yr", "model", "arise_engine.india_swr_assessment",
            "India-specific safe withdrawal band (Raju & Saraogi 2024). The US 4% rule "
            "does not transfer: Saraogi (2022) found 4% depleted Indian portfolios "
            "early in nearly a third of scenarios. Reported as CONTEXT only -- ARISE's "
            "own simulation of the actual portfolio is the stronger evidence. Caveat: "
            "a small literature from essentially one research group.",
        ),
        Assumption(
            "closet_index_screens", [_activeness.TE_STRICT, _activeness.TE_LOOSE, _activeness.R2_HIGH],
            "decimal", "model", "analytics/activeness.py",
            "Return-based closet-indexing screens (tracking error 3%/4%, R-squared "
            "0.95), the return-based half of ESMA's 2016 criteria. Return-based "
            "because true Active Share needs benchmark CONSTITUENT weights this "
            "platform lacks -- which is also why ESMA's own 2020 paper and France's "
            "AMF use return-based detection. A SIGNAL, not a verdict: ESMA required "
            "persistence over 3 of 5 years, and EFAMA argues fixed thresholds "
            "penalise some fund types unfairly.",
        ),
        Assumption(
            "allocation_method", "hierarchical_risk_parity", "-", "model",
            "analytics/hrp.py",
            "Lopez de Prado (2016). Chosen over mean-variance because it never INVERTS "
            "the covariance matrix: with schemes correlated 0.85-0.95 over ~3 years, "
            "that matrix is near-singular and inversion amplifies estimation noise "
            "into extreme, unstable weights. A risk-balanced REFERENCE with no view on "
            "expected returns, taxes or goals -- explicitly not a recommendation.",
        ),
        Assumption(
            "model_conservatism_haircut",
            float(getattr(settings, "MODEL_CONSERVATISM_HAIRCUT", 0.0) or 0.0),
            "decimal/yr", "model", "settings.MODEL_CONSERVATISM_HAIRCUT",
            "A DELIBERATE deduction from the expected return, applied by both the "
            "risk engine's forward simulation and ARISE. It is not a maths "
            "correction: both engines previously ran ~sigma^2/2 pessimistic by "
            "ACCIDENT (the risk engine applied an Ito correction to a drift that "
            "was already logarithmic; ARISE fed a geometric CAGR into a process "
            "that adds its own volatility drag -- the 'double-correction' Kitces, "
            "Boldin and Voyant all describe for retirement Monte Carlo). Those "
            "are fixed, so removing the accidental pessimism would have made "
            "every projection rosier overnight; this re-applies the margin "
            "explicitly instead. Responses report the return before and after "
            "it. Set to 0 for the statistically unbiased model.",
        ),
        Assumption(
            "mc_student_t_df", 5.0, "df", "model", "risk_engine.MC_T_DF",
            "Degrees of freedom for the Student-t shocks in the risk engine's "
            "1-year forward simulation. Previously Gaussian, which understates "
            "the left tail of a negatively-skewed, fat-tailed asset class in "
            "exactly the place a risk model exists to measure. Matches "
            "arise_engine.RETURN_T_DF so the dashboard and the retirement "
            "projection no longer assume different tail behaviour for the same "
            "portfolio. Variance is preserved, so only the tails change.",
        ),
        Assumption(
            "return_shrinkage_tau_years", ps.RETURN_SHRINKAGE_TAU_YEARS, "years", "model",
            "portfolio_statistics.RETURN_SHRINKAGE_TAU_YEARS",
            "Sample gets weight n/(n+tau). More real history => more trust in the "
            "sample; thin/synthetic history => mostly the prior.",
        ),
        Assumption(
            "return_cap_abs_ceiling", ps.RETURN_CAP_ABS_CEILING, "decimal/yr", "model",
            "portfolio_statistics.RETURN_CAP_ABS_CEILING",
            "Hard ceiling on the projected portfolio CAGR regardless of evidence.",
        ),
        Assumption(
            "equity_prior_mu_sigma", [ps._EQUITY_PRIOR_MU, ps._EQUITY_PRIOR_SIGMA],
            "decimal/yr", "model", "portfolio_statistics",
            "Asset-class prior the sample CAGR is shrunk toward when a scheme's own "
            "real metadata is unavailable.",
        ),
        Assumption(
            "debt_prior_mu_sigma", [ps._DEBT_PRIOR_MU, ps._DEBT_PRIOR_SIGMA],
            "decimal/yr", "model", "portfolio_statistics", "As above, for debt.",
        ),
        Assumption(
            "debt_assumed_yield_vol", dm.ASSUMED_ANNUAL_YIELD_VOL, "decimal/yr", "model",
            "debt_returns_model.ASSUMED_ANNUAL_YIELD_VOL",
            "~120bps typical annual yield swing. Multiplied by each scheme's OWN real "
            "modified duration to get its volatility -- so the duration is real data, "
            "and only the yield-vol scalar is assumed.",
        ),
        Assumption(
            "equity_subtier_vol_bands", [fe.CORE_VOL_MAX, fe.GROWTH_VOL_MAX], "%", "model",
            "filter_engine",
            "Core <=16%, Growth 16-18%, Aggressive >18%, applied to each scheme's OWN "
            "real disclosed std_deviation.",
        ),
        Assumption(
            "plos_layer_weights", "10 layers, sum=1.0", "weights", "model",
            "plos_engine.LAYER_WEIGHTS",
            "Relative importance of each overlap signal. A judgement call, exposed here.",
        ),

        # ── ESTIMATE (the dangerous category) ─────────────────────────────────
        Assumption(
            "default_sip_rate_of_income", asvc.DEFAULT_SIP_RATE_OF_INCOME, "decimal",
            "estimate", "analysis_service.DEFAULT_SIP_RATE_OF_INCOME",
            "ONLY used when the profile has no monthly_sip. Always flagged in the "
            "ARISE response as monthly_sip_is_estimated=true. Set profile.monthly_sip "
            "to eliminate it entirely.",
        ),
        Assumption(
            "synthetic_regime_params", "NSE 2003-2024 calibration", "-", "estimate",
            "market_data.REAL_CALIBRATED_PARAMS",
            "LAST-RESORT tier only, when every real source is unreachable. Any scheme "
            "using it is flagged is_synthetic=true in the response.",
        ),
    ]


def as_dict() -> dict[str, Any]:
    items = registry()
    by_provenance: dict[str, list[dict]] = {}
    for a in items:
        by_provenance.setdefault(a.provenance, []).append(
            {
                "key": a.key,
                "value": a.value,
                "unit": a.unit,
                "source": a.source,
                "rationale": a.rationale,
            }
        )
    return {
        "note": (
            "Market data is fetched live; every MODEL parameter is necessarily "
            "hardcoded (there is no API for 'Student-t degrees of freedom'). This "
            "endpoint exists so that every such choice is visible and reviewable "
            "rather than buried in the source."
        ),
        "provenance_key": {
            "law": "Statutory fact. Hardcoded is correct.",
            "market": "Fetched live, or derived from real disclosed factsheet data.",
            "model": "A modelling choice. Hardcoded is unavoidable; it must be visible.",
            "estimate": "A stand-in for data we do not have. Always flagged in responses.",
        },
        "counts": {k: len(v) for k, v in by_provenance.items()},
        "assumptions": by_provenance,
    }
