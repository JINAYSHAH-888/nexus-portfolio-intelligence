"""
Upstox token storage.

THE RULE THAT MAKES THIS NON-TRIVIAL: an access_token does not live for a fixed
duration from issuance. It dies at 03:30 IST on "the following day" -- and a
token minted at 02:30 IST dies at 03:30 the SAME calendar day, 1 hour later.
A naive `expires_in=86400` TTL would keep a dead token alive for hours (every
call fails with 401 until the TTL catches up) or kill a fresh token early
(regenerating one that was still good). `next_upstox_expiry()` computes the
real cutoff explicitly instead.

Tokens are per Upstox-user (their UCC), not per our platform user, because one
Upstox login can legitimately back multiple investor sessions in this app (an
RM using their own broker connection to pull a client's read-only view via
`extended_token`, for instance).
"""
from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass
from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

from backend.infrastructure import redis_client

logger = logging.getLogger(__name__)

IST = ZoneInfo("Asia/Kolkata")
EXPIRY_TIME_IST = time(3, 30)
_REDIS_PREFIX = "upstox:token:"


@dataclass(frozen=True)
class UpstoxToken:
    """Mirrors the Get Token response body verbatim -- see field-by-field docs
    in the Upstox Authentication guide. Nothing here is invented."""

    access_token: str
    extended_token: str | None
    user_id: str
    user_name: str
    email: str
    broker: str
    exchanges: list[str]
    products: list[str]
    order_types: list[str]
    user_type: str
    poa: bool
    is_active: bool
    issued_at: str  # ISO8601, UTC; we stamp this ourselves on receipt

    def to_json(self) -> str:
        return json.dumps(asdict(self))

    @classmethod
    def from_json(cls, raw: str) -> UpstoxToken:
        return cls(**json.loads(raw))

    @classmethod
    def from_api_response(cls, body: dict) -> UpstoxToken:
        """Build from the raw Get Token JSON. Extra/missing optional fields
        degrade gracefully rather than raising, since Upstox has added fields
        to this response before without a version bump."""
        return cls(
            access_token=body["access_token"],
            extended_token=body.get("extended_token"),
            user_id=body["user_id"],
            user_name=body.get("user_name", ""),
            email=body.get("email", ""),
            broker=body.get("broker", "UPSTOX"),
            exchanges=body.get("exchanges", []),
            products=body.get("products", []),
            order_types=body.get("order_types", []),
            user_type=body.get("user_type", "individual"),
            poa=bool(body.get("poa", False)),
            is_active=bool(body.get("is_active", True)),
            issued_at=datetime.now(tz=ZoneInfo("UTC")).isoformat(),
        )


def next_upstox_expiry(now: datetime | None = None) -> datetime:
    """
    The next 03:30 IST from `now`.

    A token minted at 20:00 IST Tuesday expires 03:30 IST Wednesday (next day).
    A token minted at 02:30 IST Wednesday ALSO expires 03:30 IST Wednesday (same
    day, only an hour later) -- because 02:30 is already "after" the previous
    day's 03:30 cutoff. The `<` check below is what encodes that asymmetry.
    """
    now_ist = (now or datetime.now(tz=ZoneInfo("UTC"))).astimezone(IST)
    today_cutoff = now_ist.replace(
        hour=EXPIRY_TIME_IST.hour, minute=EXPIRY_TIME_IST.minute, second=0, microsecond=0
    )
    if now_ist < today_cutoff:
        return today_cutoff
    return today_cutoff + timedelta(days=1)


def seconds_until_expiry(now: datetime | None = None) -> int:
    now = now or datetime.now(tz=ZoneInfo("UTC"))
    delta = next_upstox_expiry(now) - now
    return max(int(delta.total_seconds()), 1)


async def store_token(token: UpstoxToken) -> None:
    ttl = seconds_until_expiry()
    # cache_set does its own json.dumps -- passing token.to_json() (already a
    # string) here would double-encode it into a JSON string containing JSON,
    # which cache_get's single json.loads() would hand back unparsed.
    await redis_client.cache_set(_REDIS_PREFIX + token.user_id, asdict(token), ttl=ttl)
    logger.info(
        "Stored Upstox token for user_id=%s, expires in %ds (03:30 IST)",
        token.user_id,
        ttl,
    )


async def get_token(upstox_user_id: str) -> UpstoxToken | None:
    raw = await redis_client.cache_get(_REDIS_PREFIX + upstox_user_id)
    if raw is None:
        return None
    try:
        return UpstoxToken(**raw)
    except TypeError as e:
        logger.warning("Corrupt Upstox token for %s: %s", upstox_user_id, e)
        return None


async def revoke_token(upstox_user_id: str) -> None:
    await redis_client.cache_delete(_REDIS_PREFIX + upstox_user_id)


def get_service_token_sync() -> str:
    """
    The access token for the dedicated Upstox SERVICE account -- never an
    individual investor's personal connection. Used by processes that have no
    event loop of their own (the feed worker, Celery tasks) and therefore
    can't await `get_token`, so this reads the identical Redis key directly
    with the plain sync `redis` client.

    Shared by feed_worker.py and tasks/market_tasks.py's price backfill so the
    "which token, and what to say when it's missing" logic exists in exactly
    one place.
    """
    import redis

    from backend.core.config import settings

    if not settings.UPSTOX_SERVICE_ACCOUNT_USER_ID:
        raise RuntimeError(
            "UPSTOX_SERVICE_ACCOUNT_USER_ID is not configured. Background "
            "Upstox jobs must run under a DEDICATED service login, not any "
            "individual investor's personal connection."
        )

    client = redis.Redis.from_url(settings.REDIS_URL, decode_responses=True)
    raw = client.get(_REDIS_PREFIX + settings.UPSTOX_SERVICE_ACCOUNT_USER_ID)
    if raw is None:
        raise RuntimeError(
            f"No stored token for the service account "
            f"({settings.UPSTOX_SERVICE_ACCOUNT_USER_ID}). It must complete "
            f"the OAuth flow (GET /api/v1/auth/upstox/login) at least once "
            f"today -- tokens expire daily at 03:30 IST."
        )
    return json.loads(raw)["access_token"]
