"""
Decodes Upstox Market Data Feed V3 protobuf messages into plain, typed Python.

THE LANDMINE THIS MODULE EXISTS TO DEFUSE
------------------------------------------
`FullFeed` is a oneof: `marketFF` (a `MarketFullFeed`) OR `indexFF` (an
`IndexFullFeed`) -- never both, and they are NOT the same shape.
`IndexFullFeed` has no `vtt` (volume), no `oi` (open interest), and no
`marketLevel` (bid/ask depth) fields AT ALL -- not "always zero", genuinely
absent from the message type. Verified directly against the compiled
bindings: `hasattr(IndexFullFeed(), "vtt")` is `False`.

A decoder that reads `feed.fullFeed.marketFF.vtt` unconditionally on every
instrument would either crash on an index tick or -- if it reads through
`getattr(..., default=0)` -- silently record NIFTY's volume as zero forever.
Since NIFTY is the benchmark this platform computes beta and tracking error
against, that zero would quietly poison every risk number derived from it.

Every function here branches on `WhichOneof()` explicitly and returns `None`
for anything that doesn't exist on that branch, never `0`. `None` says
"this doesn't apply to this instrument type"; `0` would say "we asked and the
answer was zero," which is a different, false claim for an index.

WHY THE BINDINGS ARE NAMED `nippon_market_data_feed_v3`
-------------------------------------------------------
`upstox_client` ships its OWN compiled copy of a proto whose file is also
named `MarketDataFeedV3.proto`. Generated protobuf modules register
themselves in a single process-wide descriptor pool keyed by that filename,
so importing the SDK and a vendored copy of the same filename made the second
one raise `duplicate file name MarketDataFeedV3.proto` at import time -- and
since feed_worker.py imports both, it could not be imported or tested at all.

Our copy is therefore compiled from `nippon_market_data_feed_v3.proto` under
package `nippon.upstox.feed.v3`, which cannot collide with the SDK's
registration no matter which SDK version is installed. Renaming is safe
because proto3 encodes FIELD NUMBERS, not message or package names: bytes
produced by the SDK's classes parse identically through these bindings, which
`tests/test_feed_decoder.py` asserts directly against SDK-encoded input.

Regenerate with:
    python -m grpc_tools.protoc -Ibackend/integrations/upstox/proto \
        --python_out=backend/integrations/upstox/proto \
        nippon_market_data_feed_v3.proto
"""
from __future__ import annotations

from dataclasses import dataclass, field

from backend.integrations.upstox.proto import nippon_market_data_feed_v3_pb2 as pb2

MARKET_STATUS_NAMES: dict[int, str] = {
    v: k for k, v in pb2.MarketStatus.items()
}


@dataclass(frozen=True)
class Tick:
    """One instrument's decoded tick. Fields that don't exist for this
    instrument's oneof branch (e.g. volume on an index) are None, not 0."""

    instrument_key: str
    request_mode: str

    # LTPC -- present on every mode.
    ltp: float | None = None
    last_trade_time_ms: int | None = None
    last_trade_qty: int | None = None
    prev_close: float | None = None

    # MarketFullFeed only (absent, not zero, on an index).
    is_index: bool = False
    avg_traded_price: float | None = None
    volume_traded_today: int | None = None
    open_interest: float | None = None
    implied_volatility: float | None = None
    total_buy_qty: float | None = None
    total_sell_qty: float | None = None
    bid_ask: list[dict] = field(default_factory=list)

    # Shared: OHLC candles (both market and index feeds carry this).
    ohlc: dict[str, dict] = field(default_factory=dict)

    # option_greeks / full_d5 mode only.
    greeks: dict | None = None

    @property
    def change(self) -> float | None:
        if self.ltp is None or self.prev_close is None:
            return None
        return self.ltp - self.prev_close

    @property
    def change_pct(self) -> float | None:
        if self.ltp is None or not self.prev_close:
            return None
        return (self.ltp - self.prev_close) / self.prev_close * 100


def _decode_ltpc(ltpc: pb2.LTPC) -> dict:
    return {
        "ltp": ltpc.ltp,
        "last_trade_time_ms": ltpc.ltt or None,
        "last_trade_qty": ltpc.ltq or None,
        "prev_close": ltpc.cp,
    }


def _decode_ohlc(market_ohlc: pb2.MarketOHLC) -> dict[str, dict]:
    """
    Keyed by `OHLC.interval`. Upstox does not document a closed enum for this
    string field, so we pass it through verbatim rather than mapping it onto a
    guessed set of allowed values -- an unrecognised interval string is still
    perfectly usable data, keyed correctly.
    """
    out: dict[str, dict] = {}
    for bar in market_ohlc.ohlc:
        out[bar.interval] = {
            "open": bar.open,
            "high": bar.high,
            "low": bar.low,
            "close": bar.close,
            "volume": bar.vol or None,
            "ts": bar.ts or None,
        }
    return out


