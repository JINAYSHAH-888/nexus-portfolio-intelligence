"""Auth routes: register, login, refresh, logout, logout-all, me."""
from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, status
from fastapi.security import OAuth2PasswordRequestForm

from backend.core.deps import CurrentUser, DbSession
from backend.schemas.auth import (
    LogoutRequest,
    MeResponse,
    RefreshRequest,
    RegisterRequest,
    RegisterResponse,
    TokenPairResponse,
)
from backend.services import auth_service

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/register", response_model=RegisterResponse, status_code=status.HTTP_201_CREATED)
async def register(payload: RegisterRequest, db: DbSession) -> RegisterResponse:
    user, access, refresh = await auth_service.register(
        db,
        email=payload.email,
        password=payload.password,
        full_name=payload.full_name,
    )
    return RegisterResponse(
        user_id=user.id,
        email=user.email,
        access_token=access,
        refresh_token=refresh,
    )


@router.post("/login", response_model=TokenPairResponse)
async def login(
    db: DbSession,
    form: Annotated[OAuth2PasswordRequestForm, Depends()],
) -> TokenPairResponse:
    """
    OAuth2 password flow, so Swagger's Authorize button and standard OAuth2
    clients work unmodified. The spec names the field `username`; we carry the
    investor's email in it.
    """
    _user, access, refresh = await auth_service.login(
        db, email=form.username, password=form.password
    )
    return TokenPairResponse(access_token=access, refresh_token=refresh)


@router.post("/refresh", response_model=TokenPairResponse)
async def refresh(payload: RefreshRequest, db: DbSession) -> TokenPairResponse:
    """Rotates the refresh token. The presented token is burned on success."""
    _user, access, new_refresh = await auth_service.refresh_tokens(
        db, refresh_token=payload.refresh_token
    )
    return TokenPairResponse(access_token=access, refresh_token=new_refresh)


@router.post("/logout", status_code=status.HTTP_200_OK)
async def logout(payload: LogoutRequest, db: DbSession) -> dict[str, str]:
    await auth_service.logout(db, refresh_token=payload.refresh_token)
    return {"message": "Logged out."}


@router.post("/logout-all", status_code=status.HTTP_200_OK)
async def logout_all(user: CurrentUser, db: DbSession) -> dict[str, object]:
    revoked = await auth_service.logout_all(db, user=user)
    return {"message": "All sessions ended.", "sessions_revoked": revoked}


@router.get("/me", response_model=MeResponse)
async def me(user: CurrentUser) -> MeResponse:
    return MeResponse(
        user_id=user.id,
        email=user.email,
        role=user.role,
        is_active=user.is_active,
    )
