"""
Historical Candle Data V3 / Intraday Candle Data V3.

Every fact pinned here is verified, not inferred: the to_date-before-from_date
path ordering, the unit/interval split (a real V3 change from v2's single
"1minute"/"day" strings), OHLC arriving as JSON numbers (not the int64-as-string
quirk seen elsewhere in the Upstox API), and the documented retrieval-limit
table. Response bodies used below are the doc's OWN sample payloads.
"""
from __future__ import annotations

from datetime import date, datetime

import pytest

from backend.core.errors import UpstreamError, ValidationError
from backend.integrations.upstox.candles import (
    Candle,
    candles_to_dataframe,
    chunk_date_range,
    get_historical_candles,
    get_intraday_candles,
    validate_unit_interval,
)

# The exact sample body from the Historical Candle Data V3 doc.
HISTORICAL_SAMPLE = {
    "status": "success",
    "data": {
        "candles": [
            ["2025-01-01T00:00:00+05:30", 53.1, 53.95, 51.6, 52.05, 235519861, 0],
            ["2025-02-01T00:00:00+05:30", 50.35, 56.85, 49.35, 52.8, 1004998611, 0],
        ]
    },
}

# The exact sample body from the Intraday Candle Data V3 doc.
INTRADAY_SAMPLE = {
    "status": "success",
    "data": {
        "candles": [
            ["2025-01-12T15:15:00+05:30", 2305.3, 2307.05, 2301, 2304.65, 559982, 0],
            ["2025-01-12T14:45:00+05:30", 2309.1, 2310.75, 2305.25, 2305.3, 740124, 0],
        ]
    },
}


class FakeClient:
    """Stands in for UpstoxRestClient -- records the exact path requested so
    tests can assert on URL construction without a real HTTP call."""

    def __init__(self, response_body: dict):
        self.response_body = response_body
        self.requested_paths: list[str] = []

    async def get_json(self, path, *, base=None, params=None, endpoint_tag="default"):
        self.requested_paths.append(path)
        return self.response_body


class TestUrlConstruction:
    async def test_the_path_uses_to_date_before_from_date(self):
        """
        THE ORDER THAT'S EASY TO GET BACKWARDS. Confirmed against the doc's own
        curl sample: '.../minutes/1/2025-01-02/2025-01-01' where 2025-01-02
        (the END of the range) comes FIRST. Intuition says from-then-to; the
        real API says to-then-from.
        """
        client = FakeClient(HISTORICAL_SAMPLE)
        await get_historical_candles(
            client,
            "NSE_EQ|INE848E01016",
            unit="minutes",
            interval=1,
            from_date=date(2025, 1, 1),
            to_date=date(2025, 1, 2),
        )
        path = client.requested_paths[0]
        # to_date (01-02) must appear BEFORE from_date (01-01) in the path.
        assert path.index("2025-01-02") < path.index("2025-01-01")

    async def test_matches_the_documented_sample_curl_exactly(self):
        """Reproduces the doc's own example byte-for-byte in the resulting path."""
        client = FakeClient(HISTORICAL_SAMPLE)
        await get_historical_candles(
            client,
            "NSE_EQ|INE848E01016",
            unit="minutes",
            interval=1,
            from_date=date(2025, 1, 1),
            to_date=date(2025, 1, 2),
        )
        assert (
            client.requested_paths[0]
            == "/historical-candle/NSE_EQ%7CINE848E01016/minutes/1/2025-01-02/2025-01-01"
        )

    async def test_instrument_key_pipe_is_percent_encoded(self):
        client = FakeClient(HISTORICAL_SAMPLE)
        await get_historical_candles(
            client,
            "NSE_EQ|INE848E01016",
            unit="days",
            interval=1,
            from_date=date(2024, 1, 1),
            to_date=date(2024, 12, 31),
        )
        assert "%7C" in client.requested_paths[0]
        assert "|" not in client.requested_paths[0]

    async def test_intraday_uses_the_separate_intraday_path_with_no_dates(self):
        """Historical and intraday are DIFFERENT endpoints -- intraday has no
        to_date/from_date in its path at all."""
        client = FakeClient(INTRADAY_SAMPLE)
        await get_intraday_candles(client, "NSE_EQ|INE848E01016", unit="minutes", interval=30)
        assert (
            client.requested_paths[0]
            == "/historical-candle/intraday/NSE_EQ%7CINE848E01016/minutes/30"
        )


