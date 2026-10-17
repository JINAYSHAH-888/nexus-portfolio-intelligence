"""
Shared FastAPI dependencies.

The three-stage gate that CLAUDE.md §6 specifies for every Phase-3 analysis
endpoint -- "valid JWT + completed profile + at least one scheme selected" --
is expressed here once, as composable dependencies, rather than being
re-implemented (and eventually mis-implemented) in each route.

    get_current_user   -> 401 if the JWT is missing/invalid/revoked
    get_current_profile-> 409 if the Deep Profile is absent or incomplete
    get_selection      -> 404 "Scheme selection not found" if nothing selected
"""
from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import Depends
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.core.errors import (
    AuthenticationError,
    PermissionError_,
    ProfileIncompleteError,
    SelectionNotFoundError,
)
from backend.core.security import TokenError, decode_token
from backend.infrastructure import redis_client
from backend.infrastructure.db import get_db
from backend.models import InvestorProfile, SchemeSelection, User

# auto_error=False so a missing header raises our own AuthenticationError
# (consistent JSON body) instead of FastAPI's bare 403.
bearer_scheme = HTTPBearer(auto_error=False)

DbSession = Annotated[AsyncSession, Depends(get_db)]


async def get_current_user(
    db: DbSession,
    credentials: Annotated[
        HTTPAuthorizationCredentials | None, Depends(bearer_scheme)
    ] = None,
) -> User:
    if credentials is None or not credentials.credentials:
        raise AuthenticationError("Missing bearer token.")

    try:
        payload = decode_token(credentials.credentials, expected_type="access")
    except TokenError as e:
        raise AuthenticationError(str(e)) from e

    # Access tokens are short-lived (30 min) and NOT tracked in Postgres, so
    # this denylist check is the only way logout-all can cut an access token
    # short. If Redis is down it returns False -- meaning the token stays valid
    # until it expires on its own. That is the deliberate trade: availability
    # over a <=30-minute revocation window.
    if await redis_client.is_jti_revoked(payload["jti"]):
        raise AuthenticationError("Token has been revoked.")

    try:
        user_id = uuid.UUID(payload["sub"])
    except (ValueError, TypeError) as e:
        raise AuthenticationError("Malformed token subject.") from e

    user = await db.get(User, user_id)
    if user is None:
        raise AuthenticationError("User no longer exists.")
    if not user.is_active:
        raise AuthenticationError("This account has been deactivated.")
    return user


CurrentUser = Annotated[User, Depends(get_current_user)]


async def get_current_profile(db: DbSession, user: CurrentUser) -> InvestorProfile:
    """
    Phase-1 gate. Raises 409 with the exact list of missing fields, so the UI
    can jump the user straight back to the fields it still needs rather than
    just saying "incomplete".
    """
    result = await db.execute(
        select(InvestorProfile).where(InvestorProfile.user_id == user.id)
    )
    profile = result.scalar_one_or_none()
    if profile is None:
        raise ProfileIncompleteError(
            "No Deep Profile found. Create one via POST /api/v1/profile.",
            missing_fields=list(InvestorProfile.REQUIRED_FIELDS),
        )
    if not profile.is_complete:
        missing = [
            f for f in InvestorProfile.REQUIRED_FIELDS if getattr(profile, f) is None
        ]
        raise ProfileIncompleteError(missing_fields=missing)
    return profile


CurrentProfile = Annotated[InvestorProfile, Depends(get_current_profile)]


async def get_selection(db: DbSession, user: CurrentUser) -> SchemeSelection:
    """
    Phase-2 gate for analysis endpoints.

    The 404 message is pinned by CLAUDE.md §6 ("Scheme selection not found").
    """
    result = await db.execute(
        select(SchemeSelection).where(SchemeSelection.user_id == user.id)
    )
    selection = result.scalar_one_or_none()
    if selection is None or not selection.selected_scheme_ids:
        raise SelectionNotFoundError()
    return selection


CurrentSelection = Annotated[SchemeSelection, Depends(get_selection)]


def require_role(*roles: str):
    """Route guard for RM/admin-only endpoints."""

    async def _guard(user: CurrentUser) -> User:
        if user.role not in roles:
            raise PermissionError_(
                f"This endpoint requires one of these roles: {', '.join(roles)}."
            )
        return user

    return _guard
