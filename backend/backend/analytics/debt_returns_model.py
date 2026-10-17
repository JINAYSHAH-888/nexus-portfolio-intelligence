"""
Debt Returns Model — real-data-anchored return/volatility generator for debt
(bond fund) schemes.

WHY THIS EXISTS:
  Equity scheme returns are reconstructed from real stock-ticker holdings via
  yfinance (MarketDataLoader.get_scheme_portfolio_returns). Debt schemes have
  no equivalent: their holdings are bond issuers, G-Secs, CDs/CPs -- none of
  which have a daily-traded price series a retail data source exposes. Feeding
  those "holdings" through the equity path finds zero mappable tickers and
  falls straight to the BLIND regime-switching synthetic generator, which
  knows nothing about this specific scheme's actual duration or yield.

  This module is the honest alternative: it builds a return series ANCHORED
  to the scheme's own real, disclosed numbers (modified duration, YTM,
  trailing 1yr/3yr/5yr/since-inception returns from the factsheet), so a
  short-duration liquid fund and a long-duration gilt fund produce
  meaningfully different, defensible risk profiles instead of the same
  generic "debt" noise.

INPUTS (all sourced from schemes_metadata.json, already populated by the
2026-06 factsheet refresh):
  modified_duration_years  -- % price sensitivity to a 1% yield move
  ytm_decimal               -- current running yield (the carry anchor)
  trailing_returns          -- {period: realized %}, when available -- these
                                are ACTUAL realized numbers and take priority
                                over the YTM anchor whenever present, since
                                realized performance already nets out
                                expenses, credit events, and roll-down that a
                                pure YTM figure doesn't capture.

METHOD:
  mean  = blend(realized trailing return, YTM carry) -- see `_anchor_mean`
  vol   = modified_duration_years * ASSUMED_ANNUAL_YIELD_VOL
          (the standard first-order bond-fund vol approximation: a fund's
          NAV volatility from rate moves scales with its duration)
        + a small credit/liquidity floor so even a 1-day overnight fund
          doesn't get a literal 0% volatility
  path  = mean-reverting AR(1) daily process at this (mean, vol), not a pure
          random walk -- bond fund NAVs are far smoother/more autocorrelated
          day-to-day than equities.
"""
from __future__ import annotations

import logging
import math
from datetime import datetime

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

# First-order approximation of annual Indian G-Sec/corporate-bond yield
# volatility (basis points, expressed as a decimal fraction of yield).
# This is a labelled assumption, not a fitted number -- review periodically.
ASSUMED_ANNUAL_YIELD_VOL = 0.012        # ~120 bps/yr typical yield swing
CREDIT_LIQUIDITY_VOL_FLOOR = 0.002      # 20 bps -- keeps overnight/liquid funds > 0
AR1_PHI = 0.05                          # mild positive autocorrelation (roll-down carry)


def _anchor_mean(meta: dict) -> tuple[float, str]:
    """
    Pick the mean annual return anchor for this scheme, preferring REALIZED
    trailing performance over the YTM carry estimate wherever available.
    Returns (mean_decimal, source_label) for logging/transparency.
    """
    trailing = meta.get("trailing_returns") or {}
    for key in ("3_years", "5_years", "1_year", "since_inception"):
        val = trailing.get(key)
        if val is not None:
            return float(val) / 100.0, f"trailing_returns[{key}]"

    ytm = meta.get("ytm_decimal")
    if ytm is not None:
        return float(ytm), "ytm_decimal"

    logger.warning(
        f"No trailing_returns or ytm_decimal for {meta.get('id')!r}; "
        f"falling back to a flat 6% nominal carry assumption."
    )
    return 0.06, "fallback_flat_6pct"


def _anchor_vol(meta: dict) -> tuple[float, str]:
    duration = meta.get("modified_duration_years")
    if duration is not None:
        vol = max(float(duration) * ASSUMED_ANNUAL_YIELD_VOL, CREDIT_LIQUIDITY_VOL_FLOOR)
        return vol, "duration_scaled"
    logger.warning(
        f"No modified_duration_years for {meta.get('id')!r}; "
        f"using the liquidity floor volatility."
    )
    return CREDIT_LIQUIDITY_VOL_FLOOR, "floor_only"


def debt_scheme_returns(
    meta: dict,
    n_days: int = 1260,          # ~5 years of trading days, matches equity default
    seed: int | None = None,
) -> "ReturnsDataLike":
    """
    Build a duration/YTM/trailing-return-anchored daily return series for one
    debt scheme. `meta` is that scheme's full record from schemes_metadata.json
    (must have at least `id`; `modified_duration_years`, `ytm_decimal`,
    `trailing_returns` are used when present, degrading gracefully otherwise).

    Returns a namedtuple-compatible object with the same shape as
    `market_data.ReturnsData` (ticker, returns, prices, is_synthetic,
    fetched_at) so it drops into the same pipeline; `is_synthetic=True`
    still applies (this is not a real daily-traded price series) but the
    scheme's own real numbers drive the parameters, unlike the blind
    regime-switching fallback.
    """
    from backend.data_loaders.market_data import ReturnsData   # local import, avoid cycle

    scheme_id = meta.get("id", "unknown-debt-scheme")
    mean, mean_src = _anchor_mean(meta)
    vol, vol_src = _anchor_vol(meta)

    rng = np.random.default_rng(seed if seed is not None else abs(hash(scheme_id)) % (2**31))
    daily_mean = mean / 252
    daily_vol = vol / math.sqrt(252)

    shocks = rng.normal(0, 1, n_days)
    daily = np.empty(n_days)
    prev = 0.0
    for i in range(n_days):
        # mean-reverting AR(1) around the daily carry -- smoother than a pure
        # random walk, matching real bond-fund NAV behaviour
        daily[i] = daily_mean + AR1_PHI * prev + daily_vol * math.sqrt(1 - AR1_PHI**2) * shocks[i]
        prev = daily[i] - daily_mean

    end = pd.Timestamp.today().normalize()
    # See market_data.py's _synthetic_returns for the full explanation:
    # pd.bdate_range(end=X, periods=N) returns N-1 elements on this pandas
    # version -- over-request and slice to guarantee the exact length needed.
    dates = pd.bdate_range(end=end, periods=n_days + 5)[-n_days:]
    prices = pd.Series(100 * np.exp(np.cumsum(daily)), index=dates)
    returns = pd.Series(daily, index=dates)

    logger.info(
        f"debt_scheme_returns[{scheme_id}]: mean={mean:.2%} ({mean_src}), "
        f"vol={vol:.2%} ({vol_src})"
    )

    return ReturnsData(
        ticker=scheme_id, returns=returns, prices=prices,
        is_synthetic=True, fetched_at=datetime.now(),
        source="debt_model",
    )
