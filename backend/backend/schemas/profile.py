"""
InvestorProfile Pydantic schemas.

Design:
  - ProfileCreate:  all fields required (complete profile submission)
  - ProfileUpdate:  all fields optional (partial update / PATCH semantics via PUT)
  - ProfileResponse: what the API returns, including computed fields (age, years_to_retirement)
"""
from __future__ import annotations

import uuid
from datetime import date
from enum import Enum

from pydantic import BaseModel, Field, field_validator, model_validator


# ── Enums ──────────────────────────────────────────────────────────────────────
class InvestmentGoal(str, Enum):
    GROWTH = "growth"
    INCOME = "income"
    SAFETY = "safety"
    TAX_SAVING = "tax_saving"


class RiskAppetite(str, Enum):
    CONSERVATIVE = "conservative"
    MODERATE = "moderate"
    MODERATELY_HIGH = "moderately_high"
    AGGRESSIVE = "aggressive"


# ── Base (shared validators) ───────────────────────────────────────────────────
class ProfileBase(BaseModel):
    full_name: str | None = Field(default=None, min_length=1, max_length=255)
    date_of_birth: date | None = None
    occupation: str | None = Field(default=None, min_length=1, max_length=255)

    monthly_income: int | None = Field(default=None, gt=0, description="Gross monthly income in INR")
    total_corpus: int | None = Field(default=None, ge=0, description="Total current savings in INR")
    investment_amount: int | None = Field(default=None, gt=0, description="Amount to invest now in INR")

    goal: InvestmentGoal | None = None
    risk_appetite: RiskAppetite | None = None

    target_retirement_age: int | None = Field(default=None, ge=40, le=80)
    monthly_expenses_in_retirement: int | None = Field(default=None, gt=0)

    # ── Additive, OPTIONAL. ProfileCreate's documented 10-field contract
    # (CLAUDE.md §6) is unchanged: a client that never sends these keeps working.
    # But when supplied, ARISE projects on the investor's REAL SIP instead of a
    # 20%-of-income estimate. ──────────────────────────────────────────────────
    monthly_sip: int | None = Field(
        default=None, ge=0,
        description="Ongoing monthly SIP in INR. ARISE uses this instead of estimating.",
    )
    sip_step_up_pct: float | None = Field(
        default=None, ge=0.0, le=0.50,
        description="Annual SIP escalation as a fraction (0.10 = 10%/yr).",
    )

    @field_validator("date_of_birth")
    @classmethod
    def validate_dob(cls, v: date | None) -> date | None:
        if v is None:
            return v
        today = date.today()
        age = today.year - v.year - ((today.month, today.day) < (v.month, v.day))
        if age < 18:
            raise ValueError("Investor must be at least 18 years old.")
        if age > 100:
            raise ValueError("Date of birth appears invalid (age > 100).")
        return v


# ── Create: all fields required ────────────────────────────────────────────────
class ProfileCreate(ProfileBase):
    full_name: str = Field(min_length=1, max_length=255)
    date_of_birth: date
    occupation: str = Field(min_length=1, max_length=255)
    monthly_income: int = Field(gt=0)
    total_corpus: int = Field(ge=0)
    investment_amount: int = Field(gt=0)
    goal: InvestmentGoal
    risk_appetite: RiskAppetite
    target_retirement_age: int = Field(ge=40, le=80)
    monthly_expenses_in_retirement: int = Field(gt=0)

    @model_validator(mode="after")
    def validate_investment_vs_corpus(self) -> ProfileCreate:
        if self.investment_amount > self.total_corpus:
            raise ValueError(
                "investment_amount cannot exceed total_corpus. "
                "You cannot invest more than you have."
            )
        return self

    @model_validator(mode="after")
    def validate_retirement_age_vs_dob(self) -> ProfileCreate:
        today = date.today()
        age = today.year - self.date_of_birth.year - (
            (today.month, today.day) < (self.date_of_birth.month, self.date_of_birth.day)
        )
        if self.target_retirement_age <= age:
            raise ValueError(
                f"target_retirement_age ({self.target_retirement_age}) must be "
                f"greater than current age ({age})."
            )
        return self

    @model_validator(mode="after")
    def validate_expenses_vs_income(self) -> ProfileCreate:
        # Sanity check: retirement expenses shouldn't exceed 5x current income
        # (flags obvious data entry errors)
        if self.monthly_expenses_in_retirement > self.monthly_income * 5:
            raise ValueError(
                "monthly_expenses_in_retirement seems unreasonably high "
                "relative to monthly_income. Please double-check."
            )
        return self

    @model_validator(mode="after")
    def validate_sip_vs_income(self) -> ProfileCreate:
        # You cannot SIP more than you earn. This catches a units slip -- 5,00,000
        # typed where 50,000 was meant -- which would otherwise sail through and
        # hand ARISE a wildly optimistic accumulation path.
        if self.monthly_sip is not None and self.monthly_sip > self.monthly_income:
            raise ValueError(
                f"monthly_sip (Rs {self.monthly_sip:,}) cannot exceed "
                f"monthly_income (Rs {self.monthly_income:,})."
            )
        return self


# ── Update: all fields optional ────────────────────────────────────────────────
class ProfileUpdate(ProfileBase):
    """
    Same as ProfileCreate but nothing is required.
    Only provided fields are applied (service layer merges with existing).
    """
    pass


# ── Response: what the API sends back ─────────────────────────────────────────
class ProfileResponse(BaseModel):
    id: uuid.UUID
    user_id: uuid.UUID

    full_name: str | None
    date_of_birth: date | None
    occupation: str | None

    monthly_income: int | None
    total_corpus: int | None
    investment_amount: int | None

    goal: str | None
    risk_appetite: str | None

    target_retirement_age: int | None
    monthly_expenses_in_retirement: int | None

    monthly_sip: int | None = None
    sip_step_up_pct: float | None = None

    # Computed fields (derived at read-time, never stored)
    age: int | None = None
    years_to_retirement: int | None = None

    is_complete: bool

    model_config = {"from_attributes": True}
