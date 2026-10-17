"""
The standalone process that holds Upstox's ONE market-data websocket
connection and fans ticks out to every browser client through Redis.

WHY A SEPARATE PROCESS, NOT AN ASYNCIO TASK INSIDE FASTAPI
------------------------------------------------------------
The official `upstox-python-sdk`'s `MarketDataStreamerV3` is synchronous and
callback-based (`on_open`/`on_message`/`on_error` run on its own thread,
driven by a blocking `connect()`). Calling `streamer.connect()` from inside
the FastAPI event loop would block it -- one dropped tick's callback stalls
every HTTP request the API is serving. A separate process sidesteps the
sync/async impedance mismatch at a process boundary instead of papering over
it with a thread pool inside the API server, and it means a feed-worker crash
or restart never takes the API down with it.

WHY ONE UPSTREAM CONNECTION, NOT ONE PER INVESTOR
----------------------------------------------------
Upstox caps a user to 2 concurrent market-data connections. If N investors'
browser sessions each opened their own upstream Upstox socket, the platform
would hit that cap almost immediately, AND every investor's feed would be tied
to their own personal Upstox login staying connected -- one investor
disconnecting their account would silently kill live prices for everyone else
subscribed to the same stock through their own selection. Instead:

    ONE Upstox login -- a dedicated SERVICE account (settings.
    UPSTOX_SERVICE_ACCOUNT_USER_ID), NOT any individual investor's personal
    OAuth connection -- backs a single upstream socket. This worker
    subscribes to the UNION of every currently-selected holding across every
    investor (see subscription.py), decodes each tick once, and republishes
    it over Redis pub/sub. Every browser client's `/api/v1/ws/live-prices`
    connection (already built) relays from Redis, not from Upstox directly.

WHY handle_message IS OVERRIDDEN
-----------------------------------
The SDK's own `handle_message` calls `json_format.MessageToDict()` before
emitting to `on_message` listeners, which re-encodes every int64 field
(last-trade-time, volumes, timestamps) as a JSON STRING and silently drops
any singular field left at its default value -- indistinguishable, from the
dict alone, between "this field doesn't apply to this instrument type" (a
genuine index has no volume field) and "this field's real value happens to be
zero" (see feed_decoder.py's module docstring for the full reasoning). This
class intercepts ONE step earlier -- after the SDK's own `decode_protobuf`,
before its `MessageToDict` -- and hands the real typed object to
`feed_decoder.decode_feed_response`, which was built and tested specifically
to preserve that distinction.
"""
from __future__ import annotations

import json
import logging
import signal
import sys
import time

import upstox_client

from backend.core.config import settings
from backend.integrations.upstox import feed_decoder
from backend.integrations.upstox.feed_decoder import Tick

logger = logging.getLogger(__name__)

LIVE_PRICES_CHANNEL = "live_prices"  # matches the existing /api/v1/websocket.py relay contract
SUBSCRIPTION_REFRESH_SECONDS = 300  # re-poll selections every 5 minutes


class DecodingMarketDataStreamer(upstox_client.MarketDataStreamerV3):
    """
    Same as the SDK's `MarketDataStreamerV3`, except `handle_message` is
    overridden to decode via our own typed decoder instead of the SDK's
    `MessageToDict()` dict conversion. Every other method (open/close/error
    handling, reconnect, subscribe/unsubscribe/change_mode) is untouched --
    we are not reimplementing the SDK, only intercepting one decode step.
    """

    def __init__(self, *args, on_tick=None, **kwargs):
        super().__init__(*args, **kwargs)
        self._on_tick = on_tick

    def handle_message(self, ws, message):  # noqa: ARG002 - matches the SDK's signature
        try:
            resp = self.decode_protobuf(message)  # the SDK's own bytes -> pb2.FeedResponse
            decoded = feed_decoder.decode_feed_response(resp)
        except Exception:
            logger.exception("Failed to decode a feed message; dropping this frame.")
            return

        if decoded.type == "market_info":
            logger.info("Market status: %s", decoded.segment_status)

        if self._on_tick:
            for tick in decoded.ticks:
                self._on_tick(tick)

        # Preserve the SDK's own event emission for anything else listening
        # (e.g. a health-check hook via .on("message", ...)) -- we're adding
        # a decode path, not removing the SDK's normal one.
        self.emit(self.Event["MESSAGE"], decoded)


class TickPublisher:
    """
    Sync Redis publisher.

    This worker is a sync process (the SDK's callbacks run on the streamer's
    own thread), so it uses the plain sync `redis` client, not
    infrastructure/redis_client.py's asyncio-based one -- there is no event
    loop here to await into.
    """

    def __init__(self, redis_url: str):
        import redis

        self._client = redis.Redis.from_url(redis_url, decode_responses=True)

    def publish_tick(self, tick: Tick, *, stock_name_by_key: dict[str, str]) -> None:
        """
        Publishes to the SAME channel and (thin, backward-compatible) message
        shape `live_price_loader._publish` already uses, so the existing
        `/api/v1/websocket.py` relay works unchanged. `is_live` is genuinely
        `True` here -- unlike yfinance's delayed quotes, which hardcode it
        `False` -- and `source` is added (additively; existing consumers
        ignore unknown keys) so a client can tell the two apart.
        """
        if tick.ltp is None:
            return

        ticker = stock_name_by_key.get(tick.instrument_key, tick.instrument_key)
        payload = {
            "ticker": ticker,
            "instrument_key": tick.instrument_key,
            "ltp": tick.ltp,
            "change_pct": round(tick.change_pct, 4) if tick.change_pct is not None else None,
            "volume": tick.volume_traded_today,
            "ts": tick.last_trade_time_ms,
            "is_live": True,
            "source": "upstox_ws",
        }
        try:
            self._client.publish(LIVE_PRICES_CHANNEL, json.dumps(payload, default=str))
        except Exception as e:
            logger.debug("Redis publish failed for %s: %s", tick.instrument_key, e)


