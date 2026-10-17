"""
TimescaleDB writers for the market-data time series.

HARD CONTRACT: `data_loaders/yfinance_pipeline.py::store_to_timescaledb_batch`
imports and calls exactly:

    bulk_insert_prices(ticker, df) -> int                  # rows written
    bulk_insert_corporate_actions(actions) -> int          # rows written

`df` arrives straight out of `clean_ohlcv()`: a DatetimeIndex plus
*capitalised* yfinance columns (Open/High/Low/Close/Volume). `actions` is a
list of {ticker, date, action_type, value}.

Two details that matter and are easy to get wrong:

1. `daily_return` is written here, not by the caller. The read side
   (`analytics/duckdb_client.py::load_prices_from_timescale`) SELECTs a
   `daily_return` column straight out of `tsdb.stock_prices`. If we only
   stored OHLCV, every analytics query would come back with a NULL return
   column and silently poison the covariance matrix. We compute it as a log
   return, matching `market_data._synthetic_returns` and `risk_metrics`,
   which both work in log space.

2. Writes are idempotent (ON CONFLICT DO NOTHING against the (ticker, date)
   primary key). The Celery refresh job re-downloads overlapping windows on
   every run; without this, a nightly job would either crash on the second
   run or double-count rows.

These are sync functions on purpose -- they are called from Celery workers and
from the sync yfinance pipeline, never from the request path.
"""
from __future__ import annotations

import logging
from datetime import date
from typing import Any

import pandas as pd
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine

from backend.core.config import settings

logger = logging.getLogger(__name__)

_engine: Engine | None = None

# Timescale hypertables live in the `tsdb` schema, which is what the DuckDB
# analytics reader qualifies its queries with (tsdb.stock_prices, etc).
SCHEMA = "tsdb"

# Values for stock_prices.source. 'unknown' is deliberately not a synonym for
# yfinance: rows predating the column genuinely have unproven provenance, and
# labelling them retroactively would be a guess presented as a fact.
SOURCE_UPSTOX = "upstox"
SOURCE_YFINANCE = "yfinance"
UNKNOWN_SOURCE = "unknown"

DDL = f"""
CREATE SCHEMA IF NOT EXISTS {SCHEMA};

CREATE TABLE IF NOT EXISTS {SCHEMA}.stock_prices (
    ticker        TEXT        NOT NULL,
    date          DATE        NOT NULL,
    open          DOUBLE PRECISION,
    high          DOUBLE PRECISION,
    low           DOUBLE PRECISION,
    close         DOUBLE PRECISION NOT NULL,
    volume        BIGINT,
    daily_return  DOUBLE PRECISION,
    -- WHICH feed this bar came from ('upstox', 'yfinance', ...). Two
    -- pipelines write into this table; without this column an Upstox bar and
    -- a yfinance bar are indistinguishable once stored, and the platform
    -- loses the provenance it is otherwise careful to state everywhere else
    -- (see core/assumptions.py). 'unknown' is the honest label for rows
    -- written before this column existed -- not a guess at which feed it was.
    source        TEXT        NOT NULL DEFAULT 'unknown',
    PRIMARY KEY (ticker, date)
);

CREATE TABLE IF NOT EXISTS {SCHEMA}.fund_nav_history (
    scheme_id  TEXT  NOT NULL,
    date       DATE  NOT NULL,
    nav        DOUBLE PRECISION NOT NULL,
    PRIMARY KEY (scheme_id, date)
);

CREATE TABLE IF NOT EXISTS {SCHEMA}.corporate_actions (
    ticker       TEXT NOT NULL,
    date         DATE NOT NULL,
    action_type  TEXT NOT NULL,
    value        DOUBLE PRECISION NOT NULL,
    PRIMARY KEY (ticker, date, action_type)
);

ALTER TABLE {SCHEMA}.stock_prices
    ADD COLUMN IF NOT EXISTS source TEXT NOT NULL DEFAULT 'unknown';

CREATE INDEX IF NOT EXISTS idx_stock_prices_date ON {SCHEMA}.stock_prices (date DESC);
CREATE INDEX IF NOT EXISTS idx_fund_nav_date     ON {SCHEMA}.fund_nav_history (date DESC);
"""

