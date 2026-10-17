"""
The Market Data Feed V3 protobuf decoder.

The whole reason this module exists as a separate, carefully-tested layer:
`FullFeed` is a oneof between `MarketFullFeed` and `IndexFullFeed`, and the two
are NOT the same shape -- an index has no volume, no open interest, no bid/ask
depth field AT ALL. A decoder reading those fields unconditionally would
either crash on an index tick or silently record its volume as zero forever.
Since NIFTY is the benchmark this platform computes beta and tracking error
against, a silent zero there would quietly poison every risk number derived
from it. These tests build real protobuf messages (not mocks of the decoder)
and assert on what comes out.
"""
from __future__ import annotations

from backend.integrations.upstox.feed_decoder import decode_feed, decode_response
from backend.integrations.upstox.proto import nippon_market_data_feed_v3_pb2 as pb2


def _feed_response(**kwargs) -> pb2.FeedResponse:
    resp = pb2.FeedResponse()
    resp.type = kwargs.get("type", pb2.Type.live_feed)
    resp.currentTs = kwargs.get("current_ts", 1740729566039)
    return resp


class TestIndexVsMarketBranch:
    def test_index_tick_has_no_volume_oi_or_depth(self):
        """The core guarantee: these are None (absent), never 0 (a real
        answer). A silent zero would be a false claim about NIFTY's volume."""
        resp = _feed_response()
        idx = resp.feeds["NSE_INDEX|Nifty 50"]
        idx.requestMode = pb2.RequestMode.full_d5
        idx.fullFeed.indexFF.ltpc.ltp = 24500.15
        idx.fullFeed.indexFF.ltpc.cp = 24350.0

        decoded = decode_response(resp.SerializeToString())
        tick = decoded.ticks[0]

        assert tick.is_index is True
        assert tick.volume_traded_today is None
        assert tick.open_interest is None
        assert tick.avg_traded_price is None
        assert tick.bid_ask == []
        assert tick.ltp == 24500.15  # LTPC IS present on both branches

    def test_equity_tick_carries_full_market_data(self):
        resp = _feed_response()
        eq = resp.feeds["NSE_EQ|INE002A01018"]
        eq.requestMode = pb2.RequestMode.full_d5
        eq.fullFeed.marketFF.ltpc.ltp = 2456.75
        eq.fullFeed.marketFF.ltpc.cp = 2440.10
        eq.fullFeed.marketFF.vtt = 1234567
        eq.fullFeed.marketFF.oi = 500.0
        q = eq.fullFeed.marketFF.marketLevel.bidAskQuote.add()
        q.bidQ, q.bidP, q.askQ, q.askP = 100, 2456.5, 50, 2457.0

        decoded = decode_response(resp.SerializeToString())
        tick = decoded.ticks[0]

        assert tick.is_index is False
        assert tick.volume_traded_today == 1234567
        assert tick.open_interest == 500.0
        assert tick.bid_ask == [
            {"bid_qty": 100, "bid_price": 2456.5, "ask_qty": 50, "ask_price": 2457.0}
        ]

    def test_a_mixed_batch_keeps_each_instrument_on_its_own_branch(self):
        """The realistic case: one subscription batch with both an index and
        equities. Each tick must decode according to ITS OWN oneof branch,
        not leak fields from one instrument type into another."""
        resp = _feed_response()
        resp.feeds["NSE_INDEX|Nifty 50"].fullFeed.indexFF.ltpc.ltp = 24500.0
        resp.feeds["NSE_EQ|INE002A01018"].fullFeed.marketFF.ltpc.ltp = 2456.75
        resp.feeds["NSE_EQ|INE002A01018"].fullFeed.marketFF.vtt = 999

        decoded = decode_response(resp.SerializeToString())
        by_key = {t.instrument_key: t for t in decoded.ticks}

        assert by_key["NSE_INDEX|Nifty 50"].volume_traded_today is None
        assert by_key["NSE_EQ|INE002A01018"].volume_traded_today == 999


class TestLtpcMode:
    def test_ltpc_only_mode_decodes_the_minimal_shape(self):
        resp = _feed_response()
        f = resp.feeds["NSE_FO|45450"]
        f.requestMode = pb2.RequestMode.ltpc
        f.ltpc.ltp = 219.3
        f.ltpc.ltt = 1740729552723
        f.ltpc.ltq = 75
        f.ltpc.cp = 494.05

        decoded = decode_response(resp.SerializeToString())
        tick = decoded.ticks[0]

        assert tick.ltp == 219.3
        assert tick.last_trade_qty == 75
        assert tick.request_mode == "ltpc"
        # LTPC mode carries nothing else -- these must not be fabricated.
        assert tick.volume_traded_today is None
        assert tick.bid_ask == []


class TestChangeCalculation:
    def test_change_and_change_pct_derive_from_ltp_and_cp(self):
        resp = _feed_response()
        f = resp.feeds["X"]
        f.ltpc.ltp = 110.0
        f.ltpc.cp = 100.0

        tick = decode_response(resp.SerializeToString()).ticks[0]
        assert tick.change == 10.0
        assert tick.change_pct == 10.0

    def test_change_pct_does_not_divide_by_zero_on_a_missing_prev_close(self):
        resp = _feed_response()
        resp.feeds["X"].ltpc.ltp = 50.0
        # cp left at its proto default (0.0) -- must not raise ZeroDivisionError.
        tick = decode_response(resp.SerializeToString()).ticks[0]
        assert tick.change_pct is None