def _load_service_token() -> str:
    """Thin re-export for readability at this module's call sites -- the real
    implementation is shared with tasks/market_tasks.py via token_store.py."""
    from backend.integrations.upstox.token_store import get_service_token_sync

    return get_service_token_sync()


def _resolve_keys_from_db() -> tuple[list[str], dict[str, str]]:
    """
    Sync DB read (this is a sync worker process, not FastAPI) of every active
    selection, resolved to instrument keys via subscription.py.

    Returns (instrument_keys, stock_name_by_instrument_key) -- the reverse map
    is needed so published ticks can carry the same "ticker" field shape the
    existing relay/consumers already expect.
    """
    from sqlalchemy import create_engine, select
    from sqlalchemy.orm import sessionmaker

    from backend.integrations.upstox import equity_mapping
    from backend.integrations.upstox.subscription import (
        SchemeSelectionSnapshot,
        resolve_subscription_keys,
    )
    from backend.models import SchemeSelection
    from backend.services import engine_registry

    engine = create_engine(settings.sync_database_url, pool_pre_ping=True)
    Session = sessionmaker(bind=engine)

    with Session() as session:
        rows = session.execute(select(SchemeSelection)).scalars().all()
        selections = [
            SchemeSelectionSnapshot(scheme_ids=list(r.selected_scheme_ids), weights=dict(r.weights))
            for r in rows
        ]

    overlap = engine_registry.get_overlap_engine()
    holdings_by_scheme = dict(overlap._holdings)  # noqa: SLF001 - read-only, JSON-sourced

    instrument_key_for_stock: dict[str, str | None] = {}
    stock_name_by_key: dict[str, str] = {}
    seen_names = {h["name"] for hs in holdings_by_scheme.values() for h in hs if h.get("name")}
    for name in seen_names:
        key = equity_mapping.instrument_key_for(name)
        instrument_key_for_stock[name] = key
        if key:
            stock_name_by_key[key] = name

    keys, dropped = resolve_subscription_keys(
        selections,
        holdings_by_scheme,
        instrument_key_for_stock,
        mode_limit=settings.UPSTOX_WS_LIMIT_FULL,
    )
    if dropped:
        logger.warning("%d instrument(s) dropped for capacity this cycle.", len(dropped))

    return keys, stock_name_by_key


def run() -> None:
    """Process entrypoint. Blocks until SIGTERM/SIGINT."""
    logging.basicConfig(
        level=settings.LOG_LEVEL,
        format="%(asctime)s %(levelname)-7s %(name)-38s %(message)s",
    )
    logger.info("Starting Upstox feed worker.")

    access_token = _load_service_token()
    keys, stock_name_by_key = _resolve_keys_from_db()

    publisher = TickPublisher(settings.REDIS_URL)

    def on_tick(tick: Tick) -> None:
        publisher.publish_tick(tick, stock_name_by_key=stock_name_by_key)

    configuration = upstox_client.Configuration()
    configuration.access_token = access_token
    api_client = upstox_client.ApiClient(configuration)

    streamer = DecodingMarketDataStreamer(api_client, keys, mode="full", on_tick=on_tick)

    def on_open():
        logger.info("Connected to Upstox market-data feed (%d instrument keys).", len(keys))

    def on_error(err):
        logger.error("Upstox feed error: %s", err)

    def on_close():
        logger.warning("Upstox feed connection closed.")

    def on_reconnecting(attempt):
        logger.info("Reconnecting to Upstox feed (attempt %s)...", attempt)

    def on_reconnect_stopped(msg):
        logger.error("Auto-reconnect exhausted its retries: %s", msg)

    streamer.on("open", on_open)
    streamer.on("error", on_error)
    streamer.on("close", on_close)
    streamer.on("reconnecting", on_reconnecting)
    streamer.on("autoReconnectStopped", on_reconnect_stopped)
    streamer.auto_reconnect(True, interval=5, retry_count=20)

    shutting_down = {"flag": False}

    def _handle_signal(signum, _frame):
        logger.info("Received signal %s, shutting down.", signum)
        shutting_down["flag"] = True
        streamer.disconnect()
        sys.exit(0)

    signal.signal(signal.SIGTERM, _handle_signal)
    signal.signal(signal.SIGINT, _handle_signal)

    streamer.connect()  # blocks; callbacks fire on the streamer's own thread

    # Periodically re-resolve the subscription set (new selections, schemes
    # opening/closing) without tearing down the connection.
    last_refresh = time.monotonic()
    current_keys = set(keys)
    while not shutting_down["flag"]:
        time.sleep(5)
        if time.monotonic() - last_refresh < SUBSCRIPTION_REFRESH_SECONDS:
            continue
        last_refresh = time.monotonic()
        try:
            new_keys, new_names = _resolve_keys_from_db()
            new_set = set(new_keys)
            to_add = new_set - current_keys
            to_remove = current_keys - new_set
            if to_add:
                streamer.subscribe(list(to_add), "full")
                logger.info("Subscribed to %d new instrument(s).", len(to_add))
            if to_remove:
                streamer.unsubscribe(list(to_remove))
                logger.info("Unsubscribed from %d instrument(s) no longer held.", len(to_remove))
            current_keys = new_set
            stock_name_by_key.update(new_names)
        except Exception:
            logger.exception("Subscription refresh cycle failed; keeping the current set.")


if __name__ == "__main__":
    run()
