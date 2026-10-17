"""
Historical Candle Data V3 and Intraday Candle Data V3.

Every detail below is taken from the verified doc pages, not inferred:

    GET /v3/historical-candle/{instrument_key}/{unit}/{interval}/{to_date}/{from_date}
    GET /v3/historical-candle/intraday/{instrument_key}/{unit}/{interval}

TWO DETAILS THAT ARE EASY TO GET BACKWARDS
--------------------------------------------
1. `to_date` comes BEFORE `from_date` in the path -- confirmed against the
   sample curl: `.../minutes/1/2025-01-02/2025-01-01`, where 2025-01-02 (the
   END of the range) is listed first. The intuitive from-then-to ordering
   would silently swap the two and either 400 or (worse) silently return the
   wrong window if Upstox happens to tolerate a reversed range.
2. V3 splits `unit` (minutes/hours/days/weeks/months) from `interval` (a
   number within that unit's range) -- this is NOT the old v2 API's single
   `1minute`/`day`/`week` string. The two APIs are not interchangeable and
   this module only speaks V3.

RESPONSE SHAPE
---------------
`data.candles` is an array of 7-element arrays:
    [timestamp (ISO-8601 string), open, high, low, close, volume, open_interest]
OHLC/volume/OI are JSON NUMBERS here -- unlike the market-data feed's int64
fields, which the feed's own JSON path (if you ever went through
MessageToDict) renders as strings. Confirmed directly from the documented
sample body; no string-to-int coercion is needed for this endpoint.

RETRIEVAL LIMITS (enforced client-side, not just documented)
---------------------------------------------------------------
    1-15 min intervals   -> 1 month per request
    >15 min intervals    -> 1 quarter per request
    hours                 -> 1 quarter per request
    days                  -> 10 years per request
    weeks / months        -> unlimited

A request exceeding these is rejected BEFORE it reaches Upstox (a clear
ValidationError naming the limit) rather than being sent and getting a vague
4XX back -- the caller finds out why immediately, and we never burn a rate-limit
token on a request we already know is invalid.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from urllib.parse import quote

import pandas as pd

from backend.core.errors import UpstreamError, ValidationError
from backend.integrations.upstox.rest import BASE_URL_V3, UpstoxRestClient

VALID_UNITS = ("minutes", "hours", "days", "weeks", "months")

# (min_interval, max_interval) per unit, from the documented table.
_INTERVAL_RANGES: dict[str, tuple[int, int]] = {
    "minutes": (1, 300),
    "hours": (1, 5),
    "days": (1, 1),
    "weeks": (1, 1),
    "months": (1, 1),
}

# Max (to_date - from_date) per the documented retrieval-limit table. Minutes
# has two tiers depending on the interval itself, handled specially below.
_MAX_RANGE_DAYS: dict[str, int] = {
    "hours": 92,       # ~1 quarter
    "days": 3653,      # ~10 years
    # weeks / months: no limit -- omitted, meaning "unchecked" below.
}
_MINUTES_SHORT_INTERVAL_MAX_DAYS = 31   # 1-15 min -> ~1 month
_MINUTES_LONG_INTERVAL_MAX_DAYS = 92    # >15 min -> ~1 quarter

# Upstox's own minutes/hours history starts January 2022; days/weeks/months
# from January 2000. Requesting before this is a guaranteed empty/invalid
# response, worth catching before spending a rate-limit token on it.
_MINUTES_HOURS_AVAILABLE_FROM = date(2022, 1, 1)
_DAYS_WEEKS_MONTHS_AVAILABLE_FROM = date(2000, 1, 1)


def validate_unit_interval(unit: str, interval: int) -> None:
    if unit not in VALID_UNITS:
        raise ValidationError(f"Invalid unit '{unit}'. Must be one of {VALID_UNITS}.")
    lo, hi = _INTERVAL_RANGES[unit]
    if not (lo <= interval <= hi):
        raise ValidationError(
            f"Invalid interval {interval} for unit '{unit}'. Must be in [{lo}, {hi}]."
        )


def _validate_range(unit: str, interval: int, from_date: date, to_date: date) -> None:
    if from_date > to_date:
        raise ValidationError(f"from_date ({from_date}) must not be after to_date ({to_date}).")

    earliest = (
        _MINUTES_HOURS_AVAILABLE_FROM
        if unit in ("minutes", "hours")
        else _DAYS_WEEKS_MONTHS_AVAILABLE_FROM
    )
    if from_date < earliest:
        raise ValidationError(
            f"Upstox has no {unit} data before {earliest.isoformat()}; "
            f"from_date={from_date} is earlier."
        )

    span_days = (to_date - from_date).days

    if unit == "minutes":
        cap = (
            _MINUTES_SHORT_INTERVAL_MAX_DAYS
            if interval <= 15
            else _MINUTES_LONG_INTERVAL_MAX_DAYS
        )
    else:
        cap = _MAX_RANGE_DAYS.get(unit)  # None for weeks/months -> unlimited

    if cap is not None and span_days > cap:
        raise ValidationError(
            f"Requested range ({span_days} days) exceeds Upstox's retrieval "
            f"limit for unit='{unit}'"
            + (f", interval={interval}" if unit == "minutes" else "")
            + f" ({cap} days max). Split into multiple requests."
        )


@dataclass(frozen=True)
class Candle:
    timestamp: datetime  # tz-aware, IST offset as returned by Upstox
    open: float
    high: float
    low: float
    close: float
    volume: float
    open_interest: float


def _parse_candles(raw: list[list]) -> list[Candle]:
    candles = []
    for row in raw:
        if len(row) != 7:
            raise UpstreamError(
                f"Unexpected candle row shape from Upstox: expected 7 elements, got {len(row)}."
            )
        ts_str, o, h, low, c, vol, oi = row
        candles.append(
            Candle(
                timestamp=datetime.fromisoformat(ts_str),
                open=float(o),
                high=float(h),
                low=float(low),
                close=float(c),
                volume=float(vol),
                open_interest=float(oi),
            )
        )
    # Upstox's own samples are newest-first; normalise to chronological order
    # so downstream consumers (e.g. candles_to_dataframe) never have to guess.
    candles.sort(key=lambda c: c.timestamp)
    return candles


async def get_historical_candles(
    client: UpstoxRestClient,
    instrument_key: str,
    *,
    unit: str,
    interval: int,
    from_date: date,
    to_date: date,
) -> list[Candle]:
    validate_unit_interval(unit, interval)
    _validate_range(unit, interval, from_date, to_date)

    encoded_key = quote(instrument_key, safe="")
    # Path order is to_date THEN from_date -- confirmed against the sample
    # curl, and the opposite of what "from -> to" intuition suggests.
    path = (
        f"/historical-candle/{encoded_key}/{unit}/{interval}/"
        f"{to_date.isoformat()}/{from_date.isoformat()}"
    )

    body = await client.get_json(path, base=BASE_URL_V3, endpoint_tag="historical_candle")
    if body.get("status") != "success":
        raise UpstreamError(f"Upstox historical-candle returned status={body.get('status')!r}.")

    return _parse_candles(body.get("data", {}).get("candles", []))


async def get_intraday_candles(
    client: UpstoxRestClient, instrument_key: str, *, unit: str, interval: int
) -> list[Candle]:
    if unit not in ("minutes", "hours", "days"):
        # Intraday's documented unit set is a STRICT SUBSET of historical's --
        # no weeks/months, since "intraday" only ever spans one trading day.
        raise ValidationError(
            f"Invalid unit '{unit}' for intraday candles. Must be one of "
            f"('minutes', 'hours', 'days')."
        )
    validate_unit_interval(unit, interval)

    encoded_key = quote(instrument_key, safe="")
    path = f"/historical-candle/intraday/{encoded_key}/{unit}/{interval}"

    body = await client.get_json(path, base=BASE_URL_V3, endpoint_tag="intraday_candle")
    if body.get("status") != "success":
        raise UpstreamError(f"Upstox intraday-candle returned status={body.get('status')!r}.")

    return _parse_candles(body.get("data", {}).get("candles", []))


def candles_to_dataframe(candles: list[Candle]) -> pd.DataFrame:
    """
    Converts to the exact OHLCV DataFrame shape
    `infrastructure.timescale.bulk_insert_prices` already expects (capitalised
    columns, DatetimeIndex) -- so the Upstox path can call that EXISTING,
    already-idempotent, already-tested writer unmodified, rather than growing
    a second write path into tsdb.stock_prices.
    """
    if not candles:
        return pd.DataFrame(columns=["Open", "High", "Low", "Close", "Volume"])

    idx = pd.DatetimeIndex([c.timestamp for c in candles])
    return pd.DataFrame(
        {
            "Open": [c.open for c in candles],
            "High": [c.high for c in candles],
            "Low": [c.low for c in candles],
            "Close": [c.close for c in candles],
            "Volume": [c.volume for c in candles],
        },
        index=idx,
    )


def chunk_date_range(
    from_date: date, to_date: date, *, unit: str, interval: int
) -> list[tuple[date, date]]:
    """
    Splits a wide date range into Upstox-retrieval-limit-respecting chunks, so
    a caller wanting 5 years of daily bars (well within the 10y daily cap, but
    potentially over a MINUTE-level cap if reused for intraday backfill) gets
    back a sequence of valid sub-requests rather than one that would be
    rejected by `_validate_range`.
    """
    if unit == "minutes":
        cap = (
            _MINUTES_SHORT_INTERVAL_MAX_DAYS if interval <= 15 else _MINUTES_LONG_INTERVAL_MAX_DAYS
        )
    else:
        cap = _MAX_RANGE_DAYS.get(unit)

    if cap is None or (to_date - from_date).days <= cap:
        return [(from_date, to_date)]

    chunks: list[tuple[date, date]] = []
    chunk_start = from_date
    while chunk_start <= to_date:
        chunk_end = min(chunk_start + timedelta(days=cap), to_date)
        chunks.append((chunk_start, chunk_end))
        chunk_start = chunk_end + timedelta(days=1)
    return chunks
