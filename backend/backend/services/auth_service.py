"""
Authentication: register, login, refresh (with rotation), logout, logout-all.

The interesting part is refresh-token handling. A JWT can't be un-issued, so
every refresh token's `jti` is recorded in Postgres and checked on use. That
buys three things:

  1. /auth/logout genuinely ends a session (revoke that one jti).
  2. /auth/logout-all ends every session (revoke all of the user's jtis) --
     the "someone stole my laptop" button.
  3. Rotation + reuse detection: refreshing burns the old token and issues a
     new one. If a token that was ALREADY used comes back, that means two
     parties hold it -- i.e. it leaked -- so we revoke the user's entire token
     family rather than quietly issuing another one. This is the standard
     OAuth 2.0 BCP response to refresh-token replay.
"""
from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from backend.core.errors import AuthenticationError, ConflictError
from backend.core.security import (
    TokenError,
    create_access_token,
    create_refresh_token,
    decode_token,
    hash_password,
    verify_password,
)
from backend.infrastructure import redis_client
from backend.models import RefreshToken, User

logger = logging.getLogger(__name__)


async def _issue_refresh(db: AsyncSession, user: User) -> str:
    token, jti, expires_at = create_refresh_token(user.id)
    db.add(RefreshToken(user_id=user.id, jti=jti, expires_at=expires_at))
    return token


async def register(
    db: AsyncSession, *, email: str, password: str, full_name: str
) -> tuple[User, str, str]:
    """Create a user and issue an initial token pair."""
    email = email.strip().lower()

    user = User(
        email=email,
        hashed_password=hash_password(password),
        full_name=full_name.strip(),
        role="investor",
        is_active=True,
    )
    db.add(user)
    try:
        # Flush (not commit) so the UNIQUE violation surfaces here and we can
        # translate it into a clean 409. Relying on a pre-SELECT check instead
        # would still race under concurrent signups; the DB constraint is the
        # only real guard.
        await db.flush()
    except IntegrityError as e:
        await db.rollback()
        raise ConflictError(f"An account with email '{email}' already exists.") from e

    refresh = await _issue_refresh(db, user)
    access = create_access_token(user.id, user.role)
    await db.commit()
    await db.refresh(user)

    logger.info("Registered user %s", user.id)
    return user, access, refresh


async def authenticate(db: AsyncSession, *, email: str, password: str) -> User:
    email = email.strip().lower()
    result = await db.execute(select(User).where(User.email == email))
    user = result.scalar_one_or_none()

    # Same error message and (roughly) the same work whether the account is
    # missing or the password is wrong -- otherwise the endpoint becomes an
    # account-enumeration oracle. We run a verify against a dummy hash so a
    # missing user doesn't return measurably faster than a bad password.
    if user is None:
        verify_password(password, "$2b$12$" + "x" * 53)
        raise AuthenticationError("Incorrect email or password.")

    if not verify_password(password, user.hashed_password):
        raise AuthenticationError("Incorrect email or password.")

    if not user.is_active:
        raise AuthenticationError("This account has been deactivated.")

    return user


async def login(db: AsyncSession, *, email: str, password: str) -> tuple[User, str, str]:
    user = await authenticate(db, email=email, password=password)
    refresh = await _issue_refresh(db, user)
    access = create_access_token(user.id, user.role)
    await db.commit()
    return user, access, refresh


async def _revoke_all_for_user(db: AsyncSession, user_id: uuid.UUID) -> int:
    result = await db.execute(
        update(RefreshToken)
        .where(RefreshToken.user_id == user_id, RefreshToken.revoked_at.is_(None))
        .values(revoked_at=datetime.now(UTC))
    )
    return result.rowcount or 0


async def refresh_tokens(db: AsyncSession, *, refresh_token: str) -> tuple[User, str, str]:
    """
    Rotate a refresh token: validate, revoke the presented one, issue a new pair.
    """
    try:
        payload = decode_token(refresh_token, expected_type="refresh")
    except TokenError as e:
        raise AuthenticationError(str(e)) from e

    jti = payload["jti"]
    user_id = uuid.UUID(payload["sub"])

    result = await db.execute(select(RefreshToken).where(RefreshToken.jti == jti))
    record = result.scalar_one_or_none()

    if record is None:
        # Signature was valid but we have no record: the token was issued by a
        # key we still trust yet isn't in our store. Treat as hostile.
        raise AuthenticationError("Refresh token is not recognised.")

    if record.revoked_at is not None:
        # REUSE DETECTED. A revoked token was just presented, which means the
        # legitimate client already rotated it and someone else has a copy.
        # Burn every session this user has and force a fresh login.
        revoked = await _revoke_all_for_user(db, user_id)
        await db.commit()
        logger.warning(
            "Refresh token reuse detected for user %s (jti=%s). "
            "Revoked %d active token(s) for the whole account.",
            user_id,
            jti,
            revoked,
        )
        raise AuthenticationError(
            "This refresh token has already been used. For your security, all "
            "sessions have been ended -- please log in again."
        )

    if not record.is_usable:
        raise AuthenticationError("Refresh token has expired.")

    user = await db.get(User, user_id)
    if user is None or not user.is_active:
        raise AuthenticationError("Account is no longer active.")

    # Rotate.
    record.revoked_at = datetime.now(UTC)
    ttl = max(int((record.expires_at.replace(tzinfo=UTC) if record.expires_at.tzinfo is None else record.expires_at).timestamp() - datetime.now(UTC).timestamp()), 1)
    await redis_client.revoke_jti(jti, ttl)

    new_refresh = await _issue_refresh(db, user)
    new_access = create_access_token(user.id, user.role)
    await db.commit()
    return user, new_access, new_refresh


async def logout(db: AsyncSession, *, refresh_token: str) -> None:
    """
    Revoke a single refresh token.

    Idempotent and quiet: logging out with an already-dead or malformed token
    still 'succeeds'. There is nothing to protect here -- failing loudly would
    only leak whether a given token was ever valid, and would strand clients
    that can't clear their session.
    """
    try:
        payload = decode_token(refresh_token, expected_type="refresh")
    except TokenError:
        return

    jti = payload["jti"]
    result = await db.execute(select(RefreshToken).where(RefreshToken.jti == jti))
    record = result.scalar_one_or_none()
    if record is None or record.revoked_at is not None:
        return

    record.revoked_at = datetime.now(UTC)
    await db.commit()
    await redis_client.revoke_jti(jti, 60 * 60 * 24)


async def logout_all(db: AsyncSession, *, user: User) -> int:
    """Revoke every active refresh token for this user. Returns how many died."""
    result = await db.execute(
        select(RefreshToken).where(
            RefreshToken.user_id == user.id, RefreshToken.revoked_at.is_(None)
        )
    )
    records = list(result.scalars())
    now = datetime.now(UTC)
    for r in records:
        r.revoked_at = now
        await redis_client.revoke_jti(r.jti, 60 * 60 * 24)
    await db.commit()
    logger.info("logout-all revoked %d token(s) for user %s", len(records), user.id)
    return len(records)


async def get_user_by_id(db: AsyncSession, user_id: uuid.UUID) -> User | None:
    return await db.get(User, user_id)