# Converting a plain table to a hypertable is a no-op if it already is one, but
# it fails outright if the timescaledb extension is absent (e.g. plain Postgres
# in CI). That must not be fatal -- the tables work fine either way, they just
# lose automatic time-partitioning.
HYPERTABLE_SQL = [
    f"SELECT create_hypertable('{SCHEMA}.stock_prices', 'date', if_not_exists => TRUE, migrate_data => TRUE);",
    f"SELECT create_hypertable('{SCHEMA}.fund_nav_history', 'date', if_not_exists => TRUE, migrate_data => TRUE);",
]


def get_engine() -> Engine:
    global _engine
    if _engine is None:
        _engine = create_engine(
            settings.sync_database_url,
            pool_pre_ping=True,
            pool_size=5,
            max_overflow=5,
            future=True,
        )
    return _engine


def init_timescale() -> None:
    """Create schema/tables/hypertables. Idempotent; safe to call at boot."""
    engine = get_engine()
    with engine.begin() as conn:
        conn.execute(text("CREATE EXTENSION IF NOT EXISTS timescaledb CASCADE;"))
    with engine.begin() as conn:
        for stmt in DDL.strip().split(";\n\n"):
            if stmt.strip():
                conn.execute(text(stmt))
    for sql in HYPERTABLE_SQL:
        try:
            with engine.begin() as conn:
                conn.execute(text(sql))
        except Exception as e:
            logger.info(
                "Skipping hypertable conversion (%s). Plain Postgres tables will "
                "be used -- correct, just not time-partitioned.",
                type(e).__name__,
            )


def _normalise_price_frame(
    ticker: str, df: pd.DataFrame, source: str = UNKNOWN_SOURCE
) -> list[dict[str, Any]]:
    """yfinance OHLCV frame -> row dicts matching tsdb.stock_prices."""
    if df is None or df.empty:
        return []

    out = df.copy()
    # clean_ohlcv leaves yfinance's capitalised names; be tolerant of either.
    out.columns = [str(c).lower() for c in out.columns]
    if "close" not in out.columns:
        logger.error("Price frame for %s has no close column: %s", ticker, list(out.columns))
        return []

    out = out[~out.index.duplicated(keep="last")].sort_index()

    # Written here, not by the caller -- the analytics read path SELECTs this
    # column directly. Log returns, to match the rest of the stack.
    import numpy as np

    out["daily_return"] = np.log(out["close"] / out["close"].shift(1))

    rows: list[dict[str, Any]] = []
    for idx, r in out.iterrows():
        d = idx.date() if hasattr(idx, "date") else idx
        close = r.get("close")
        if close is None or pd.isna(close):
            continue

        def num(col: str, row=r) -> float | None:
            # `row=r` binds the current row explicitly; closing over the loop
            # variable would make every call read the LAST row once the loop
            # advanced (B023).
            v = row.get(col)
            return None if v is None or pd.isna(v) else float(v)

        dr = num("daily_return")
        vol = num("volume")
        rows.append(
            {
                "ticker": ticker,
                "date": d,
                "open": num("open"),
                "high": num("high"),
                "low": num("low"),
                "close": float(close),
                "volume": int(vol) if vol is not None else None,
                # First row of a series has no prior close -> NaN, which is not
                # valid JSON/SQL. Store NULL rather than letting NaN through.
                "daily_return": dr,
                "source": source,
            }
        )
    return rows


def bulk_insert_prices(
    ticker: str, df: pd.DataFrame, *, source: str = UNKNOWN_SOURCE
) -> int:
    """
    Idempotently upsert one ticker's OHLCV history. Returns rows written.

    `source` records WHICH feed produced these bars -- SOURCE_UPSTOX or
    SOURCE_YFINANCE. It is keyword-only and defaults to 'unknown' so the
    pre-existing two-positional-argument contract named in this module's
    docstring still holds for any caller that hasn't been updated.

    ON CONFLICT stays DO NOTHING, which means the FIRST feed to store a given
    (ticker, date) owns that bar and its source label. A later run from the
    other feed will not silently relabel history it did not write.
    """
    rows = _normalise_price_frame(ticker, df, source)
    if not rows:
        return 0

    sql = text(
        f"""
        INSERT INTO {SCHEMA}.stock_prices
            (ticker, date, open, high, low, close, volume, daily_return, source)
        VALUES
            (:ticker, :date, :open, :high, :low, :close, :volume, :daily_return, :source)
        ON CONFLICT (ticker, date) DO NOTHING
        """
    )
    with get_engine().begin() as conn:
        result = conn.execute(sql, rows)
    written = result.rowcount if result.rowcount and result.rowcount > 0 else 0
    logger.info(
        "stock_prices: %s -> %d/%d new rows (source=%s)", ticker, written, len(rows), source
    )
    return written


