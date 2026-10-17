"""Deep Profile routes (Phase 1)."""
from __future__ import annotations

from fastapi import APIRouter, status

from backend.core.deps import CurrentUser, DbSession
from backend.core.errors import NotFoundError
from backend.schemas.profile import ProfileCreate, ProfileResponse, ProfileUpdate
from backend.services import profile_service

router = APIRouter(prefix="/profile", tags=["profile"])


def _serialize(p) -> ProfileResponse:
    # age / years_to_retirement / is_complete are model properties computed at
    # read time, never stored -- an age column would be wrong by the next birthday.
    return ProfileResponse(
        id=p.id,
        user_id=p.user_id,
        full_name=p.full_name,
        date_of_birth=p.date_of_birth,
        occupation=p.occupation,
        monthly_income=p.monthly_income,
        total_corpus=p.total_corpus,
        investment_amount=p.investment_amount,
        goal=p.goal,
        risk_appetite=p.risk_appetite,
        target_retirement_age=p.target_retirement_age,
        monthly_expenses_in_retirement=p.monthly_expenses_in_retirement,
        monthly_sip=p.monthly_sip,
        sip_step_up_pct=p.sip_step_up_pct,
        age=p.age,
        years_to_retirement=p.years_to_retirement,
        is_complete=p.is_complete,
    )


@router.post("", response_model=ProfileResponse, status_code=status.HTTP_201_CREATED)
async def create_profile(
    payload: ProfileCreate, user: CurrentUser, db: DbSession
) -> ProfileResponse:
    """Submit the complete 10-field Deep Profile. Idempotent upsert."""
    profile = await profile_service.create_or_replace_profile(db, user, payload)
    return _serialize(profile)


@router.get("", response_model=ProfileResponse)
async def read_profile(user: CurrentUser, db: DbSession) -> ProfileResponse:
    profile = await profile_service.get_profile(db, user)
    if profile is None:
        raise NotFoundError("No profile found. Create one via POST /api/v1/profile.")
    return _serialize(profile)


@router.put("", response_model=ProfileResponse)
async def update_profile(
    payload: ProfileUpdate, user: CurrentUser, db: DbSession
) -> ProfileResponse:
    """Partial update -- only the fields present in the body are changed."""
    profile = await profile_service.update_profile(db, user, payload)
    return _serialize(profile)
