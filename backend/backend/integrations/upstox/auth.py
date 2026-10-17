"""
Upstox OAuth 2.0 authorization-code flow.

Three steps, matching the Upstox Authentication guide exactly:
    1. build_authorize_url() -- where we send the browser
    2. (Upstox redirects back to our /callback with ?code=...)
    3. exchange_code() -- server-to-server, trades the single-use code for a
       token (and, bundled in the same response, the user's broker profile)

The code is single-use "regardless of whether the access token generation
succeeds or fails" (Upstox's own wording) -- so exchange_code() must never be
retried with the same code on failure. A caller that retries blindly will get
a confusing second error instead of the real one; we surface the first error
as-is and let the route send the user back to step 1.
"""
from __future__ import annotations

import logging
import secrets
from urllib.parse import urlencode

import httpx

from backend.core.config import settings
from backend.core.errors import UpstreamError, ValidationError
from backend.core.http_client import (
    SSL_CONTEXT,  # one shared TLS context; per-client creation segfaults in worker threads
)
from backend.integrations.upstox.token_store import UpstoxToken

logger = logging.getLogger(__name__)

AUTHORIZE_URL = "https://api.upstox.com/v2/login/authorization/dialog"
TOKEN_URL = "https://api.upstox.com/v2/login/authorization/token"


def _require_configured() -> None:
    if not (settings.UPSTOX_API_KEY and settings.UPSTOX_API_SECRET):
        raise ValidationError(
            "Upstox integration is not configured. Set UPSTOX_API_KEY and "
            "UPSTOX_API_SECRET (see .env.example)."
        )


def build_authorize_url(state: str | None = None) -> tuple[str, str]:
    """
    Returns (url, state). If the caller doesn't supply a state, we generate a
    random one -- an unauthenticated, unpredictable state is what makes this an
    effective CSRF guard on the callback, not just an echo parameter.
    """
    _require_configured()
    state = state or secrets.token_urlsafe(24)
    params = {
        "response_type": "code",
        "client_id": settings.UPSTOX_API_KEY,
        "redirect_uri": settings.UPSTOX_REDIRECT_URI,
        "state": state,
    }
    return f"{AUTHORIZE_URL}?{urlencode(params)}", state


async def exchange_code(code: str) -> UpstoxToken:
    """
    Trade a single-use authorization code for an access token + broker profile.

    Raises UpstreamError on anything Upstox itself rejects (bad/reused code,
    misconfigured redirect_uri, inactive segments) -- these are real, specific
    failure states worth showing the caller, not generic 500s.
    """
    _require_configured()

    data = {
        "code": code,
        "client_id": settings.UPSTOX_API_KEY,
        "client_secret": settings.UPSTOX_API_SECRET,
        "redirect_uri": settings.UPSTOX_REDIRECT_URI,
        "grant_type": "authorization_code",
    }

    try:
        async with httpx.AsyncClient(timeout=15.0, verify=SSL_CONTEXT) as client:
            resp = await client.post(
                TOKEN_URL,
                headers={
                    "accept": "application/json",
                    "Content-Type": "application/x-www-form-urlencoded",
                },
                data=data,
            )
    except httpx.HTTPError as e:
        raise UpstreamError(f"Could not reach Upstox to exchange the auth code: {e}") from e

    if resp.status_code >= 400:
        # Upstox's error body carries a real, actionable message (e.g. "No
        # segments for these users are active") -- pass it through rather than
        # collapsing everything into a bare "token exchange failed".
        try:
            detail = resp.json()
        except ValueError:
            detail = resp.text
        logger.warning("Upstox token exchange failed (%d): %s", resp.status_code, detail)
        raise UpstreamError(
            f"Upstox rejected the authorization code (HTTP {resp.status_code}).",
            details=detail,
        )

    body = resp.json()
    token = UpstoxToken.from_api_response(body)

    if not token.is_active:
        # A dormant/suspended broker account can still complete OAuth and hand
        # back a token that fails on every subsequent call. Catch it here with
        # a clear message instead of a confusing downstream 401 on the first
        # real API call.
        raise UpstreamError(
            "This Upstox account is not active. Reactivate it via the Upstox "
            "app or web before connecting it here."
        )

    return token
