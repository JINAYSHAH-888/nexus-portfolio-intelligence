"""
Bridges a platform User to their Upstox broker session.

Two-tier storage, deliberately:
    Postgres (UpstoxConnection)  -- durable identity: WHO they are on Upstox
    Redis (token_store)          -- ephemeral credential: what proves it right now

A platform user can be "connected" (a row exists) while their token has expired
overnight -- that's the normal daily state, not an error. `get_valid_token`
distinguishes "never connected" from "connected but needs to re-auth today" so
the API can return the right one (404 vs 401-shaped "reconnect needed").
"""
from __future__ import annotations

import logging
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.core.errors import NotFoundError
from backend.integrations.upstox.token_store import UpstoxToken, get_token, revoke_token
from backend.models import UpstoxConnection, User

logger = logging.getLogger(__name__)


async def get_connection(db: AsyncSession, user: User) -> UpstoxConnection | None:
    result = await db.execute(
        select(UpstoxConnection).where(UpstoxConnection.user_id == user.id)
    )
    return result.scalar_one_or_none()


async def link_account(db: AsyncSession, user: User, token: UpstoxToken) -> UpstoxConnection:
    """Persist the identity (Postgres) after a successful OAuth exchange. The
    token itself is stored separately by the caller via token_store.store_token."""
    conn = await get_connection(db, user)
    fields = {
        "upstox_user_id": token.user_id,
        "upstox_user_name": token.user_name,
        "upstox_email": token.email,
        "exchanges": token.exchanges,
        "products": token.products,
        "has_extended_token": token.extended_token is not None,
        "poa": token.poa,
    }
    if conn is None:
        conn = UpstoxConnection(user_id=user.id, **fields)
        db.add(conn)
    else:
        for k, v in fields.items():
            setattr(conn, k, v)
        conn.last_connected_at = datetime.now(UTC)

    await db.commit()
    await db.refresh(conn)
    logger.info("Linked Upstox account %s to platform user %s", token.user_id, user.id)
    return conn


async def get_valid_token(db: AsyncSession, user: User) -> UpstoxToken:
    """
    The token for this user's Upstox session, or a specific, actionable error.

    NotFoundError distinguishes two states a client needs to render differently:
      - never connected at all -> "Connect your Upstox account" CTA
      - connected, but today's token has expired (the normal 03:30 IST reset)
        -> "Reconnect" CTA, with their identity already known
    """
    conn = await get_connection(db, user)
    if conn is None:
        raise NotFoundError(
            "No Upstox account connected. Start at GET /api/v1/auth/upstox/login."
        )

    token = await get_token(conn.upstox_user_id)
    if token is None:
        raise NotFoundError(
            "Your Upstox session has expired (tokens reset daily at 03:30 IST). "
            "Reconnect at GET /api/v1/auth/upstox/login.",
        )
    return token


async def unlink_account(db: AsyncSession, user: User) -> None:
    conn = await get_connection(db, user)
    if conn is None:
        return
    await revoke_token(conn.upstox_user_id)
    await db.delete(conn)
    await db.commit()
    logger.info("Unlinked Upstox account for platform user %s", user.id)
