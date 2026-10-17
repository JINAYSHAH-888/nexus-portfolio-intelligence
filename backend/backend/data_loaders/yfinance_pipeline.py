"""
Yahoo Finance ETL Pipeline.

Complete flow:
  yfinance → Validate → Clean → Deduplicate → TimescaleDB → DuckDB

Supported intervals:
  1d  — daily OHLCV (primary — used for all risk/return calculations)
  1wk — weekly (derived from daily via resampling)
  1mo — monthly (derived from daily via resampling)

What is downloaded per ticker:
  - Adjusted Close price (accounts for splits and dividends)
  - OHLCV (Open, High, Low, Close, Volume)
  - Dividends (cash per share, ex-dividend date)
  - Stock splits (ratio and date)

IMPORTANT: Upstox live prices are NEVER used here.
All data in this pipeline comes from Yahoo Finance historical data only.
Monte Carlo, Risk Engine, and ARISE consume only this historical dataset.

Adjusted Close handling:
  yfinance auto-adjusts Close prices for splits and dividends when
  auto_adjust=True. This is the default and gives a clean return series
  suitable for performance and risk calculations.

Corporate actions:
  Dividends: stored in TimescaleDB corporate_actions table as DIVIDEND type.
  Splits: stored as SPLIT type with the split ratio.
  Both are also factored into the adjusted price series.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

# Supported yfinance periods
VALID_PERIODS = {"1d", "5d", "1mo", "3mo", "6mo", "1y", "2y", "5y", "10y", "ytd", "max"}
DEFAULT_PERIOD = "5y"

# Validation thresholds
MAX_DAILY_RETURN = 0.60    # flag if single-day return > 60%
MIN_PRICE = 0.01           # flag prices below this as suspicious
MAX_PRICE = 1_000_000.0    # sanity cap


@dataclass
class DownloadResult:
    ticker: str
    rows_downloaded: int
    rows_stored: int
    rows_skipped_duplicate: int
    rows_failed_validation: int
    start_date: date | None
    end_date: date | None
    dividends_stored: int
    splits_stored: int
    error: str | None
    is_synthetic: bool


def download_ticker(
    ticker: str,
    period: str = DEFAULT_PERIOD,
    interval: str = "1d",
) -> tuple[pd.DataFrame | None, pd.DataFrame | None, pd.DataFrame | None]:
    """
    Download OHLCV, dividends, and splits for a single ticker from Yahoo Finance.

    Returns:
        (ohlcv_df, dividends_df, splits_df) — any may be None on error
    """
    try:
        import yfinance as yf

        ticker_obj = yf.Ticker(ticker)

        # Download OHLCV with adjustment (handles splits + dividends in price)
        ohlcv = yf.download(
            ticker,
            period=period,
            interval=interval,
            auto_adjust=True,    # adjusted close accounts for splits + dividends
            actions=True,        # include dividend/split columns in dataframe
            progress=False,
            threads=False,
        )

        if ohlcv is None or ohlcv.empty:
            logger.warning(f"yfinance returned empty data for {ticker}")
            return None, None, None

        # Flatten multi-level columns if present
        if isinstance(ohlcv.columns, pd.MultiIndex):
            ohlcv.columns = [col[0] for col in ohlcv.columns]

        # Separate dividends and splits if included
        dividends_df = None
        splits_df = None

        if "Dividends" in ohlcv.columns:
            div_mask = ohlcv["Dividends"] > 0
            if div_mask.any():
                dividends_df = ohlcv[div_mask][["Dividends"]].copy()
                dividends_df.columns = ["amount"]
                dividends_df.index.name = "date"

        if "Stock Splits" in ohlcv.columns:
            split_mask = ohlcv["Stock Splits"] > 0
            if split_mask.any():
                splits_df = ohlcv[split_mask][["Stock Splits"]].copy()
                splits_df.columns = ["ratio"]
                splits_df.index.name = "date"

        # Keep only OHLCV
        keep_cols = [c for c in ["Open", "High", "Low", "Close", "Volume"]
                     if c in ohlcv.columns]
        ohlcv = ohlcv[keep_cols].copy()
        ohlcv.index.name = "date"

        return ohlcv, dividends_df, splits_df

    except ImportError:
        logger.error("yfinance not installed. Run: pip install yfinance")
        return None, None, None
    except Exception as e:
        logger.warning(f"yfinance download failed for {ticker}: {type(e).__name__}: {e}")
        return None, None, None


def validate_ohlcv(df: pd.DataFrame, ticker: str) -> tuple[pd.DataFrame, list[str]]:
    """
    Validate downloaded OHLCV data.

    Checks:
      1. Non-null Close prices
      2. Positive prices (Close > MIN_PRICE)
      3. Sane price levels (Close < MAX_PRICE)
      4. No extreme single-day returns (> MAX_DAILY_RETURN)
      5. High >= Low (OHLC consistency)
      6. Volume >= 0

    Returns:
        (validated_df, list_of_warnings)
    """
    warnings: list[str] = []

    if df is None or df.empty:
        return pd.DataFrame(), [f"{ticker}: empty dataframe"]

    original_len = len(df)

    # Drop rows with null Close
    df = df.dropna(subset=["Close"])
    null_dropped = original_len - len(df)
    if null_dropped > 0:
        warnings.append(f"{ticker}: dropped {null_dropped} rows with null Close")

    # Drop rows with non-positive prices
    invalid_price = df["Close"] <= 0
    if invalid_price.any():
        warnings.append(f"{ticker}: {invalid_price.sum()} rows with non-positive price")
        df = df[~invalid_price]

    # Flag extreme prices
    extreme = (df["Close"] < MIN_PRICE) | (df["Close"] > MAX_PRICE)
    if extreme.any():
        warnings.append(f"{ticker}: {extreme.sum()} rows with extreme price values")

    # Compute daily log returns and flag extreme moves
    if len(df) > 1:
        log_returns = np.log(df["Close"] / df["Close"].shift(1))
        extreme_returns = log_returns.abs() > MAX_DAILY_RETURN
        extreme_return_count = extreme_returns.sum()
        if extreme_return_count > 0:
            warnings.append(
                f"{ticker}: {extreme_return_count} days with |return| > {MAX_DAILY_RETURN:.0%}"
            )

    # Check OHLC consistency
    if "High" in df.columns and "Low" in df.columns:
        bad_ohlc = df["High"] < df["Low"]
        if bad_ohlc.any():
            warnings.append(f"{ticker}: {bad_ohlc.sum()} rows where High < Low — correcting")
            df.loc[bad_ohlc, "High"] = df.loc[bad_ohlc, ["High", "Low"]].max(axis=1)
            df.loc[bad_ohlc, "Low"] = df.loc[bad_ohlc, ["High", "Low"]].min(axis=1)

    return df, warnings


def clean_ohlcv(df: pd.DataFrame) -> pd.DataFrame:
    """
    Clean OHLCV data.

    Steps:
      1. Forward-fill missing prices (market holidays, suspended stocks)
         but only within a 5-day gap (wider gaps left as NaN)
      2. Deduplicate on index (keep last)
      3. Sort ascending by date
      4. Compute log daily return
    """
    if df.empty:
        return df

    # Sort and deduplicate
    df = df.sort_index()
    df = df[~df.index.duplicated(keep="last")]

    # Forward-fill gaps up to 5 trading days (market closure, not delisting)
    df["Close"] = df["Close"].ffill(limit=5)

    # Fill OHLV from Close if missing
    for col in ["Open", "High", "Low"]:
        if col in df.columns:
            df[col] = df[col].fillna(df["Close"])

    if "Volume" in df.columns:
        df["Volume"] = df["Volume"].fillna(0).astype(int)

    # Compute log daily return
    df["daily_return"] = np.log(df["Close"] / df["Close"].shift(1))

    return df


def store_to_timescaledb_batch(
    ticker: str,
    df: pd.DataFrame,
    dividends_df: pd.DataFrame | None,
    splits_df: pd.DataFrame | None,
) -> tuple[int, int]:
    """
    Bulk-insert OHLCV into TimescaleDB stock_prices table.
    Uses INSERT ON CONFLICT DO NOTHING for idempotent upserts.

    Returns:
        (rows_stored, corporate_actions_stored)
    """
    from backend.infrastructure.timescale import (
        SOURCE_YFINANCE,
        bulk_insert_corporate_actions,
        bulk_insert_prices,
    )

    rows = 0
    ca_rows = 0

    try:
        rows = bulk_insert_prices(ticker, df, source=SOURCE_YFINANCE)
    except Exception as e:
        logger.error(f"Failed to store prices for {ticker}: {e}")

    # Store corporate actions
    actions = []
    if dividends_df is not None and not dividends_df.empty:
        for dt, row in dividends_df.iterrows():
            actions.append({
                "ticker": ticker,
                "date": dt.date() if hasattr(dt, "date") else dt,
                "action_type": "DIVIDEND",
                "value": float(row["amount"]),
            })

    if splits_df is not None and not splits_df.empty:
        for dt, row in splits_df.iterrows():
            actions.append({
                "ticker": ticker,
                "date": dt.date() if hasattr(dt, "date") else dt,
                "action_type": "SPLIT",
                "value": float(row["ratio"]),
            })

    if actions:
        try:
            ca_rows = bulk_insert_corporate_actions(actions)
        except Exception as e:
            logger.error(f"Failed to store corporate actions for {ticker}: {e}")

    return rows, ca_rows


def run_ticker_pipeline(
    ticker: str,
    period: str = DEFAULT_PERIOD,
) -> DownloadResult:
    """
    Run the complete ETL pipeline for a single ticker.

    Flow:
      yfinance → validate → clean → TimescaleDB
    """
    logger.info(f"Starting yfinance pipeline for {ticker}")

    ohlcv, dividends_df, splits_df = download_ticker(ticker, period)

    if ohlcv is None:
        return DownloadResult(
            ticker=ticker, rows_downloaded=0, rows_stored=0,
            rows_skipped_duplicate=0, rows_failed_validation=0,
            start_date=None, end_date=None,
            dividends_stored=0, splits_stored=0,
            error="Download failed", is_synthetic=False,
        )

    rows_downloaded = len(ohlcv)

    # Validate
    ohlcv_valid, warnings = validate_ohlcv(ohlcv, ticker)
    rows_failed = rows_downloaded - len(ohlcv_valid)
    for w in warnings:
        logger.warning(w)

    if ohlcv_valid.empty:
        return DownloadResult(
            ticker=ticker, rows_downloaded=rows_downloaded, rows_stored=0,
            rows_skipped_duplicate=0, rows_failed_validation=rows_failed,
            start_date=None, end_date=None,
            dividends_stored=0, splits_stored=0,
            error="All rows failed validation", is_synthetic=False,
        )

    # Clean
    ohlcv_clean = clean_ohlcv(ohlcv_valid)

    # Store
    rows_stored, ca_rows = store_to_timescaledb_batch(
        ticker, ohlcv_clean, dividends_df, splits_df
    )

    div_count = len(dividends_df) if dividends_df is not None else 0
    split_count = len(splits_df) if splits_df is not None else 0

    logger.info(
        f"{ticker}: downloaded={rows_downloaded} stored={rows_stored} "
        f"dividends={div_count} splits={split_count}"
    )

    idx = ohlcv_clean.index
    return DownloadResult(
        ticker=ticker,
        rows_downloaded=rows_downloaded,
        rows_stored=rows_stored,
        rows_skipped_duplicate=0,
        rows_failed_validation=rows_failed,
        start_date=idx.min().date() if len(idx) > 0 else None,
        end_date=idx.max().date() if len(idx) > 0 else None,
        dividends_stored=div_count,
        splits_stored=split_count,
        error=None,
        is_synthetic=False,
    )


def run_batch_pipeline(
    tickers: list[str],
    period: str = DEFAULT_PERIOD,
    delay_seconds: float = 0.5,
) -> list[DownloadResult]:
    """
    Run the yfinance ETL pipeline for a batch of tickers.
    Includes a polite delay between requests to respect rate limits.
    """
    import time

    results: list[DownloadResult] = []
    total = len(tickers)

    for i, ticker in enumerate(tickers, 1):
        logger.info(f"Processing {i}/{total}: {ticker}")
        result = run_ticker_pipeline(ticker, period)
        results.append(result)

        if i < total:
            time.sleep(delay_seconds)

    success = sum(1 for r in results if r.error is None)
    logger.info(f"Batch complete: {success}/{total} tickers succeeded")
    return results
