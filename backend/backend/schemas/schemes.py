"""
Pydantic schemas for the scheme filtering and selection API.
"""
from __future__ import annotations

import uuid

from pydantic import BaseModel, Field, field_validator


class SchemeInfoResponse(BaseModel):
    """Mirrors engines.filter_engine.SchemeInfo for API serialization."""

    id: str
    name: str
    category: str | None
    instrument_type: str
    risk_rating: str | None
    risk_score_1_to_6: int | None
    riskometer: str | None
    aum_cr: float | None
    nav_regular_growth: float | None
    expense_ratio_regular: float | None
    min_investment: int | None
    suitable_for: list[str] = Field(default_factory=list)
    benchmark: str | None = None
    holdings_count: int = 0
    subtier: str | None = None
    risk_flag: str | None = None
    risk_flag_level: str = "match"

    # ── Additive: live Upstox tradability facts, when available ────────────────
    # None when Upstox isn't configured or the scheme has no verified mapping --
    # the platform must keep working with zero Upstox setup.
    upstox_purchasable: bool | None = None
    upstox_min_investment: float | None = None

    model_config = {"from_attributes": True}


class FilteredOutResponse(BaseModel):
    """Mirrors engines.filter_engine.FilteredOut for API serialization."""

    scheme_id: str
    scheme_name: str
    reasons: list[str] = Field(default_factory=list)

    model_config = {"from_attributes": True}


class FilterResultResponse(BaseModel):
    """Full response body for GET /schemes/filtered."""

    eligible_count: int
    total_schemes: int
    eligible_schemes: list[SchemeInfoResponse]
    filtered_out: list[FilteredOutResponse]
    filter_summary: dict[str, int]


class SelectSchemesRequest(BaseModel):
    """Request body for POST /schemes/select.

    weights is optional — if omitted, the schemes are equal-weighted by
    default. Investors should call POST /schemes/allocate afterward (or
    supply weights here directly) to set a real allocation before running
    ARISE, which requires explicit weights rather than assuming equal split.
    """

    selected_scheme_ids: list[str] = Field(min_length=1, max_length=20)
    weights: dict[str, float] | None = None

    @field_validator("selected_scheme_ids")
    @classmethod
    def dedupe_and_validate(cls, v: list[str]) -> list[str]:
        if len(v) != len(set(v)):
            raise ValueError("selected_scheme_ids must not contain duplicates.")
        if any(not sid.strip() for sid in v):
            raise ValueError("selected_scheme_ids must not contain empty strings.")
        return v


class AllocateWeightsRequest(BaseModel):
    """
    Request body for POST /schemes/allocate — the "Allocate Portfolio
    Percentage" step. Weights must cover exactly the currently selected
    schemes and sum to 1.0 (100%).
    """

    weights: dict[str, float] = Field(min_length=1)

    @field_validator("weights")
    @classmethod
    def validate_weights_sum(cls, v: dict[str, float]) -> dict[str, float]:
        total = sum(v.values())
        if not (0.995 <= total <= 1.005):
            raise ValueError(
                f"Portfolio weights must sum to 1.0 (100%), got {total:.4f}."
            )
        if any(w < 0 for w in v.values()):
            raise ValueError("Portfolio weights must not be negative.")
        return v


class SelectSchemesResponse(BaseModel):
    """Response body for POST /schemes/select."""

    selected_count: int
    schemes: list[SchemeInfoResponse]
    weights: dict[str, float]
    message: str


class SelectedSchemesResponse(BaseModel):
    """Response body for GET /schemes/selected."""

    id: uuid.UUID
    user_id: uuid.UUID
    selected_scheme_ids: list[str]
    weights: dict[str, float]
    schemes: list[SchemeInfoResponse]

    model_config = {"from_attributes": True}


class SchemeDetailResponse(BaseModel):
    """
    Full single-scheme detail for the per-scheme dashboard view -- everything
    SchemeInfoResponse has, plus the rich factsheet fields (sector allocation,
    trailing SIP returns, fund manager, philosophy, load structure) that exist
    in schemes_metadata.json but were never exposed through any endpoint until
    this one. Read-only, unauthenticated-safe: nothing here is investor-specific.
    """

    id: str
    name: str
    category: str | None
    instrument_type: str
    risk_rating: str | None
    risk_score_1_to_6: int | None
    riskometer: str | None
    benchmark_riskometer: str | None = None
    aum_cr: float | None
    aum_monthly_avg_cr: float | None = None
    nav_regular_growth: float | None
    nav_direct_growth: float | None = None
    nav_as_of: str | None = None
    expense_ratio_regular: float | None
    expense_ratio_direct: float | None = None
    min_investment: int | None
    max_investment: int | None = None
    min_age: int | None = None
    max_age: int | None = None
    min_monthly_income: int | None = None
    suitable_for: list[str] = Field(default_factory=list)
    benchmark: str | None = None
    holdings_count: int = 0
    subtier: str | None = None

    fund_manager: str | None = None
    investment_philosophy: str | None = None
    inception_date: str | None = None
    data_vintage: str | None = None
    type_description: str | None = None
    product_label: str | None = None
    entry_load: str | None = None
    exit_load: str | None = None
    load_structure: str | None = None
    portfolio_turnover: float | None = None

    std_deviation: float | None = None
    beta: float | None = None
    sharpe_ratio: float | None = None

    # Debt-specific (None for equity schemes).
    ytm: str | None = None
    modified_duration: str | None = None
    macaulay_duration: str | None = None
    avg_maturity: str | None = None

    sector_allocation: list[dict] = Field(default_factory=list)
    sip_returns: dict = Field(default_factory=dict)
    trailing_returns: dict = Field(default_factory=dict)

    top_holdings: list[dict] = Field(default_factory=list)

    # Live/Upstox tradability, when available -- absent (None) rather than a
    # fabricated value when Upstox isn't configured.
    upstox_purchasable: bool | None = None
    upstox_min_investment: float | None = None

    model_config = {"from_attributes": True}