class TestMarketStatus:
    def test_segment_status_is_decoded_from_the_enum_map(self):
        resp = _feed_response(type=pb2.Type.market_info)
        resp.marketInfo.segmentStatus["NSE_EQ"] = pb2.MarketStatus.NORMAL_OPEN
        resp.marketInfo.segmentStatus["NSE_FO"] = pb2.MarketStatus.CLOSING_START

        decoded = decode_response(resp.SerializeToString())
        assert decoded.type == "market_info"
        assert decoded.segment_status == {"NSE_EQ": "NORMAL_OPEN", "NSE_FO": "CLOSING_START"}


class TestOhlcKeying:
    def test_multiple_intervals_are_keyed_by_their_own_string(self):
        resp = _feed_response()
        f = resp.feeds["X"]
        f.fullFeed.marketFF.ltpc.ltp = 100.0
        for interval, close in (("1d", 100.0), ("I1", 99.5), ("I30", 98.0)):
            bar = f.fullFeed.marketFF.marketOHLC.ohlc.add()
            bar.interval, bar.close = interval, close

        tick = decode_response(resp.SerializeToString()).ticks[0]
        assert set(tick.ohlc) == {"1d", "I1", "I30"}
        assert tick.ohlc["I30"]["close"] == 98.0


class TestUnsetFeed:
    def test_a_feed_with_no_oneof_branch_set_decodes_to_none(self):
        """An empty Feed message (no ltpc/fullFeed/firstLevelWithGreeks set at
        all) must be skipped, not crash the whole batch."""
        f = pb2.Feed()
        assert decode_feed("X", f) is None

    def test_one_bad_instrument_does_not_drop_the_rest_of_the_batch(self):
        resp = _feed_response()
        resp.feeds["good"].ltpc.ltp = 100.0
        # "empty" has no oneof set at all -- decode_feed returns None for it.
        resp.feeds["empty"].requestMode = pb2.RequestMode.ltpc

        decoded = decode_response(resp.SerializeToString())
        keys = {t.instrument_key for t in decoded.ticks}
        assert "good" in keys


class TestCoexistsWithTheUpstoxSdkProto:
    """
    `upstox_client` ships its own compiled copy of a proto whose FILE is also
    called MarketDataFeedV3.proto. Generated modules register themselves in a
    single process-wide descriptor pool keyed by that filename, so a vendored
    copy under the same name raised `duplicate file name
    MarketDataFeedV3.proto` the moment both were imported together -- which
    feed_worker.py does, making it impossible to import or test at all.

    Our bindings are compiled from nippon_market_data_feed_v3.proto under
    package nippon.upstox.feed.v3 so the clash cannot recur. These tests fail
    if anyone renames them back, or regenerates them from a .proto that
    reinstates the upstream package.
    """

    def test_both_protos_import_in_one_process(self):
        import upstox_client  # noqa: F401  -- registers the SDK's descriptors
        from upstox_client.feeder.proto import MarketDataFeedV3_pb2 as sdk

        assert pb2.FeedResponse.DESCRIPTOR.file.name == "nippon_market_data_feed_v3.proto"
        assert sdk.FeedResponse.DESCRIPTOR.file.name == "MarketDataFeedV3.proto"

    def test_our_package_is_not_the_upstream_one(self):
        assert pb2.FeedResponse.DESCRIPTOR.full_name.startswith("nippon.upstox.feed.v3.")

    def test_sdk_encoded_bytes_decode_identically(self):
        """
        THE PROPERTY THAT MAKES THE RENAME SAFE. proto3 puts field NUMBERS on
        the wire, not message or package names, so bytes produced by the SDK's
        own classes -- i.e. shaped exactly like what Upstox sends -- must parse
        through our renamed bindings with every value intact, oneof included.
        """
        from upstox_client.feeder.proto import MarketDataFeedV3_pb2 as sdk

        key = "NSE_EQ|INE002A01018"
        upstream = sdk.FeedResponse()
        upstream.type = sdk.live_feed
        upstream.currentTs = 1760000000000
        ff = upstream.feeds[key].fullFeed.marketFF
        ff.ltpc.ltp = 1234.56
        ff.ltpc.ltq = 42
        ff.vtt = 987654
        ff.oi = 11.5
        quote = ff.marketLevel.bidAskQuote.add()
        quote.bidP, quote.askP, quote.bidQ, quote.askQ = 1234.0, 1235.0, 10, 20

        decoded = pb2.FeedResponse.FromString(upstream.SerializeToString())

        ours = decoded.feeds[key].fullFeed
        assert ours.WhichOneof("FullFeedUnion") == "marketFF"
        assert ours.marketFF.ltpc.ltp == 1234.56
        assert ours.marketFF.ltpc.ltq == 42
        assert ours.marketFF.vtt == 987654
        assert ours.marketFF.oi == 11.5
        assert ours.marketFF.marketLevel.bidAskQuote[0].askP == 1235.0
        assert decoded.currentTs == 1760000000000

    def test_sdk_encoded_index_tick_still_decodes_as_an_index(self):
        """The oneof branch must survive the rename too -- an index tick
        misread as a market tick is the exact bug this decoder exists for."""
        from upstox_client.feeder.proto import MarketDataFeedV3_pb2 as sdk

        key = "NSE_INDEX|Nifty 50"
        upstream = sdk.FeedResponse()
        upstream.type = sdk.live_feed
        upstream.feeds[key].fullFeed.indexFF.ltpc.ltp = 24500.25

        decoded = pb2.FeedResponse.FromString(upstream.SerializeToString())

        assert decoded.feeds[key].fullFeed.WhichOneof("FullFeedUnion") == "indexFF"
        assert decoded.feeds[key].fullFeed.indexFF.ltpc.ltp == 24500.25
