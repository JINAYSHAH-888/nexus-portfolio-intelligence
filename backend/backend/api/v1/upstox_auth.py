"""
Upstox account linking.

    GET  /auth/upstox/login     -> redirects the browser to Upstox's login page
    GET  /auth/upstox/callback  -> Upstox lands here with ?code=...&state=...
    GET  /auth/upstox/status    -> is an Upstox account connected right now?
    POST /auth/upstox/disconnect

`state` round-trips through Redis (not a signed cookie) so the flow works
identically whether the browser follows the redirect itself or a mobile app
opens Upstox's page in a system browser tab -- there is no assumption that the
callback lands back in the same browser session that started it. It DOES still
have to land on the same PLATFORM user's login, so `state` is bound to our
platform's access token, not just a random nonce.
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Query

from backend.core.deps import CurrentUser, DbSession
from backend.core.errors import AuthenticationError, ValidationError
from backend.infrastructure import redis_client
from backend.integrations.upstox import auth as upstox_auth
from backend.integrations.upstox.token_store import store_token
from backend.services import upstox_service

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/auth/upstox", tags=["upstox"])

_STATE_PREFIX = "upstox:oauth_state:"
_STATE_TTL_SECONDS = 600  # generous for an interactive login, tight enough to limit replay


@router.get("/login")
async def login(user: CurrentUser) -> dict:
    """
    Start the OAuth flow. Requires our OWN auth (CurrentUser) first -- Upstox's
    `state` round-trip is what binds "this Upstox login" to "this platform
    user" on the way back, so we must know who's asking before we redirect.

    Returns the authorize URL as JSON rather than issuing a raw HTTP redirect.
    This is deliberate, not a style choice: this endpoint requires a Bearer
    token (CurrentUser), but a real browser navigating a link or address bar
    can NEVER attach a custom Authorization header -- those two requirements
    are fundamentally incompatible. A raw redirect also breaks any
    programmatic caller (Swagger's "Execute" button, or a frontend using
    fetch()) because fetch() auto-follows redirects, and following this one
    means attempting a cross-origin XHR straight into Upstox's login page,
    which Upstox correctly refuses -- reported by the browser as an opaque
    "Failed to fetch", not a clear CORS error.

    The correct call sequence is two steps, exactly because of that
    incompatibility: (1) an authenticated fetch() to THIS endpoint for the
    URL (same-origin, real auth header, no redirect involved), then (2) the
    caller performs the actual top-level navigation to that URL itself
    (`window.location.href = authorize_url` in a browser, or literally
    pasting the URL into a new tab when testing by hand) -- a step that
    needs no auth header at all, since Upstox's login page doesn't want one.
    """
    url, state = upstox_auth.build_authorize_url()
    # Bind the state to the platform user id so the callback can't be replayed
    # against a different account than the one that initiated it.
    await redis_client.cache_set(
        _STATE_PREFIX + state, {"user_id": str(user.id)}, ttl=_STATE_TTL_SECONDS
    )
    return {"authorize_url": url, "state": state}


@router.get("/callback")
async def callback(
    db: DbSession,
    code: str = Query(...),
    state: str = Query(...),
) -> dict:
    """
    Upstox redirects here after the customer logs in.

    We deliberately do NOT re-derive `user` from the caller's own bearer token:
    a server-to-server redirect from Upstox carries no such header. `state` is
    the only thing tying this callback back to a platform user, which is why
    login() persists it server-side rather than trusting a client-supplied value.
    """
    bound = await redis_client.cache_get(_STATE_PREFIX + state)
    if bound is None:
        # Expired (>10 min), already consumed, or forged. Upstox's own code is
        # also single-use, so a replay of an old callback URL fails at the
        # token exchange too -- this check just gives a clearer message sooner.
        raise ValidationError(
            "This login link has expired or was already used. "
            "Start again at GET /api/v1/auth/upstox/login."
        )
    await redis_client.cache_delete(_STATE_PREFIX + state)  # single-use

    from uuid import UUID

    from backend.models import User

    platform_user = await db.get(User, UUID(bound["user_id"]))
    if platform_user is None:
        raise AuthenticationError("The platform session that started this login no longer exists.")

    token = await upstox_auth.exchange_code(code)
    await store_token(token)
    conn = await upstox_service.link_account(db, platform_user, token)

    return {
        "connected": True,
        "upstox_user_id": conn.upstox_user_id,
        "upstox_user_name": conn.upstox_user_name,
        "exchanges": conn.exchanges,
        "has_extended_token": conn.has_extended_token,
        "token_expires": "03:30 IST (daily, regardless of issue time)",
    }


@router.get("/status")
async def status(user: CurrentUser, db: DbSession) -> dict:
    conn = await upstox_service.get_connection(db, user)
    if conn is None:
        return {"connected": False}

    from backend.integrations.upstox.token_store import get_token

    token = await get_token(conn.upstox_user_id)
    return {
        "connected": True,
        "upstox_user_id": conn.upstox_user_id,
        "upstox_user_name": conn.upstox_user_name,
        "exchanges": conn.exchanges,
        "has_extended_token": conn.has_extended_token,
        "last_connected_at": conn.last_connected_at.isoformat(),
        # A row can exist while today's token has already expired (03:30 IST
        # reset) -- surface that distinction rather than implying live access.
        "token_valid_now": token is not None,
    }


@router.post("/disconnect")
async def disconnect(user: CurrentUser, db: DbSession) -> dict:
    await upstox_service.unlink_account(db, user)
    return {"connected": False, "message": "Upstox account disconnected."}
