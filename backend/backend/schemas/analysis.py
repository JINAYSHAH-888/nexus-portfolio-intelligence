"""Pydantic schemas for Phase 3 analysis endpoints."""
from __future__ import annotations

import math
from typing import Literal

from pydantic import BaseModel, Field, field_validator


class OverlapEdgeResponse(BaseModel):
    source_id: str
    target_id: str
    jaccard_score: float
    weighted_overlap: float
    shared_stocks: int
    shared_stock_names: list[str] = []


class SchemeNodeResponse(BaseModel):
    id: str
    name: str
    category: str | None
    instrument_type: str
    aum_cr: float | None
    holdings_count: int


class HeatmapCellResponse(BaseModel):
    row_scheme_id: str
    col_scheme_id: str
    jaccard_score: float
    weighted_overlap: float


class OverlapResponse(BaseModel):
    scheme_ids: list[str]
    nodes: list[SchemeNodeResponse]
    edges: list[OverlapEdgeResponse]
    heatmap: list[HeatmapCellResponse]
    overlap_threshold: float
    avg_pairwise_jaccard: float
    most_overlapping_pair: list[str] | None
    least_overlapping_pair: list[str] | None
    scheme_overlap_summary: list[dict]


class LayerScoreResponse(BaseModel):
    layer_id: str
    description: str
    raw_score: float
    weight: float
    contribution: float
    data_source: str


class PairPLOSResponse(BaseModel):
    scheme_a_id: str
    scheme_b_id: str
    scheme_a_name: str
    scheme_b_name: str
    plos_score: float
    real_correlation: float | None
    layers: list[LayerScoreResponse]
    interpretation: str
    data_quality: str


class PLOSResponse(BaseModel):
    scheme_ids: list[str]
    pair_scores: list[PairPLOSResponse]
    portfolio_plos: float
    diversification_score: float
    interpretation: str
    top_5_recommendation: list[dict]
    most_overlapping_pair: list[str] | None
    least_overlapping_pair: list[str] | None
    data_source: str


class ARISERequest(BaseModel):
    n_simulations: int = Field(default=1000, ge=100, le=5000)
    life_expectancy: int = Field(default=85, ge=70, le=100)
    slab_rate: float = Field(default=0.30, ge=0.0, le=0.42)
    strategy: str = Field(default="adaptive", pattern="^(robotic|adaptive|vpw)$")
    step_up: float = Field(default=0.08, ge=0.0, le=0.30)

    # ── Additive, all optional; omitting them reproduces the documented
    # 5-field contract in CLAUDE.md §6 exactly. ────────────────────────────────
    monthly_sip: int | None = Field(
        default=None,
        ge=0,
        description=(
            "Ongoing monthly SIP in INR. The Deep Profile does not capture this, "
            "so when omitted the server derives a clearly-labelled estimate "
            "(20% of gross monthly income) and reports it in `assumptions`."
        ),
    )
    spending_model: Literal["constant_real", "blanchett_smile"] = "constant_real"
    glide_mode: Literal["declining", "rising", "static"] = "declining"
    cape: float | None = Field(
        default=None,
        gt=0,
        description=(
            "Optional CAPE valuation anchor (ERN/Kitces). Off unless supplied -- "
            "when None, no valuation adjustment is applied."
        ),
    )


