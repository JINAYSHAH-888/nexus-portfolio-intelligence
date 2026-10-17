"""
Shared authenticated HTTP client for Upstox's REST API (v2 and v3).

WHAT THIS MODULE DELIBERATELY DOES NOT DO
--------------------------------------------
It does not know about candles, quotes, holdings, positions, or P&L reports.
Every one of those endpoints has its own URL, query parameters, and response
JSON shape that this codebase has NOT yet seen a real example of -- only a
one-line card summary in a docs sidebar screenshot. Building `candles.py` or
`holdings.py` against a GUESSED response shape is exactly the class of mistake
that produced two real bugs earlier in this integration (the 63%-NAV-drift MF
mismatch, and the two-disagreeing-risk-free-rates bug) -- both caused by
trusting an assumption instead of a verified payload. This client is the
foundation those modules sit on ONCE their real contracts are confirmed; it is
not a place to encode a guess.

WHAT IS SAFE TO BUILD NOW, AND WHY
-------------------------------------
Everything in this file is confirmed by documents already provided, not
inferred:
  - Auth header format ("Authorization: Bearer <access_token>") -- from the
    Get Token API doc and the Market Data Feed V3 doc's Header Parameters.
  - Base URLs (api.upstox.com/v2, api.upstox.com/v3) -- from every endpoint
    URL seen so far.
  - Error-surfacing pattern -- proven against auth.py's real 400-response
    handling (Upstox returns a real, actionable JSON error body worth passing
    through, not collapsing into a generic message).
  - Rate limiting -- reuses infrastructure/rate_limit.py's existing sliding
    window, applied per Upstox account rather than per platform user, since
    Upstox's limits are enforced against the ACCESS TOKEN, not our app.

Concrete conservative defaults (UPSTOX_REST_DEFAULT_RPS) are used until the
real Appendix rate-limit numbers are provided -- deliberately cautious rather
than assumed generous, because being wrong in the cautious direction costs
latency; being wrong in the generous direction gets the token throttled or
revoked.
"""
from __future__ import annotations

import logging
from typing import Any

import httpx

from backend.core.errors import UpstreamError
from backend.core.http_client import (
    SSL_CONTEXT,  # one shared TLS context; per-client creation segfaults in worker threads
)
from backend.infrastructure import rate_limit

logger = logging.getLogger(__name__)

BASE_URL_V2 = "https://api.upstox.com/v2"
BASE_URL_V3 = "https://api.upstox.com/v3"

# Deliberately conservative until Upstox's own Appendix numbers are confirmed.
# Better to under-use a limit we don't yet know than to guess high and get
# throttled or flagged.
DEFAULT_RPS_BUDGET = 8


class UpstoxRestClient:
    """
    Thin, auth-aware wrapper around httpx for Upstox's REST endpoints.

    Every specific endpoint (candles, quotes, holdings, ...) becomes a small
    function that calls `client.get_json(path, params=...)` and interprets the
    response according to ITS OWN VERIFIED contract -- this class only owns
    the auth header, base URL selection, rate limiting, and error surfacing
    that are common to all of them.
    """

    def __init__(self, access_token: str, *, upstox_user_id: str, timeout: float = 15.0):
        self._access_token = access_token
        self._upstox_user_id = upstox_user_id  # rate-limit bucket key
        self._timeout = timeout

    @property
    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self._access_token}",
            "Accept": "application/json",
        }

    async def _throttle(self, endpoint_tag: str) -> None:
        """
        Enforce our own conservative outbound rate before Upstox does it for
        us with a 429. Bucketed per (Upstox account, endpoint family) so one
        chatty endpoint can't exhaust another's budget -- same reasoning as
        the platform's own inbound limiter in infrastructure/rate_limit.py.
        """
        key = f"rl:upstox_out:{endpoint_tag}:{self._upstox_user_id}"
        result = await rate_limit.check(key, DEFAULT_RPS_BUDGET, window_s=1.0)
        if not result.allowed:
            import asyncio

            await asyncio.sleep(min(result.retry_after, 2))

    async def get_json(
        self,
        path: str,
        *,
        base: str = BASE_URL_V2,
        params: dict[str, Any] | None = None,
        endpoint_tag: str = "default",
    ) -> Any:
        """
        GET `base + path`, bearer-authenticated, and return the parsed JSON
        body verbatim -- UNINTERPRETED. The caller (a verified, contract-aware
        function) decides what the shape means; this method's job ends at
        "valid JSON came back with a 2xx status."
        """
        await self._throttle(endpoint_tag)
        url = f"{base}{path}"

        try:
            async with httpx.AsyncClient(timeout=self._timeout, verify=SSL_CONTEXT) as client:
                resp = await client.get(url, headers=self._headers, params=params)
        except httpx.HTTPError as e:
            raise UpstreamError(f"Could not reach Upstox ({path}): {e}") from e

        if resp.status_code == 401:
            raise UpstreamError(
                "Upstox rejected this token (401). It may have expired -- "
                "tokens reset daily at 03:30 IST regardless of issue time.",
            )
        if resp.status_code == 429:
            raise UpstreamError(
                "Upstox rate-limited this request (429). Retry after a short backoff.",
            )
        if resp.status_code >= 400:
            try:
                detail = resp.json()
            except ValueError:
                detail = resp.text
            logger.warning("Upstox %s %s -> %d: %s", "GET", path, resp.status_code, detail)
            raise UpstreamError(
                f"Upstox returned HTTP {resp.status_code} for {path}.", details=detail
            )

        try:
            return resp.json()
        except ValueError as e:
            raise UpstreamError(f"Upstox returned a non-JSON response for {path}.") from e