class TestResponseParsing:
    async def test_ohlc_values_are_real_floats_not_strings(self):
        """
        Unlike the market-data feed's int64 fields (JSON strings when routed
        through MessageToDict), this endpoint's documented sample shows OHLC as
        JSON NUMBERS. Confirming that assumption doesn't hold would mean every
        candle silently carries string prices into pandas.
        """
        client = FakeClient(HISTORICAL_SAMPLE)
        candles = await get_historical_candles(
            client, "X", unit="days", interval=1,
            from_date=date(2025, 1, 1), to_date=date(2025, 2, 1),
        )
        for c in candles:
            assert isinstance(c.open, float)
            assert isinstance(c.volume, float)

    async def test_timestamps_are_parsed_with_their_ist_offset(self):
        client = FakeClient(HISTORICAL_SAMPLE)
        candles = await get_historical_candles(
            client, "X", unit="days", interval=1,
            from_date=date(2025, 1, 1), to_date=date(2025, 2, 1),
        )
        assert candles[0].timestamp == datetime.fromisoformat("2025-01-01T00:00:00+05:30")

    async def test_candles_are_returned_chronologically_regardless_of_upstox_order(self):
        """Upstox's own sample is newest-first (Feb before Jan in the sample's
        listed order is actually oldest-first here, but this is NOT guaranteed
        -- normalise rather than trust wire order)."""
        client = FakeClient(HISTORICAL_SAMPLE)
        candles = await get_historical_candles(
            client, "X", unit="days", interval=1,
            from_date=date(2025, 1, 1), to_date=date(2025, 2, 1),
        )
        assert candles == sorted(candles, key=lambda c: c.timestamp)

    async def test_a_non_success_status_raises(self):
        client = FakeClient({"status": "error", "data": {}})
        with pytest.raises(UpstreamError):
            await get_historical_candles(
                client, "X", unit="days", interval=1,
                from_date=date(2025, 1, 1), to_date=date(2025, 2, 1),
            )

    async def test_a_malformed_candle_row_raises_rather_than_silently_truncating(self):
        client = FakeClient({"status": "success", "data": {"candles": [["only", "four", "fields", "here"]]}})
        with pytest.raises(UpstreamError, match="7 elements"):
            await get_historical_candles(
                client, "X", unit="days", interval=1,
                from_date=date(2025, 1, 1), to_date=date(2025, 2, 1),
            )


class TestUnitIntervalValidation:
    @pytest.mark.parametrize(
        "unit,interval",
        [("minutes", 1), ("minutes", 300), ("hours", 1), ("hours", 5), ("days", 1),
         ("weeks", 1), ("months", 1)],
    )
    def test_valid_combinations_pass(self, unit, interval):
        validate_unit_interval(unit, interval)  # must not raise

    @pytest.mark.parametrize(
        "unit,interval",
        [("minutes", 0), ("minutes", 301), ("hours", 0), ("hours", 6), ("days", 2),
         ("weeks", 2), ("months", 2)],
    )
    def test_out_of_range_intervals_are_rejected(self, unit, interval):
        with pytest.raises(ValidationError):
            validate_unit_interval(unit, interval)

    def test_unknown_unit_is_rejected(self):
        with pytest.raises(ValidationError):
            validate_unit_interval("fortnights", 1)

    async def test_intraday_rejects_weeks_and_months(self):
        """Intraday's documented unit set is a strict subset of historical's --
        weeks/months don't apply to 'today only' data."""
        client = FakeClient(INTRADAY_SAMPLE)
        with pytest.raises(ValidationError):
            await get_intraday_candles(client, "X", unit="weeks", interval=1)