def _decode_greeks(g: pb2.OptionGreeks) -> dict:
    return {"delta": g.delta, "theta": g.theta, "gamma": g.gamma, "vega": g.vega, "rho": g.rho}


def _decode_bid_ask(level: pb2.MarketLevel) -> list[dict]:
    return [
        {"bid_qty": q.bidQ, "bid_price": q.bidP, "ask_qty": q.askQ, "ask_price": q.askP}
        for q in level.bidAskQuote
    ]


_REQUEST_MODE_NAMES: dict[int, str] = {v: k for k, v in pb2.RequestMode.items()}


def decode_feed(instrument_key: str, feed: pb2.Feed) -> Tick | None:
    """Decode one instrument's `Feed` message. Returns None for an empty/unset
    feed (the oneof having no branch set) rather than fabricating zeros."""
    which = feed.WhichOneof("FeedUnion")
    mode_name = _REQUEST_MODE_NAMES.get(feed.requestMode, "unknown")

    if which is None:
        return None

    if which == "ltpc":
        return Tick(
            instrument_key=instrument_key,
            request_mode=mode_name,
            **_decode_ltpc(feed.ltpc),
        )

    if which == "firstLevelWithGreeks":
        f = feed.firstLevelWithGreeks
        return Tick(
            instrument_key=instrument_key,
            request_mode=mode_name,
            **_decode_ltpc(f.ltpc),
            bid_ask=[
                {
                    "bid_qty": f.firstDepth.bidQ,
                    "bid_price": f.firstDepth.bidP,
                    "ask_qty": f.firstDepth.askQ,
                    "ask_price": f.firstDepth.askP,
                }
            ],
            greeks=_decode_greeks(f.optionGreeks),
            volume_traded_today=f.vtt or None,
            open_interest=f.oi or None,
            implied_volatility=f.iv or None,
        )

    if which == "fullFeed":
        branch = feed.fullFeed.WhichOneof("FullFeedUnion")

        if branch == "indexFF":
            # An index has LTPC + OHLC ONLY. No depth, no volume, no OI -- the
            # fields genuinely do not exist on this message, so everything
            # market-specific stays None rather than being read as 0.
            idx = feed.fullFeed.indexFF
            return Tick(
                instrument_key=instrument_key,
                request_mode=mode_name,
                is_index=True,
                ohlc=_decode_ohlc(idx.marketOHLC),
                **_decode_ltpc(idx.ltpc),
            )

        if branch == "marketFF":
            mkt = feed.fullFeed.marketFF
            return Tick(
                instrument_key=instrument_key,
                request_mode=mode_name,
                is_index=False,
                avg_traded_price=mkt.atp or None,
                volume_traded_today=mkt.vtt or None,
                open_interest=mkt.oi or None,
                implied_volatility=mkt.iv or None,
                total_buy_qty=mkt.tbq or None,
                total_sell_qty=mkt.tsq or None,
                bid_ask=_decode_bid_ask(mkt.marketLevel),
                ohlc=_decode_ohlc(mkt.marketOHLC),
                greeks=_decode_greeks(mkt.optionGreeks) if mkt.HasField("optionGreeks") else None,
                **_decode_ltpc(mkt.ltpc),
            )

        return None  # oneof unset -- an empty fullFeed message

    return None


@dataclass(frozen=True)
class DecodedResponse:
    type: str
    current_ts: int
    ticks: list[Tick]
    segment_status: dict[str, str]


def decode_response(raw: bytes) -> DecodedResponse:
    """Top-level entry point: raw websocket binary frame -> DecodedResponse."""
    resp = pb2.FeedResponse()
    resp.ParseFromString(raw)
    return decode_feed_response(resp)


def decode_feed_response(resp: pb2.FeedResponse) -> DecodedResponse:
    """
    Same decode, starting from an already-parsed `pb2.FeedResponse` object.

    This is what the live feed worker actually calls: the official Upstox SDK
    parses the wire bytes into this exact object internally (via its own
    `decode_protobuf`) before it would normally hand you a lossy
    `json_format.MessageToDict()` dict -- which re-encodes every int64 field
    (ltt, ltq, vtt, currentTs, OHLC vol/ts) as a JSON STRING, a detail
    confirmed directly against the SDK's own output and easy to get wrong
    silently (an unconverted `"1740729552723"` compares unequal to any int).
    The worker intercepts one call earlier, before that lossy step, and hands
    the real typed object straight to this function instead.
    """
    ticks = []
    for key, feed in resp.feeds.items():
        tick = decode_feed(key, feed)
        if tick is not None:
            ticks.append(tick)

    segment_status = {
        segment: MARKET_STATUS_NAMES.get(status, "UNKNOWN")
        for segment, status in resp.marketInfo.segmentStatus.items()
    }

    return DecodedResponse(
        type=pb2.Type.Name(resp.type),
        current_ts=resp.currentTs,
        ticks=ticks,
        segment_status=segment_status,
    )
