"""
The feed worker's streamer subclass and Redis publisher.

`DecodingMarketDataStreamer` is tested by calling `handle_message` directly
with a real serialised protobuf payload -- exactly what the SDK's internal
websocket-frame handler would pass it -- without ever opening a socket. This
proves the override actually intercepts before `MessageToDict` and reaches our
typed decoder, which is the whole point of subclassing rather than using the
SDK's plain `on("message", ...)` event.
"""
from __future__ import annotations

import json
import uuid

from backend.integrations.upstox.feed_worker import (
    LIVE_PRICES_CHANNEL,
    DecodingMarketDataStreamer,
    TickPublisher,
)
from backend.integrations.upstox.proto import nippon_market_data_feed_v3_pb2 as pb2


def _serialised_response() -> bytes:
    resp = pb2.FeedResponse()
    resp.type = pb2.Type.live_feed
    resp.currentTs = 1740729566039

    eq = resp.feeds["NSE_EQ|INE002A01018"]
    eq.requestMode = pb2.RequestMode.full_d5
    eq.fullFeed.marketFF.ltpc.ltp = 2456.75
    eq.fullFeed.marketFF.ltpc.cp = 2440.10
    eq.fullFeed.marketFF.ltpc.ltt = 1740729552723
    eq.fullFeed.marketFF.vtt = 1234567

    idx = resp.feeds["NSE_INDEX|Nifty 50"]
    idx.requestMode = pb2.RequestMode.full_d5
    idx.fullFeed.indexFF.ltpc.ltp = 24500.15
    idx.fullFeed.indexFF.ltpc.cp = 24350.0

    return resp.SerializeToString()


class TestDecodingStreamer:
    def test_handle_message_decodes_int64_fields_as_real_ints_not_strings(self):
        """
        THE POINT OF THE SUBCLASS. The SDK's own MessageToDict path would hand
        back ltt/vtt as JSON strings ("1740729552723"). Intercepting before
        that step must yield real Python ints.
        """
        received: list = []
        streamer = DecodingMarketDataStreamer(
            None, [], mode="full", on_tick=lambda tick: received.append(tick)
        )

        streamer.handle_message(None, _serialised_response())

        by_key = {t.instrument_key: t for t in received}
        eq_tick = by_key["NSE_EQ|INE002A01018"]
        assert isinstance(eq_tick.last_trade_time_ms, int)
        assert eq_tick.last_trade_time_ms == 1740729552723
        assert isinstance(eq_tick.volume_traded_today, int)
        assert eq_tick.volume_traded_today == 1234567

    def test_index_tick_still_has_no_fabricated_volume_via_the_streamer_path(self):
        """The oneof-branch discipline holds through the full override path,
        not just in the decoder tested in isolation."""
        received: list = []
        streamer = DecodingMarketDataStreamer(
            None, [], mode="full", on_tick=lambda tick: received.append(tick)
        )
        streamer.handle_message(None, _serialised_response())

        nifty = next(t for t in received if t.instrument_key == "NSE_INDEX|Nifty 50")
        assert nifty.is_index is True
        assert nifty.volume_traded_today is None

    def test_a_malformed_frame_is_dropped_not_crashed_on(self):
        """One corrupt websocket frame must not kill the worker process --
        garbage bytes should log and return, not raise out of handle_message."""
        received: list = []
        streamer = DecodingMarketDataStreamer(
            None, [], mode="full", on_tick=lambda tick: received.append(tick)
        )
        streamer.handle_message(None, b"\xff\xfe not a valid protobuf frame at all")
        assert received == []  # no crash, no tick emitted

    def test_no_on_tick_callback_is_a_safe_no_op(self):
        streamer = DecodingMarketDataStreamer(None, [], mode="full")  # on_tick omitted
        streamer.handle_message(None, _serialised_response())  # must not raise


class TestLoadServiceToken:
    def test_missing_service_account_config_is_a_clear_error(self, monkeypatch):
        from backend.core.config import settings
        from backend.integrations.upstox.feed_worker import _load_service_token

        monkeypatch.setattr(settings, "UPSTOX_SERVICE_ACCOUNT_USER_ID", None)
        try:
            _load_service_token()
            raise AssertionError("expected RuntimeError")
        except RuntimeError as e:
            assert "not configured" in str(e)

    def test_missing_stored_token_names_the_daily_expiry_as_the_likely_cause(self, monkeypatch):
        """
        The most common real-world reason this fires is the 03:30 IST daily
        reset -- the error message should point the operator at that, not just
        say 'not found'.
        """
        import redis

        from backend.core.config import settings
        from backend.integrations.upstox.feed_worker import _load_service_token

        monkeypatch.setattr(settings, "UPSTOX_SERVICE_ACCOUNT_USER_ID", "SVC001")

        class FakeRedis:
            def get(self, key):
                return None

        monkeypatch.setattr(redis.Redis, "from_url", classmethod(lambda cls, *a, **k: FakeRedis()))

        try:
            _load_service_token()
            raise AssertionError("expected RuntimeError")
        except RuntimeError as e:
            assert "03:30 IST" in str(e)

    def test_a_stored_token_is_returned(self, monkeypatch):
        import json as _json

        import redis

        from backend.core.config import settings
        from backend.integrations.upstox.feed_worker import _load_service_token

        monkeypatch.setattr(settings, "UPSTOX_SERVICE_ACCOUNT_USER_ID", "SVC001")

        class FakeRedis:
            def get(self, key):
                assert key == "upstox:token:SVC001"
                return _json.dumps({"access_token": "the-real-token"})

        monkeypatch.setattr(redis.Redis, "from_url", classmethod(lambda cls, *a, **k: FakeRedis()))

        assert _load_service_token() == "the-real-token"


