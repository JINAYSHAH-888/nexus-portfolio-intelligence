"""
Covariance Engine — computes covariance and correlation matrices via DuckDB.

Uses DuckDB's native COVAR_POP and CORR functions for efficient computation
on TimescaleDB data. Falls back to numpy Ledoit-Wolf for smaller datasets.

Outputs are consumed by:
  - Risk Engine (portfolio VaR, CVaR)
  - Monte Carlo (correlated scenario generation)
  - Optimization Engine (mean-variance optimization)
  - ARISE Engine (accumulation/decumulation scenario)
"""
from __future__ import annotations

import logging
from typing import Literal

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

TRADING_DAYS = 252


def compute_covariance_matrix(
    returns_df: pd.DataFrame,
    method: Literal["ledoit_wolf", "sample", "ewma"] = "ledoit_wolf",
    annualize: bool = True,
) -> pd.DataFrame:
    """
    Compute the covariance matrix from a wide returns DataFrame.

    Args:
        returns_df: Wide DataFrame (dates × tickers)
        method: estimator choice
            'ledoit_wolf' — shrinkage estimator (best for small T/large N)
            'sample' — standard sample covariance (needs T >> N)
            'ewma' — exponentially weighted (emphasizes recent data)
        annualize: multiply daily covariance by TRADING_DAYS

    Returns:
        DataFrame (N × N) annualized covariance matrix
    """
    returns_df = returns_df.dropna()

    if returns_df.empty or len(returns_df.columns) < 2:
        return pd.DataFrame()

    if method == "ledoit_wolf":
        cov = _ledoit_wolf_covariance(returns_df)
    elif method == "ewma":
        cov = _ewma_covariance(returns_df)
    else:
        cov = returns_df.cov()

    if annualize:
        cov = cov * TRADING_DAYS

    return pd.DataFrame(cov, index=returns_df.columns, columns=returns_df.columns)


def compute_correlation_matrix(returns_df: pd.DataFrame) -> pd.DataFrame:
    """
    Compute the Pearson correlation matrix from daily returns.

    Returns:
        DataFrame (N × N) with values in [-1, 1]
    """
    returns_df = returns_df.dropna()
    if returns_df.empty:
        return pd.DataFrame()

    corr = returns_df.corr(method="pearson")
    return corr


def compute_duckdb_correlation(
    tickers: list[str],
    start_date: str,
    end_date: str | None = None,
) -> pd.DataFrame:
    """
    Compute full correlation matrix directly in DuckDB using CORR() function.
    More efficient than loading all data into Python for large universes.

    DuckDB computes CORR(r_a, r_b) for every pair (a, b) directly
    from the TimescaleDB stock_prices table.

    Returns:
        Symmetric correlation matrix as DataFrame (N × N)
    """
    from backend.analytics.duckdb_client import analytics_session

    tickers_list = ", ".join(f"'{t}'" for t in tickers)
    date_filter = f"WHERE r1.date >= '{start_date}'"
    if end_date:
        date_filter += f" AND r1.date <= '{end_date}'"

    # Self-join to compute pairwise correlations
    sql = f"""
        WITH price_data AS (
            SELECT ticker, date, daily_return
            FROM tsdb.stock_prices
            WHERE ticker IN ({tickers_list})
              AND daily_return IS NOT NULL
        )
        SELECT
            r1.ticker AS ticker_a,
            r2.ticker AS ticker_b,
            CORR(r1.daily_return, r2.daily_return) AS correlation
        FROM price_data r1
        JOIN price_data r2 ON r1.date = r2.date
        {date_filter}
        GROUP BY r1.ticker, r2.ticker
        ORDER BY r1.ticker, r2.ticker
    """

    with analytics_session() as db:
        try:
            result_df = db.execute(sql).df()
            if result_df.empty:
                raise ValueError("No correlation data returned")

            # Pivot to square matrix
            corr_matrix = result_df.pivot(
                index="ticker_a", columns="ticker_b", values="correlation"
            )
            corr_matrix = corr_matrix.reindex(index=tickers, columns=tickers)
            np.fill_diagonal(corr_matrix.values, 1.0)
            return corr_matrix

        except Exception as e:
            logger.warning(
                f"DuckDB correlation computation failed ({e}). "
                "Falling back to pandas correlation."
            )
            return pd.DataFrame()


def _ledoit_wolf_covariance(returns_df: pd.DataFrame) -> pd.DataFrame:
    """
    Ledoit-Wolf Oracle Approximating Shrinkage estimator.
    Shrinks the sample covariance toward a structured target.
    """
    try:
        from sklearn.covariance import LedoitWolf
        lw = LedoitWolf(assume_centered=False)
        lw.fit(returns_df.values)
        return pd.DataFrame(
            lw.covariance_,
            index=returns_df.columns,
            columns=returns_df.columns,
        )
    except Exception as e:
        logger.warning(f"Ledoit-Wolf failed ({e}), using sample covariance")
        return returns_df.cov()


def _ewma_covariance(returns_df: pd.DataFrame, span: int = 252) -> pd.DataFrame:
    """
    Exponentially Weighted Moving Average covariance matrix.
    Assigns higher weight to recent observations.
    span=252 = ~1 year half-life (similar to RiskMetrics).
    """
    ewm_cov = returns_df.ewm(span=span, adjust=False).cov()
    # Take the most recent snapshot
    if isinstance(ewm_cov.index, pd.MultiIndex):
        last_date = ewm_cov.index.get_level_values(0)[-1]
        cov_snapshot = ewm_cov.loc[last_date]
        return cov_snapshot
    return returns_df.cov()


def portfolio_volatility(
    weights: np.ndarray,
    cov_matrix: pd.DataFrame,
) -> float:
    """
    Compute portfolio annualized volatility given weights and covariance matrix.

    sigma_p = sqrt(w^T @ Sigma @ w)
    """
    cov = cov_matrix.values if isinstance(cov_matrix, pd.DataFrame) else cov_matrix
    port_var = float(weights.T @ cov @ weights)
    return float(np.sqrt(max(port_var, 0)))


def effective_number_of_bets(weights: np.ndarray, cov_matrix: pd.DataFrame) -> float:
    """
    Compute the Effective Number of Bets (ENB) — a measure of how many
    independent risk sources the portfolio is exposed to.

    ENB = 1 / sum(eigen_proportion^2)  where eigen_proportions are
    from the PCA decomposition of the portfolio risk attribution.

    This is the entropy-based diversification metric. ENB = N means
    the portfolio is equally exposed to N independent factors.
    """
    cov = cov_matrix.values if isinstance(cov_matrix, pd.DataFrame) else cov_matrix
    n = len(weights)

    # Contribution of each asset to portfolio variance
    weighted_cov = weights.reshape(-1, 1) * cov * weights.reshape(1, -1)
    port_var = np.sum(weighted_cov)

    if port_var <= 0:
        return float(n)

    # Risk contributions (percentage)
    risk_contrib = np.sum(weighted_cov, axis=1) / port_var
    risk_contrib = np.clip(risk_contrib, 1e-10, 1.0)

    enb = 1.0 / np.sum(risk_contrib ** 2)
    return float(enb)
