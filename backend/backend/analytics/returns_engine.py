"""
Returns Engine — computes expected returns from TimescaleDB price history via DuckDB.

Methods:
  1. Simple historical mean (annualized)
  2. EWMA — exponentially weighted, recent data weighted higher
  3. Rolling return windows (1M, 3M, 6M, 1Y, 3Y, 5Y)
  4. Rolling volatility (annualized std dev of daily returns)
  5. Rolling Sharpe ratio

All calculations use historical data from TimescaleDB.
Live Upstox prices are NEVER used here.
"""
from __future__ import annotations

import logging
from datetime import date, timedelta

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

TRADING_DAYS = 252
# Resolved from real disclosed data, not hardcoded. See core/assumptions.py.
def _risk_free_rate() -> float:
    from backend.core.assumptions import resolve_risk_free_rate
    return resolve_risk_free_rate()


RISK_FREE_RATE = _risk_free_rate()


def compute_expected_returns(
    returns_df: pd.DataFrame,
    method: str = "ewma",
    span: int = 252,
) -> pd.Series:
    """
    Compute annualized expected returns for each ticker.

    Args:
        returns_df: Wide-format DataFrame (dates × tickers) of daily log returns
        method: 'simple' = historical mean | 'ewma' = exponentially weighted
        span: EWMA span in trading days (default 252 = 1 year half-life)

    Returns:
        pd.Series indexed by ticker, values = annualized expected return
    """
    if returns_df.empty:
        return pd.Series(dtype=float)

    if method == "ewma":
        # EWMA mean: recent returns get exponentially higher weight
        weighted = returns_df.ewm(span=span, adjust=False).mean()
        daily_expected = weighted.iloc[-1]
    else:
        # Simple historical mean
        daily_expected = returns_df.mean()

    # Annualize: compound daily to annual
    annualized = (1 + daily_expected) ** TRADING_DAYS - 1
    return annualized


def compute_rolling_volatility(
    returns_df: pd.DataFrame,
    window: int = 63,
) -> pd.DataFrame:
    """
    Compute rolling annualized volatility for each ticker.

    Args:
        returns_df: Wide-format DataFrame (dates × tickers) of daily log returns
        window: rolling window in trading days (63 = ~3 months)

    Returns:
        Wide-format DataFrame of annualized rolling volatility
    """
    rolling_std = returns_df.rolling(window=window, min_periods=int(window * 0.5)).std()
    return rolling_std * np.sqrt(TRADING_DAYS)


def compute_rolling_sharpe(
    returns_df: pd.DataFrame,
    window: int = 252,
) -> pd.DataFrame:
    """
    Compute rolling annualized Sharpe ratio for each ticker.

    Sharpe = (annualized_return - risk_free_rate) / annualized_volatility
    """
    rolling_mean = returns_df.rolling(window=window, min_periods=int(window * 0.5)).mean()
    rolling_std = returns_df.rolling(window=window, min_periods=int(window * 0.5)).std()

    ann_return = (1 + rolling_mean) ** TRADING_DAYS - 1
    ann_vol = rolling_std * np.sqrt(TRADING_DAYS)

    sharpe = (ann_return - RISK_FREE_RATE) / ann_vol.replace(0, np.nan)
    return sharpe


def compute_period_returns(returns_df: pd.DataFrame) -> dict[str, pd.Series]:
    """
    Compute cumulative returns for standard periods.

    Returns dict with keys: '1M', '3M', '6M', '1Y', '3Y', '5Y'
    Values: pd.Series of period total returns per ticker.
    """
    periods = {
        "1M": 21,
        "3M": 63,
        "6M": 126,
        "1Y": 252,
        "3Y": 756,
        "5Y": 1260,
    }

    results: dict[str, pd.Series] = {}
    for label, days in periods.items():
        if len(returns_df) >= days:
            window_data = returns_df.iloc[-days:]
            # Compound log returns
            total_return = (1 + window_data).prod() - 1
            results[label] = total_return
        else:
            results[label] = pd.Series(dtype=float)

    return results


def pivot_returns(prices_df: pd.DataFrame) -> pd.DataFrame:
    """
    Convert long-format price data to wide-format daily returns matrix.

    Args:
        prices_df: DataFrame with columns [ticker, date, close]

    Returns:
        Wide DataFrame (index=date, columns=tickers, values=daily_return)
    """
    if prices_df.empty:
        return pd.DataFrame()

    # Use pre-computed daily_return if available, else compute from close
    if "daily_return" in prices_df.columns and prices_df["daily_return"].notna().any():
        wide = prices_df.pivot(index="date", columns="ticker", values="daily_return")
    else:
        wide = prices_df.pivot(index="date", columns="ticker", values="close")
        wide = np.log(wide / wide.shift(1))

    wide.index = pd.to_datetime(wide.index)
    wide.sort_index(inplace=True)
    wide.dropna(how="all", inplace=True)
    wide.columns.name = None
    return wide


def get_returns_from_timescale(
    tickers: list[str],
    lookback_years: int = 3,
) -> pd.DataFrame:
    """
    Full pipeline: TimescaleDB → DuckDB query → wide returns matrix.

    Args:
        tickers: list of NSE tickers (e.g. ["RELIANCE.NS", "HDFCBANK.NS"])
        lookback_years: how many years of history to load

    Returns:
        Wide returns DataFrame (dates × tickers)
    """
    from backend.analytics.duckdb_client import load_prices_from_timescale

    start_date = (
        date.today() - timedelta(days=int(lookback_years * 365.25))
    ).strftime("%Y-%m-%d")

    prices_df = load_prices_from_timescale(tickers, start_date)

    if prices_df.empty:
        # Fallback: synthetic returns calibrated to NSE statistics
        logger.warning(
            f"No TimescaleDB price data for {tickers[:3]}... "
            f"Using synthetic returns. Run the yfinance pipeline first."
        )
        return _synthetic_returns_fallback(tickers, lookback_years)

    return pivot_returns(prices_df)


def _synthetic_returns_fallback(
    tickers: list[str],
    lookback_years: int = 3,
) -> pd.DataFrame:
    """
    Generate statistically-calibrated synthetic returns when TimescaleDB
    has no data yet (first-run scenario before yfinance pipeline has run).
    Uses NSE large-cap calibration: mean=12.4%/yr, std=15.8%/yr.
    """
    from backend.data_loaders.market_data import _synthetic_returns

    n_days = int(lookback_years * TRADING_DAYS)
    series_dict = {}
    for ticker in tickers:
        rd = _synthetic_returns(ticker, n_days=n_days)
        series_dict[ticker] = rd.returns

    df = pd.DataFrame(series_dict).dropna()
    df.columns.name = None
    return df