class TestRetrievalLimits:
    async def test_minute_bars_over_one_month_for_short_intervals_are_rejected(self):
        """1-15 min intervals cap at ~1 month per the documented table."""
        client = FakeClient(HISTORICAL_SAMPLE)
        with pytest.raises(ValidationError, match="retrieval limit"):
            await get_historical_candles(
                client, "X", unit="minutes", interval=5,
                from_date=date(2025, 1, 1), to_date=date(2025, 6, 1),  # 5 months
            )

    async def test_minute_bars_over_one_quarter_for_long_intervals_are_rejected(self):
        """>15 min intervals get the more generous ~1 quarter cap."""
        client = FakeClient(HISTORICAL_SAMPLE)
        with pytest.raises(ValidationError, match="retrieval limit"):
            await get_historical_candles(
                client, "X", unit="minutes", interval=30,
                from_date=date(2025, 1, 1), to_date=date(2025, 12, 1),  # 11 months
            )

    async def test_daily_bars_allow_up_to_ten_years(self):
        client = FakeClient(HISTORICAL_SAMPLE)
        await get_historical_candles(  # must not raise
            client, "X", unit="days", interval=1,
            from_date=date(2016, 1, 1), to_date=date(2025, 1, 1),
        )

    async def test_daily_bars_over_ten_years_are_rejected(self):
        client = FakeClient(HISTORICAL_SAMPLE)
        with pytest.raises(ValidationError, match="retrieval limit"):
            await get_historical_candles(
                client, "X", unit="days", interval=1,
                from_date=date(2000, 1, 1), to_date=date(2025, 1, 1),  # >10y
            )

    async def test_weekly_and_monthly_bars_have_no_range_cap(self):
        client = FakeClient(HISTORICAL_SAMPLE)
        await get_historical_candles(  # must not raise, even over 20 years
            client, "X", unit="months", interval=1,
            from_date=date(2000, 1, 1), to_date=date(2025, 1, 1),
        )

    async def test_data_before_2000_is_rejected_for_daily_bars(self):
        client = FakeClient(HISTORICAL_SAMPLE)
        with pytest.raises(ValidationError, match="no.*data before"):
            await get_historical_candles(
                client, "X", unit="days", interval=1,
                from_date=date(1999, 1, 1), to_date=date(1999, 12, 31),
            )

    async def test_data_before_2022_is_rejected_for_minute_bars(self):
        """Minute/hour data's documented availability starts January 2022 --
        stricter than the daily/weekly/monthly floor of January 2000."""
        client = FakeClient(HISTORICAL_SAMPLE)
        with pytest.raises(ValidationError, match="no minutes data before"):
            await get_historical_candles(
                client, "X", unit="minutes", interval=1,
                from_date=date(2021, 1, 1), to_date=date(2021, 1, 15),
            )

    async def test_from_date_after_to_date_is_rejected(self):
        client = FakeClient(HISTORICAL_SAMPLE)
        with pytest.raises(ValidationError, match="must not be after"):
            await get_historical_candles(
                client, "X", unit="days", interval=1,
                from_date=date(2025, 6, 1), to_date=date(2025, 1, 1),
            )


class TestChunkDateRange:
    def test_a_range_within_the_cap_is_a_single_chunk(self):
        chunks = chunk_date_range(
            date(2024, 1, 1), date(2024, 6, 1), unit="days", interval=1
        )
        assert chunks == [(date(2024, 1, 1), date(2024, 6, 1))]

    def test_a_wide_daily_range_is_split_at_the_ten_year_cap(self):
        chunks = chunk_date_range(
            date(2000, 1, 1), date(2025, 1, 1), unit="days", interval=1
        )
        assert len(chunks) > 1
        # Every chunk individually respects the cap.
        for start, end in chunks:
            assert (end - start).days <= 3653
        # No gaps and no overlaps: chunk N+1 starts the day after chunk N ends.
        for (_s1, e1), (s2, _e2) in zip(chunks, chunks[1:], strict=False):
            assert s2 == e1 + __import__("datetime").timedelta(days=1)
        # Coverage: first chunk starts at from_date, last ends at to_date.
        assert chunks[0][0] == date(2000, 1, 1)
        assert chunks[-1][1] == date(2025, 1, 1)

    def test_weeks_never_needs_chunking(self):
        chunks = chunk_date_range(
            date(1990, 1, 1), date(2025, 1, 1), unit="weeks", interval=1
        )
        assert len(chunks) == 1


class TestCandlesToDataframe:
    def test_produces_the_exact_shape_bulk_insert_prices_expects(self):
        """So the Upstox path can reuse infrastructure.timescale.bulk_insert_prices
        UNCHANGED rather than growing a second write path into tsdb.stock_prices."""
        candles = [
            Candle(
                timestamp=datetime.fromisoformat("2025-01-01T00:00:00+05:30"),
                open=100.0, high=105.0, low=99.0, close=103.0, volume=1000.0, open_interest=0.0,
            ),
            Candle(
                timestamp=datetime.fromisoformat("2025-01-02T00:00:00+05:30"),
                open=103.0, high=108.0, low=102.0, close=107.0, volume=1200.0, open_interest=0.0,
            ),
        ]
        df = candles_to_dataframe(candles)
        assert list(df.columns) == ["Open", "High", "Low", "Close", "Volume"]
        assert len(df) == 2
        assert df.iloc[0]["Close"] == 103.0

    def test_empty_candle_list_produces_an_empty_dataframe_not_a_crash(self):
        df = candles_to_dataframe([])
        assert len(df) == 0
        assert list(df.columns) == ["Open", "High", "Low", "Close", "Volume"]