class TestResolveKeysFromDb:
    def test_resolves_real_selections_against_a_sync_sqlite_db(self, tmp_path, monkeypatch):
        """
        Exercises the actual sync SQLAlchemy path (create_engine against
        settings.sync_database_url) this worker uses -- deliberately NOT the
        async in-memory DB the rest of the test suite shares, since a fresh
        sync engine against ':memory:' would be a SEPARATE, empty database
        from the async fixtures' connection. A temp FILE-backed sqlite makes
        the sync engine see real rows, same as it would against Postgres.
        """
        from sqlalchemy import create_engine
        from sqlalchemy.orm import sessionmaker

        from backend.core.config import settings
        from backend.infrastructure.db import Base
        from backend.models import SchemeSelection

        db_path = tmp_path / "sync_test.db"
        monkeypatch.setattr(settings, "DATABASE_URL", f"sqlite+aiosqlite:///{db_path}")

        sync_engine = create_engine(f"sqlite:///{db_path}")
        Base.metadata.create_all(sync_engine)
        Session = sessionmaker(bind=sync_engine)
        with Session() as s:
            s.add(
                SchemeSelection(
                    user_id=uuid.uuid4(),
                    selected_scheme_ids=["nippon-large-cap-fund"],
                    weights={"nippon-large-cap-fund": 1.0},
                )
            )
            s.commit()

        from backend.integrations.upstox.feed_worker import _resolve_keys_from_db

        keys, stock_name_by_key = _resolve_keys_from_db()

        # Benchmarks always present regardless of selections.
        assert "NSE_INDEX|Nifty 50" in keys
        # At least one real, verified holding of the large-cap fund resolved.
        real_holding_keys = set(keys) - set(settings.UPSTOX_BENCHMARK_INSTRUMENT_KEYS)
        assert len(real_holding_keys) > 0
        assert all(k in stock_name_by_key for k in real_holding_keys)



    def test_publishes_to_the_existing_live_prices_channel_shape(self, monkeypatch):
        """Backward compatible with the existing /api/v1/websocket.py relay
        and live_price_loader._publish's message shape -- only additive keys."""
        import redis

        published = []

        class FakeRedis:
            def publish(self, channel, message):
                published.append((channel, json.loads(message)))

        monkeypatch.setattr(redis.Redis, "from_url", classmethod(lambda cls, *a, **k: FakeRedis()))

        publisher = TickPublisher("redis://fake")
        from backend.integrations.upstox.feed_decoder import Tick

        tick = Tick(
            instrument_key="NSE_EQ|INE002A01018",
            request_mode="full",
            ltp=2456.75,
            prev_close=2440.10,
            last_trade_time_ms=1740729552723,
            volume_traded_today=1234567,
        )
        publisher.publish_tick(tick, stock_name_by_key={"NSE_EQ|INE002A01018": "Reliance"})

        assert len(published) == 1
        channel, payload = published[0]
        assert channel == LIVE_PRICES_CHANNEL
        assert payload["ticker"] == "Reliance"  # resolved back to the existing name convention
        assert payload["ltp"] == 2456.75
        assert payload["is_live"] is True  # genuinely live, unlike yfinance's hardcoded False
        assert payload["source"] == "upstox_ws"

    def test_a_tick_with_no_ltp_is_not_published(self, monkeypatch):
        """An empty/malformed tick (no last traded price at all) carries
        nothing worth telling a client -- must not publish a null price."""
        import redis

        published = []

        class FakeRedis:
            def publish(self, channel, message):
                published.append((channel, message))

        monkeypatch.setattr(redis.Redis, "from_url", classmethod(lambda cls, *a, **k: FakeRedis()))

        publisher = TickPublisher("redis://fake")
        from backend.integrations.upstox.feed_decoder import Tick

        tick = Tick(instrument_key="X", request_mode="full", ltp=None)
        publisher.publish_tick(tick, stock_name_by_key={})
        assert published == []

    def test_redis_being_down_does_not_raise(self, monkeypatch):
        """The publisher must fail open -- a Redis outage should degrade to
        'no live ticks', not crash the feed worker mid-stream."""
        import redis

        class FailingRedis:
            def publish(self, channel, message):
                raise ConnectionError("redis is down")

        monkeypatch.setattr(redis.Redis, "from_url", classmethod(lambda cls, *a, **k: FailingRedis()))

        publisher = TickPublisher("redis://fake")
        from backend.integrations.upstox.feed_decoder import Tick

        tick = Tick(instrument_key="X", request_mode="full", ltp=100.0)
        publisher.publish_tick(tick, stock_name_by_key={})  # must not raise
