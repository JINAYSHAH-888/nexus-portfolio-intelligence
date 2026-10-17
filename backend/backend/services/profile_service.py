"""Deep Profile (Phase 1) CRUD."""
from __future__ import annotations

import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.core.errors import NotFoundError, ValidationError
from backend.models import InvestorProfile, User
from backend.schemas.profile import ProfileCreate, ProfileUpdate

logger = logging.getLogger(__name__)


async def get_profile(db: AsyncSession, user: User) -> InvestorProfile | None:
    result = await db.execute(
        select(InvestorProfile).where(InvestorProfile.user_id == user.id)
    )
    return result.scalar_one_or_none()


async def create_or_replace_profile(
    db: AsyncSession, user: User, payload: ProfileCreate
) -> InvestorProfile:
    """
    POST /profile is an upsert.

    A user has exactly one profile (UNIQUE on user_id), and re-submitting the
    full 10-field form is the natural way to correct it. Returning 409 here
    instead would force the client to know whether it is creating or editing --
    for a single-row-per-user resource that's friction with no benefit.
    """
    profile = await get_profile(db, user)
    data = payload.model_dump()

    # Enums -> their string values, matching the varchar columns and the
    # FilterEngine's expected literals ("growth", "moderate", ...).
    data["goal"] = payload.goal.value
    data["risk_appetite"] = payload.risk_appetite.value

    if profile is None:
        profile = InvestorProfile(user_id=user.id, **data)
        db.add(profile)
    else:
        for k, v in data.items():
            setattr(profile, k, v)

    await db.commit()
    await db.refresh(profile)
    return profile


async def update_profile(
    db: AsyncSession, user: User, payload: ProfileUpdate
) -> InvestorProfile:
    """
    PUT /profile -- partial update. Only fields explicitly present in the
    request body are touched (`exclude_unset`), so sending {"monthly_income":
    300000} does not null out the other nine fields.
    """
    profile = await get_profile(db, user)
    if profile is None:
        raise NotFoundError(
            "No profile to update. Create one first via POST /api/v1/profile."
        )

    data = payload.model_dump(exclude_unset=True)
    if not data:
        raise ValidationError("No fields supplied to update.")

    for k, v in data.items():
        if k in ("goal", "risk_appetite") and v is not None and hasattr(v, "value"):
            v = v.value
        setattr(profile, k, v)

    # ProfileCreate enforces these across the full 10-field form, but a partial
    # PUT could otherwise slip an inconsistent pair past validation (e.g.
    # raising investment_amount above an unchanged total_corpus). Re-check the
    # cross-field invariants against the MERGED record.
    _validate_invariants(profile)

    await db.commit()
    await db.refresh(profile)
    return profile


def _validate_invariants(p: InvestorProfile) -> None:
    if (
        p.investment_amount is not None
        and p.total_corpus is not None
        and p.investment_amount > p.total_corpus
    ):
        raise ValidationError(
            f"investment_amount (Rs {p.investment_amount:,}) cannot exceed "
            f"total_corpus (Rs {p.total_corpus:,})."
        )

    if (
        p.monthly_sip is not None
        and p.monthly_income is not None
        and p.monthly_sip > p.monthly_income
    ):
        raise ValidationError(
            f"monthly_sip (Rs {p.monthly_sip:,}) cannot exceed "
            f"monthly_income (Rs {p.monthly_income:,})."
        )

    age = p.age
    if (
        p.target_retirement_age is not None
        and age is not None
        and p.target_retirement_age <= age
    ):
        raise ValidationError(
            f"target_retirement_age ({p.target_retirement_age}) must be greater "
            f"than your current age ({age})."
        )
