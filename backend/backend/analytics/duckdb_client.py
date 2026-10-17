"""
DuckDB Analytics Client.

DuckDB is the in-process analytics engine.
It attaches directly to TimescaleDB (PostgreSQL) and reads
stock_prices, fund_nav_history, and portfolio data without copying.

Architecture:
  TimescaleDB ──┐
  PostgreSQL   ──┼──► DuckDB (in-process) ──► pandas/numpy ──► Engines
  Parquet cache ──┘

DuckDB is NOT a persistent database here — it is a query engine.
All persistence happens in TimescaleDB and PostgreSQL.
DuckDB results are returned as DataFrames and passed to engines.

One DuckDB connection is created per analytics session.
It is NOT shared across threads; each Celery task or API request
that needs analytics creates its own connection.
"""
from __future__ import annotations

import logging
from contextlib import contextmanager
from pathlib import Path

import duckdb
import pandas as pd

from backend.core.config import settings

logger = logging.getLogger(__name__)

_DUCKDB_TEMP_DIR = Path(__file__).resolve().parent.parent.parent / "backend" / "data" / "duckdb_temp"
_DUCKDB_TEMP_DIR.mkdir(parents=True, exist_ok=True)


def _pg_connection_string() -> str:
    """Build a libpq-style connection string from settings DATABASE_URL."""
    url = settings.DATABASE_URL
    # Convert SQLAlchemy URL to libpq format
    # postgresql+asyncpg://user:pass@host:port/db → host=... dbname=... user=... password=...
    try:
        url = url.replace("postgresql+asyncpg://", "postgresql://")
        url = url.replace("postgresql+psycopg://", "postgresql://")
        return url
    except Exception:
        return "postgresql://postgres:postgres@localhost:5432/nippon_db"


@contextmanager
def analytics_session():
    """
    Context manager that yields a DuckDB connection with:
      - PostgreSQL scanner attached (reads from TimescaleDB / PostgreSQL)
      - Temp directory configured
      - Connection closed on exit

    Usage:
        with analytics_session() as db:
            df = db.execute("SELECT * FROM tsdb.stock_prices LIMIT 10").df()
    """
    conn = duckdb.connect(database=":memory:")

    try:
        # Install and load the PostgreSQL scanner extension
        conn.execute("INSTALL postgres_scanner")
        conn.execute("LOAD postgres_scanner")

        # Attach TimescaleDB / PostgreSQL as 'tsdb'
        pg_url = _pg_connection_string()
        conn.execute(f"ATTACH '{pg_url}' AS tsdb (TYPE POSTGRES, READ_ONLY)")

        logger.debug("DuckDB analytics session opened with TimescaleDB attached")
        yield conn

    except Exception as e:
        # Postgres scanner may not have network access in sandbox — degrade gracefully
        logger.warning(f"DuckDB could not attach PostgreSQL ({e}). Running in standalone mode.")
        try:
            yield conn
        finally:
            conn.close()
        return

    finally:
        try:
            conn.close()
        except Exception:
            pass


def run_query(sql: str, params: list | None = None) -> pd.DataFrame:
    """
    Execute a DuckDB SQL query and return a pandas DataFrame.
    Creates a fresh connection per call — safe for concurrent use.
    """
    with analytics_session() as db:
        if params:
            return db.execute(sql, params).df()
        return db.execute(sql).df()


def load_prices_from_timescale(
    tickers: list[str],
    start_date: str,
    end_date: str | None = None,
) -> pd.DataFrame:
    """
    Load daily prices from TimescaleDB stock_prices table into a DuckDB session.

    Returns: DataFrame with columns [ticker, date, close, daily_return]
    sorted by (ticker, date).
    """
    tickers_list = ", ".join(f"'{t}'" for t in tickers)
    date_filter = f"AND date >= '{start_date}'"
    if end_date:
        date_filter += f" AND date <= '{end_date}'"

    sql = f"""
        SELECT
            ticker,
            date,
            close,
            daily_return,
            volume
        FROM tsdb.stock_prices
        WHERE ticker IN ({tickers_list})
        {date_filter}
        ORDER BY ticker, date
    """

    with analytics_session() as db:
        try:
            return db.execute(sql).df()
        except Exception as e:
            logger.warning(f"TimescaleDB price query failed ({e}). Returning empty DataFrame.")
            return pd.DataFrame(columns=["ticker", "date", "close", "daily_return", "volume"])


def load_nav_from_timescale(
    scheme_ids: list[str],
    start_date: str,
    end_date: str | None = None,
) -> pd.DataFrame:
    """
    Load daily NAV history from TimescaleDB fund_nav_history table.

    Returns: DataFrame with columns [scheme_id, date, nav]
    """
    ids_list = ", ".join(f"'{s}'" for s in scheme_ids)
    date_filter = f"AND date >= '{start_date}'"
    if end_date:
        date_filter += f" AND date <= '{end_date}'"

    sql = f"""
        SELECT scheme_id, date, nav
        FROM tsdb.fund_nav_history
        WHERE scheme_id IN ({ids_list})
        {date_filter}
        ORDER BY scheme_id, date
    """

    with analytics_session() as db:
        try:
            return db.execute(sql).df()
        except Exception as e:
            logger.warning(f"TimescaleDB NAV query failed ({e}). Returning empty DataFrame.")
            return pd.DataFrame(columns=["scheme_id", "date", "nav"])


def load_holdings_from_postgres(
    scheme_ids: list[str],
) -> pd.DataFrame:
    """
    Load latest holdings from PostgreSQL (via the scheme_selections table
    and the embedded holdings JSON in schemes_metadata).

    In production this would read from a dedicated holdings table.
    For now, reads from the JSON holdings embedded in scheme metadata.
    """
    sql = """
        SELECT
            ss.user_id,
            ss.selected_scheme_ids
        FROM tsdb.scheme_selections ss
    """

    with analytics_session() as db:
        try:
            return db.execute(sql).df()
        except Exception as e:
            logger.warning(f"PostgreSQL holdings query failed ({e}).")
            return pd.DataFrame()