def read_prices(
    ticker: str, *, start: date | None = None
) -> pd.DataFrame | None:
    """
    Read one ticker's stored daily history back out of `tsdb.stock_prices`.

    Returns a frame indexed by date with EXACTLY the columns the parquet cache
    and `market_data._fetch_from_yfinance` already produce -- Close, Return --
    plus Source, so a caller can state where the bars came from. Returns None
    (never raises, never an empty frame) when there is nothing usable, so the
    caller can simply fall through to its next source.

    This is the read half of the pipeline whose write half is
    `bulk_insert_prices`. Before it existed, both the yfinance pipeline and
    the Upstox candle backfill wrote into this table and NOTHING in the
    serving path ever read it -- the engines went to parquet/yfinance/synthetic
    instead, so stored bars could not reach a PLOS score or an ARISE run.

    Sync on purpose, like every other function here: it is called from the
    engines, which already run on worker threads (anyio.to_thread), never on
    the event loop.
    """
    # The analytics tables are Postgres-only. Under the SQLite test DSN there
    # is no tsdb schema to read, and attempting the query would just raise per
    # call -- cheaper and quieter to decline up front.
    if not settings.sync_database_url.startswith("postgresql"):
        return None

    sql = text(
        f"""
        SELECT date, close, daily_return, source
        FROM {SCHEMA}.stock_prices
        WHERE ticker = :ticker
          AND (:start IS NULL OR date >= :start)
          AND close IS NOT NULL
        ORDER BY date
        """
    )
    try:
        with get_engine().connect() as conn:
            df = pd.read_sql(sql, conn, params={"ticker": ticker, "start": start})
    except Exception as e:
        # A missing table, an unreachable database, a bad DSN: all mean the
        # same thing to the caller (no stored history), and none of them
        # should be fatal when a live fetch is still available.
        logger.debug("read_prices(%s) unavailable: %s: %s", ticker, type(e).__name__, e)
        return None

    if df.empty:
        return None

    out = pd.DataFrame(
        {
            "Close": df["close"].astype(float).to_numpy(),
            "Return": df["daily_return"].astype(float).to_numpy(),
            "Source": df["source"].astype(str).to_numpy(),
        },
        index=pd.DatetimeIndex(pd.to_datetime(df["date"])),
    )
    # The first bar of any series has no prior close, so its return is NULL by
    # construction. Dropping here matches what the yfinance fetcher does, and
    # keeps a NaN out of the covariance matrix.
    out = out.dropna(subset=["Close", "Return"])
    return out if not out.empty else None


def bulk_insert_corporate_actions(actions: list[dict[str, Any]]) -> int:
    """Idempotently upsert dividends/splits. Returns rows written."""
    if not actions:
        return 0
    sql = text(
        f"""
        INSERT INTO {SCHEMA}.corporate_actions (ticker, date, action_type, value)
        VALUES (:ticker, :date, :action_type, :value)
        ON CONFLICT (ticker, date, action_type) DO NOTHING
        """
    )
    with get_engine().begin() as conn:
        result = conn.execute(sql, actions)
    return result.rowcount if result.rowcount and result.rowcount > 0 else 0


def bulk_insert_nav(scheme_id: str, records: list[dict[str, Any]]) -> int:
    """
    Idempotently upsert AMFI NAV history for one scheme.
    `records` are {date, nav} (or NAVRecord._asdict()).
    """
    if not records:
        return 0
    rows = [
        {"scheme_id": scheme_id, "date": r["date"], "nav": float(r["nav"])}
        for r in records
        if r.get("nav") is not None
    ]
    if not rows:
        return 0
    sql = text(
        f"""
        INSERT INTO {SCHEMA}.fund_nav_history (scheme_id, date, nav)
        VALUES (:scheme_id, :date, :nav)
        ON CONFLICT (scheme_id, date) DO UPDATE SET nav = EXCLUDED.nav
        """
    )
    with get_engine().begin() as conn:
        result = conn.execute(sql, rows)
    return result.rowcount if result.rowcount and result.rowcount > 0 else 0
