"""
WS /api/v1/ws/live-prices

Auth note: browsers cannot set an Authorization header on a WebSocket
handshake, so the access token comes in as a query parameter (`?token=...`).
That is the standard workaround, but it means the token can land in proxy and
access logs -- so we accept the SHORT-LIVED access token only (never a refresh
token; `decode_token` enforces the type claim), and the connection is closed
with a policy-violation code the moment it fails to validate.

Fan-out: `live_price_loader._publish` pushes each quote to the Redis
`live_prices` channel. This endpoint subscribes and relays. If Redis is
disabled or down, `subscribe()` yields nothing, so we fall back to polling
yfinance directly on an interval -- the socket stays useful in single-process
dev with no Redis running.
"""
from __future__ import annotations

import asyncio
import contextlib
import logging
import uuid

from fastapi import APIRouter, Query, WebSocket, WebSocketDisconnect, status
from sqlalchemy import select

from backend.core.security import TokenError, decode_token
from backend.infrastructure import redis_client
from backend.infrastructure.db import SessionLocal
from backend.models import SchemeSelection, User
from backend.services import engine_registry

logger = logging.getLogger(__name__)

router = APIRouter(tags=["websocket"])

POLL_INTERVAL_SECONDS = 15


async def _authenticate(token: str) -> User | None:
    try:
        payload = decode_token(token, expected_type="access")
    except TokenError:
        return None
    if await redis_client.is_jti_revoked(payload["jti"]):
        return None
    async with SessionLocal() as db:
        user = await db.get(User, uuid.UUID(payload["sub"]))
        if user is None or not user.is_active:
            return None
        return user


async def _tickers_for_user(user: User, limit: int = 20) -> dict[str, str]:
    """Ticker -> stock name for the user's selected holdings."""
    async with SessionLocal() as db:
        result = await db.execute(
            select(SchemeSelection).where(SchemeSelection.user_id == user.id)
        )
        selection = result.scalar_one_or_none()

    if selection is None or not selection.selected_scheme_ids:
        return {}

    overlap = engine_registry.get_overlap_engine()
    loader = engine_registry.get_market_loader()

    agg: dict[str, float] = {}
    for sid in selection.selected_scheme_ids:
        for h in overlap._holdings.get(sid, []):  # noqa: SLF001 - read-only JSON data
            name = h.get("name")
            if name:
                agg[name] = agg.get(name, 0.0) + float(h.get("weight") or 0.0)

    out: dict[str, str] = {}
    for name, _w in sorted(agg.items(), key=lambda kv: kv[1], reverse=True):
        t = loader.stock_name_to_ticker(name)
        if t and t not in out:
            out[t] = name
        if len(out) >= limit:
            break
    return out


@router.websocket("/ws/live-prices")
async def live_prices(websocket: WebSocket, token: str = Query(...)) -> None:
    user = await _authenticate(token)
    if user is None:
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION, reason="Invalid token")
        return

    await websocket.accept()

    tickers = await _tickers_for_user(user)
    await websocket.send_json(
        {
            "type": "subscribed",
            "tickers": sorted(tickers),
            # yfinance is delayed. Saying "live" without this flag would be a
            # straightforward lie to anyone trading off the number.
            "data_delayed": True,
        }
    )

    if not tickers:
        await websocket.send_json(
            {
                "type": "info",
                "message": (
                    "No listed equities in your selection (an all-debt portfolio "
                    "holds bonds, not tickers). Nothing to stream."
                ),
            }
        )

    relay_task = asyncio.create_task(_relay_redis(websocket, set(tickers)))
    poll_task = asyncio.create_task(_poll(websocket, tickers))

    try:
        # Block on the client: this raises WebSocketDisconnect the moment they
        # go away, which is what lets us tear the background tasks down instead
        # of leaking them for the life of the process.
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        logger.debug("live-prices socket closed by user %s", user.id)
    except Exception as e:  # pragma: no cover - transport-level oddities
        logger.debug("live-prices socket error: %s", e)
    finally:
        for t in (relay_task, poll_task):
            t.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await t


async def _relay_redis(websocket: WebSocket, tickers: set[str]) -> None:
    """Relay ticks published by any process (Celery, another API worker)."""
    try:
        async for msg in redis_client.subscribe("live_prices"):
            if not isinstance(msg, dict):
                continue
            if tickers and msg.get("ticker") not in tickers:
                continue
            await websocket.send_json({"type": "tick", "data": msg})
    except asyncio.CancelledError:
        raise
    except Exception as e:
        logger.debug("Redis relay ended: %s", e)


async def _poll(websocket: WebSocket, tickers: dict[str, str]) -> None:
    """
    Direct yfinance polling.

    This exists so the socket still works with REDIS_ENABLED=false (local dev).
    get_bulk_quotes() also publishes to Redis internally, so when Redis IS up
    both paths converge on the same data rather than diverging.
    """
    if not tickers:
        return
    live = engine_registry.get_live_price_loader()
    try:
        while True:
            quotes = await live.get_bulk_quotes(list(tickers))
            if quotes:
                await websocket.send_json(
                    {
                        "type": "snapshot",
                        "data_delayed": True,
                        "quotes": [
                            {
                                "ticker": t,
                                "stock_name": tickers.get(t),
                                "ltp": q.ltp,
                                "change": q.change,
                                "change_pct": q.change_pct,
                                "volume": q.volume,
                                "timestamp": q.timestamp.isoformat(),
                            }
                            for t, q in quotes.items()
                        ],
                    }
                )
            await asyncio.sleep(POLL_INTERVAL_SECONDS)
    except asyncio.CancelledError:
        raise
    except Exception as e:
        logger.debug("Price polling ended: %s", e)
