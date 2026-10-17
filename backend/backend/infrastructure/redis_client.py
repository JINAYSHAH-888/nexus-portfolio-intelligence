"""
Redis: caching + pub/sub.

HARD CONTRACT: the pre-existing data_loaders import these three names and call
them with these exact shapes:

    await cache_get(key)                    -> deserialized JSON | None
                                               (amfi_loader, alpha_vantage_loader)
    await cache_set(key, value, ttl=<int>)  -> None; value is JSON-serializable
                                               (amfi_loader, alpha_vantage_loader)
    await publish("live_prices", {...})     -> None
                                               (live_price_loader)

Design rule: **Redis is a cache, not a database.** Every function here fails
soft. If Redis is down or REDIS_ENABLED=false, `cache_get` returns None (a
miss), `cache_set`/`publish` become no-ops, and the caller transparently
falls back to its real data source. A cache outage must degrade latency,
never correctness, and never take the API down -- so nothing in this module
raises to its caller.
"""
from __future__ import annotations

import contextlib
import json
import logging
from typing import Any

from backend.core.config import settings

logger = logging.getLogger(__name__)

_client: Any | None = None
_unavailable = False  # sticky flag: stop retrying a dead Redis on every call


async def get_redis() -> Any | None:
    """Lazily create the shared client. Returns None if Redis is unusable."""
    global _client, _unavailable

    if not settings.REDIS_ENABLED or _unavailable:
        return None
    if _client is not None:
        return _client

    try:
        import redis.asyncio as aioredis

        _client = aioredis.from_url(
            settings.REDIS_URL,
            encoding="utf-8",
            decode_responses=True,
            socket_connect_timeout=2,
            socket_timeout=2,
            health_check_interval=30,
        )
        await _client.ping()
        logger.info("Redis connected: %s", settings.REDIS_URL)
        return _client
    except Exception as e:
        logger.warning(
            "Redis unavailable (%s: %s). Falling back to no-cache mode; the app "
            "stays fully functional, just slower on repeat market-data reads.",
            type(e).__name__,
            e,
        )
        _client = None
        _unavailable = True
        return None


async def cache_get(key: str) -> Any | None:
    """Fetch and JSON-decode `key`. Returns None on miss, corruption, or outage."""
    client = await get_redis()
    if client is None:
        return None
    try:
        raw = await client.get(key)
        if raw is None:
            return None
        return json.loads(raw)
    except json.JSONDecodeError:
        # A corrupt/legacy entry must not poison the caller. Drop it and miss.
        logger.warning("Corrupt cache entry at %s; deleting.", key)
        with contextlib.suppress(Exception):
            await client.delete(key)
        return None
    except Exception as e:
        logger.debug("cache_get(%s) failed: %s", key, e)
        return None


async def cache_set(key: str, value: Any, ttl: int = 3600) -> None:
    """JSON-encode and store `value` under `key` with a TTL in seconds."""
    client = await get_redis()
    if client is None:
        return
    try:
        await client.set(key, json.dumps(value, default=str), ex=ttl)
    except Exception as e:
        logger.debug("cache_set(%s) failed: %s", key, e)


async def cache_delete(*keys: str) -> None:
    client = await get_redis()
    if client is None or not keys:
        return
    try:
        await client.delete(*keys)
    except Exception as e:
        logger.debug("cache_delete failed: %s", e)


async def publish(channel: str, message: Any) -> None:
    """Publish a JSON message to a pub/sub channel (used by live price ticks)."""
    client = await get_redis()
    if client is None:
        return
    try:
        await client.publish(channel, json.dumps(message, default=str))
    except Exception as e:
        logger.debug("publish(%s) failed: %s", channel, e)


async def subscribe(channel: str):
    """
    Async-iterate messages on `channel`. Yields decoded payloads.
    Used by the /ws/live-prices websocket fan-out.
    """
    client = await get_redis()
    if client is None:
        return
    pubsub = client.pubsub()
    await pubsub.subscribe(channel)
    try:
        async for msg in pubsub.listen():
            if msg.get("type") != "message":
                continue
            try:
                yield json.loads(msg["data"])
            except json.JSONDecodeError:
                continue
    finally:
        await pubsub.unsubscribe(channel)
        await pubsub.close()


# ── Token revocation (denylist) ────────────────────────────────────────────────
# NOTE: this is a *latency* optimisation only. The Postgres refresh_tokens table
# remains the source of truth for revocation (see services/auth_service.py), so
# a Redis outage cannot silently un-revoke a token.
async def revoke_jti(jti: str, ttl: int) -> None:
    client = await get_redis()
    if client is None:
        return
    try:
        await client.set(f"revoked:{jti}", "1", ex=max(ttl, 1))
    except Exception as e:
        logger.debug("revoke_jti failed: %s", e)


async def is_jti_revoked(jti: str) -> bool:
    client = await get_redis()
    if client is None:
        return False
    try:
        return bool(await client.exists(f"revoked:{jti}"))
    except Exception:
        return False


async def close_redis() -> None:
    global _client
    if _client is not None:
        with contextlib.suppress(Exception):
            await _client.aclose()
        _client = None