class ARISEResponse(BaseModel):
    n_simulations: int
    retire_age: int
    life_expectancy: int
    current_age: int

    # Real-data provenance — every number here traces back to the
    # investor's actual selected schemes, never a hardcoded assumption
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

    median_withdrawal_path: list[dict]
    scheme_ids: list[str]
    weights: dict[str, float]

    # ── v4 fields (ARISEResult already emits these; the response model was
    # dropping them silently, so nothing after §5's "verified over real HTTP"
    # actually reached the client). All optional -> additive, non-breaking. ────
    data_quality_warning: str | None = None
    engine_version: str = "v4"
    primary_strategy: str = "guardrails"
    spending_model_used: str = "constant_real"
    glide_mode_used: str = "declining"
    cape_swr_used: float | None = None
    avg_ratchet_raises_per_path: float = 0.0
    income_volatility: float = 0.0

    # Milevsky's closed-form ruin probability is an INDEPENDENT cross-check, not
    # the headline number. CLAUDE.md §5/§10: it is expected to diverge from the
    # Monte Carlo whenever there is a long accumulation phase, because the
    # closed form assumes a fixed corpus at retirement with no accumulation
    # sequence risk. Reported as a diagnostic; never surfaced as the answer.
    milevsky_ruin_prob: float | None = None
    mc_ruin_prob_constant: float | None = None
    milevsky_mc_abs_gap: float | None = None

    strategy_leaderboard: list[dict] = Field(default_factory=list)

    # ── Return-space transparency ─────────────────────────────────────────────
    # `portfolio_cagr_anchor` is the compound rate the portfolio statistics
    # supplied. `expected_return_drawn` is the ARITHMETIC mean actually drawn
    # each year, which is necessarily higher because the simulation generates
    # volatility drag itself -- feeding the CAGR straight in (as this engine
    # used to) applies that drag twice and makes every projection ~1.4pp/yr
    # too pessimistic. `conservatism_haircut` is the deliberate margin then
    # subtracted. Exposed so the projection can be audited rather than taken
    # on trust.
    portfolio_cagr_anchor: float = 0.0
    expected_return_drawn: float = 0.0
    conservatism_haircut: float = 0.0

    # ── Longevity and magnitude of failure ────────────────────────────────────
    # `success_rate` alone cannot distinguish a plan that falls one year short
    # from one that falls fifteen short, and a fixed death age overstates ruin
    # (measured: 36% of the reported failure rate on this engine).
    mortality_model: str = "fixed"
    success_rate_fixed_horizon: float = 0.0
    median_ruin_age: float | None = None
    ruin_age_p10: float | None = None
    ruin_age_p90: float | None = None
    median_shortfall_years: float | None = None
    expected_shortfall_years: float = 0.0     # Kitces: P(fail) x magnitude
    goal_completion: float = 1.0              # CFA: share of retirement funded
    # Every deliberate pessimism, with its size, in one auditable place.
    conservatism_ledger: list[dict] = Field(default_factory=list)
    # Initial withdrawal rate placed against India-specific SWR research
    # (Saraogi 2022; Raju & Saraogi 2024). The US 4% rule does not transfer.
    india_swr: dict = Field(default_factory=dict)
    # Success as an ESTIMATE (Jeffreys posterior mean + 95% credible
    # interval), never a raw 1000/1000 certainty. Declared explicitly: an
    # undeclared engine field is silently dropped by this response model.
    success_rate_estimate: float = 0.0
    success_rate_low: float = 0.0
    success_rate_high: float = 0.0
    robotic_success_estimate: float = 0.0
    # sd of each path's expected-return draw around the estimate (estimation
    # risk). 0 means every path assumed the estimate was exact.
    parameter_uncertainty_sd: float = 0.0
    # Direct vs regular plan: the one lever that needs no market view and
    # whose payoff is certain. Added by the service layer.
    plan_cost_comparison: dict = Field(default_factory=dict)

    # Provenance added by the service layer (not by the engine).
    assumptions: dict = Field(default_factory=dict)
    shrinkage_diagnostics: dict = Field(default_factory=dict)

    @field_validator(
        "milevsky_ruin_prob",
        "mc_ruin_prob_constant",
        "milevsky_mc_abs_gap",
        mode="before",
    )
    @classmethod
    def _nan_to_none(cls, v: float | None) -> float | None:
        # ARISEResult defaults these to float("nan"). NaN is not valid JSON --
        # it serialises to a bare `NaN` token that strict parsers (and every
        # browser's JSON.parse) reject. Emit null instead.
        if v is None:
            return None
        f = float(v)
        return None if math.isnan(f) or math.isinf(f) else f


class PortfolioMetricsResponse(BaseModel):
    scheme_id: str
    scheme_name: str
    annualized_return: float
    annualized_volatility: float
    sharpe_ratio: float
    max_drawdown: float
    beta_vs_nifty: float | None
    var_95_parametric: float
    cvar_95_parametric: float
    var_95_historical: float
    cvar_95_historical: float
    var_99_parametric: float
    cvar_99_parametric: float
    mc_p10_return: float
    mc_p25_return: float
    mc_p50_return: float
    mc_p75_return: float
    mc_p90_return: float
    mc_ruin_probability: float
    data_points: int
    is_synthetic: bool

    # ── Added in the risk-engine upgrade. All optional with defaults, so an
    # older client that ignores them keeps working unchanged. ──────────────
    sortino_ratio: float = 0.0
    calmar_ratio: float = 0.0
    downside_deviation: float = 0.0
    ulcer_index: float = 0.0
    alpha_vs_nifty: float | None = None
    r_squared_vs_nifty: float | None = None
    tracking_error: float | None = None
    information_ratio: float | None = None       # SEBI-mandated for equity schemes
    up_capture: float | None = None
    down_capture: float | None = None
    var_95_cornish_fisher: float = 0.0
    var_99_cornish_fisher: float = 0.0
    var_backtest_95: dict = Field(default_factory=dict)
    rolling_stability: dict = Field(default_factory=dict)
    arithmetic_return: float = 0.0               # mean of one year (simulation input)
    geometric_return: float = 0.0                # the CAGR actually compounded
    data_source: str = "unknown"
    # Which benchmark the beta/alpha/TE/IR above were measured against.
    benchmark_declared: str = ""
    benchmark_used: str | None = None
    benchmark_quality: str = "none"              # exact|price_proxy|close_proxy|none
    benchmark_note: str = ""
    # Return-based closet-indexing signal (ESMA 2020 method) + fee-per-unit-of
    # -active-risk. See analytics/activeness.py.
    activeness: dict = Field(default_factory=dict)
    expense_ratio_direct: float | None = None
    expense_ratio_regular: float | None = None


class RiskResponse(BaseModel):
    scheme_ids: list[str]
    scheme_metrics: list[PortfolioMetricsResponse]
    portfolio_annualized_return: float
    portfolio_volatility: float
    portfolio_sharpe: float
    portfolio_max_drawdown: float
    portfolio_var_95: float
    portfolio_cvar_95: float
    correlation_matrix: list[list[float]]
    correlation_labels: list[str]
    covariance_matrix: list[list[float]]
    weighted_avg_vol: float
    diversification_ratio: float
    data_source: str
    n_observations: int

    # ── Which portfolio these numbers describe ─────────────────────────────
    weights: dict[str, float] = Field(default_factory=dict)
    weighting_scheme: str = "equal"              # "investor" | "equal"
    portfolio_benchmark: str | None = None
    # Risk-balanced reference weights (HRP). Explicitly not a recommendation;
    # the payload carries its own caveats.
    suggested_allocation: dict = Field(default_factory=dict)

    portfolio_sortino: float = 0.0
    portfolio_calmar: float = 0.0
    portfolio_beta: float | None = None
    portfolio_alpha: float | None = None
    portfolio_tracking_error: float | None = None
    portfolio_information_ratio: float | None = None
    portfolio_var_95_cornish_fisher: float = 0.0
    portfolio_var_backtest_95: dict = Field(default_factory=dict)

    # The conservatism margin, reported rather than folded into the headline.
    expected_return_unbiased: float = 0.0
    conservatism_haircut: float = 0.0
    expected_return_after_haircut: float = 0.0


class CompareRequest(BaseModel):
    """
    Request body for POST /analysis/compare -- "what if my portfolio were
    this instead?" without touching the investor's actually-saved selection.
    Weights optional (equal-weighted if omitted), same as /schemes/select.
    """

    scheme_ids: list[str] = Field(min_length=1, max_length=20)
    weights: dict[str, float] | None = None

    @field_validator("scheme_ids")
    @classmethod
    def dedupe_and_validate(cls, v: list[str]) -> list[str]:
        if len(v) != len(set(v)):
            raise ValueError("scheme_ids must not contain duplicates.")
        if any(not sid.strip() for sid in v):
            raise ValueError("scheme_ids must not contain empty strings.")
        return v


class ComparisonBlock(BaseModel):
    scheme_ids: list[str]
    weights: dict[str, float]
    overlap: OverlapResponse
    plos: PLOSResponse


class CompareResponse(BaseModel):
    """
    `candidate` is always the hypothetical combination from the request.
    `current` is the investor's REAL saved selection, included automatically
    when one exists, so the frontend can render a direct before/after diff
    (avg_pairwise_jaccard, portfolio_plos, diversification_score) in a single
    response rather than two separate calls the client has to reconcile
    itself. `current` is null if nothing is saved yet.
    """

    candidate: ComparisonBlock
    current: ComparisonBlock | None = None
