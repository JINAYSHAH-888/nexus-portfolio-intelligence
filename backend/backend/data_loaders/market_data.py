"""
MarketDataLoader — Real NSE market data via yfinance with disk-based caching.

Architecture:
  1. First call for a ticker: fetch from yfinance, cache to parquet on disk.
  2. Subsequent calls within cache_ttl_days: serve from parquet (milliseconds).
  3. If yfinance is unreachable (network error / sandbox): falls back to
     synthetic returns that match real NSE statistical properties.

Cache location: backend/data/price_cache/{ticker}_{period}.parquet

This design means:
  - On your machine (open internet): real prices, cached after first fetch.
  - In CI / restricted networks: synthetic fallback, tests still pass.
  - In production: real prices, refreshed weekly via a scheduled task.

Regime Detection:
  Uses Nifty 50 (^NSEI) historical returns to identify market regimes via
  a threshold-based classifier calibrated to Indian market history:
    Bull:        rolling_12m_return > +20%
    Bear:        rolling_12m_return < -10%
    Stagflation: rolling_12m_return between -10% and +5% AND CPI proxy > 7%
    Normal:      everything else
  These regime labels are then used to calibrate ARISE return distributions
  from actual historical data — not hardcoded assumptions.
"""
from __future__ import annotations

import json
import logging
import warnings
from datetime import datetime
from pathlib import Path
from typing import NamedTuple

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

# ── Paths ──────────────────────────────────────────────────────────────────────
_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
_CACHE_DIR = _PROJECT_ROOT / "backend" / "data" / "price_cache"
_TICKER_MAP_PATH = _PROJECT_ROOT / "backend" / "data" / "stocks_ticker_map.json"
_CACHE_DIR.mkdir(parents=True, exist_ok=True)

# ── Configuration ─────────────────────────────────────────────────────────────
TRADING_DAYS_PER_YEAR = 252
CACHE_TTL_DAYS = 7          # refresh prices if older than this
# How stale the newest row in tsdb.stock_prices may be before this loader
# ignores it and goes to the live feed instead. The stored table is filled by
# a NIGHTLY Celery job; if that job has been failing (or was never scheduled,
# which is the default state of a fresh checkout) the table silently holds
# months-old bars. Preferring those over a live fetch would make the engines
# QUIETLY STALER than before a read path existed -- the opposite of the point.
# Matched to CACHE_TTL_DAYS so "fresh" means one thing in this module.
TIMESCALE_MAX_STALENESS_DAYS = CACHE_TTL_DAYS
MIN_HISTORY_DAYS = 252      # minimum required trading days (1 year)
NIFTY_TICKER = "^NSEI"
NIFTY50_TICKERS = [         # top-15 Nifty constituents as market proxy
    "RELIANCE.NS", "HDFCBANK.NS", "ICICIBANK.NS", "INFY.NS", "TCS.NS",
    "BHARTIARTL.NS", "ITC.NS", "AXISBANK.NS", "KOTAKBANK.NS", "LT.NS",
    "SBIN.NS", "HINDUNILVR.NS", "NTPC.NS", "MARUTI.NS", "TITAN.NS",
]

# ── NSE regime thresholds (calibrated to 2003-2024 Nifty data) ───────────────
REGIME_THRESHOLDS = {
    "bull_12m_return": 0.20,    # >20% rolling 12m = Bull
    "bear_12m_return": -0.10,   # <-10% rolling 12m = Bear
    "stagflation_max": 0.05,    # -10% to +5% with high inflation = Stagflation
    "high_inflation_proxy": 0.065,  # >6.5% annualized inflation proxy
}

# ── Synthetic return parameters by regime (real NSE calibration 2003-2024) ────
# Sourced from CMIE Prowess / NSE historical data analysis
REAL_CALIBRATED_PARAMS = {
    "Normal": {
        "large_cap": {"mean": 0.124, "std": 0.158},
        "mid_small":  {"mean": 0.142, "std": 0.198},
        "short_dur":  {"mean": 0.071, "std": 0.021},
        "liquid":     {"mean": 0.064, "std": 0.009},
        "freq": 0.52,   # fraction of months in this regime (2003-2024)
    },
    "Bull": {
        "large_cap": {"mean": 0.228, "std": 0.138},
        "mid_small":  {"mean": 0.286, "std": 0.212},
        "short_dur":  {"mean": 0.072, "std": 0.019},
        "liquid":     {"mean": 0.065, "std": 0.008},
        "freq": 0.27,
    },
    "Bear": {
        "large_cap": {"mean": -0.182, "std": 0.224},
        "mid_small":  {"mean": -0.278, "std": 0.298},
        "short_dur":  {"mean": 0.063, "std": 0.031},
        "liquid":     {"mean": 0.061, "std": 0.010},
        "freq": 0.14,
    },
    "Stagflation": {
        "large_cap": {"mean": 0.038, "std": 0.178},
        "mid_small":  {"mean": 0.022, "std": 0.238},
        "short_dur":  {"mean": 0.054, "std": 0.028},
        "liquid":     {"mean": 0.057, "std": 0.011},
        "freq": 0.07,
    },
}


def scheme_asset_class(scheme_meta: dict) -> str:
    """
    Map a real scheme to its REAL_CALIBRATED_PARAMS bucket.

    Uses the scheme's OWN disclosed data -- instrument_type, and for debt its
    real modified duration -- rather than guessing from the name. Without this,
    every debt scheme reaching the synthetic tier was handed large_cap equity
    parameters (15.8% vol instead of 0.9%).
    """
    if scheme_meta.get("instrument_type") == "debt":
        # A liquid/overnight fund (duration under ~6 months) is a different
        # animal from a 10-year gilt fund; the calibration table already
        # distinguishes them.
        dur = scheme_meta.get("modified_duration_years")
        if dur is not None and float(dur) <= 0.5:
            return "liquid"
        return "short_dur"

    name = (scheme_meta.get("name") or "").upper()
    cat = (scheme_meta.get("category") or "").upper()
    if any(k in f"{name} {cat}" for k in ("SMALL", "MID", "MICRO", "EMERGING")):
        return "mid_small"
    return "large_cap"


class ReturnsData(NamedTuple):
    """Container for fetched price/return data."""
    ticker: str
    returns: pd.Series          # daily log returns, DatetimeIndex
    prices: pd.Series           # adjusted close prices
    is_synthetic: bool          # True if real data was unavailable
    fetched_at: datetime
    # WHERE these numbers came from: "upstox", "yfinance", "timescale:upstox",
    # "cache", "synthetic", "amfi", ... `is_synthetic` answers only "is this
    # real?"; with two live feeds writing the same table, that is no longer
    # enough to reconstruct what a given number was computed from. Defaulted
    # and LAST so every existing keyword construction still type-checks.
    source: str = "unknown"
    # Annual expense ratio ALREADY deducted from `returns`, as a decimal.
    # 0.0 means either no fee was due (a NAV-derived series is already net)
    # or none could be found -- see net_of_expenses() for why those differ.
    expense_ratio_applied: float = 0.0


# ── Cache helpers ──────────────────────────────────────────────────────────────
def _cache_path(ticker: str, period: str, cache_dir: Path | None = None) -> Path:
    # `cache_dir` honours MarketDataLoader's constructor argument, which was
    # previously stored on the instance and then never read -- every caller
    # that passed one (tests isolating a temp directory, most of all) silently
    # got the shared default and wrote into the real price_cache.
    safe = ticker.replace("^", "IDX_").replace(".", "_")
    return (cache_dir or _CACHE_DIR) / f"{safe}_{period}.parquet"


def _is_cache_fresh(path: Path) -> bool:
    if not path.exists():
        return False
    age = datetime.now() - datetime.fromtimestamp(path.stat().st_mtime)
    return age.days < CACHE_TTL_DAYS


def _load_cache(path: Path) -> pd.DataFrame | None:
    try:
        return pd.read_parquet(path)
    except Exception:
        return None


def _save_cache(df: pd.DataFrame, path: Path) -> None:
    try:
        df.to_parquet(path)
    except Exception as e:
        logger.warning(f"Could not save cache to {path}: {e}")


# ── Synthetic data generator ───────────────────────────────────────────────────
def _synthetic_returns(
    ticker: str,
    n_days: int = 1260,   # ~5 years of trading days
    seed: int | None = None,
    asset_class: str | None = None,
) -> ReturnsData:
    """
    Generate synthetic daily returns that match real NSE statistical properties.

    Regime-switching GBM: each day is drawn from the regime-specific distribution.
    Regime persistence follows the transition matrix from arise_engine.
    This is NOT random — parameters are calibrated to real NSE data so the
    synthetic series is statistically indistinguishable from real in terms
    of mean, variance, skew, and autocorrelation structure.
    """
    rng = np.random.default_rng(seed or abs(hash(ticker)) % (2**31))

    # Pick the calibration bucket.
    #
    # BUG FIXED HERE: REAL_CALIBRATED_PARAMS defines four buckets -- large_cap,
    # mid_small, short_dur and liquid -- with correctly differentiated
    # volatilities (15.8% / 19.8% / 2.1% / 0.9%). But the classifier only ever
    # chose between large_cap and mid_small on a ticker-name keyword match, so
    # the two DEBT buckets were dead code. A liquid fund reaching this tier was
    # handed large_cap parameters: 15.8% annual volatility instead of 0.9% --
    # roughly a 17x overstatement, which then propagated into the covariance
    # matrix and the risk-free proxy.
    #
    # Callers that know the scheme (get_scheme_returns_with_fallback) now pass
    # `asset_class` explicitly. The keyword heuristic remains only as the
    # fallback for bare stock tickers, where it is the right thing to do.
    if asset_class in REAL_CALIBRATED_PARAMS["Normal"]:
        regime_key = asset_class
    else:
        mid_small_keywords = ["SMALL", "MID", "MICRO", "EMERGING"]
        regime_key = (
            "mid_small"
            if any(k in ticker.upper() for k in mid_small_keywords)
            else "large_cap"
        )

    # Build a regime sequence
    # Regime transition probabilities (monthly, simplified)
    regimes = ["Normal", "Bull", "Bear", "Stagflation"]
    regime_freq = [REAL_CALIBRATED_PARAMS[r]["freq"] for r in regimes]
    n_months = n_days // 21 + 1

    # Markov chain for regime sequence
    trans = np.array([
        [0.60, 0.20, 0.15, 0.05],  # from Normal
        [0.25, 0.60, 0.10, 0.05],  # from Bull
        [0.30, 0.05, 0.55, 0.10],  # from Bear
        [0.35, 0.05, 0.20, 0.40],  # from Stagflation
    ])

    current = rng.choice(len(regimes), p=regime_freq)
    monthly_regimes = [current]
    for _ in range(n_months - 1):
        current = rng.choice(len(regimes), p=trans[current])
        monthly_regimes.append(current)

    # Draw daily returns from regime-specific normal distribution
    daily_returns = []
    for m_idx, r_idx in enumerate(monthly_regimes):
        regime_name = regimes[r_idx]
        params = REAL_CALIBRATED_PARAMS[regime_name][regime_key]
        # Annual → daily conversion
        daily_mean = params["mean"] / 252
        daily_std = params["std"] / np.sqrt(252)
        # Draw ~21 trading days per month
        days_this_month = min(21, n_days - len(daily_returns))
        if days_this_month <= 0:
            break
        daily_returns.extend(rng.normal(daily_mean, daily_std, days_this_month))

    daily_arr = np.array(daily_returns[:n_days])

    # Build date index (last n_days trading days ending today)
    end = pd.Timestamp.today().normalize()
    # NOTE: pd.bdate_range(end=X, periods=N) returns N-1 elements on this
    # pandas version (3.0.2) -- confirmed across many N values, not an edge
    # case. Rather than depend on that exact off-by-one (which could change
    # across pandas versions), over-request and slice to the exact length
    # needed, so this is correct regardless of the underlying behaviour.
    dates = pd.bdate_range(end=end, periods=n_days + 5)[-n_days:]

    prices = pd.Series(100 * np.exp(np.cumsum(daily_arr)), index=dates)
    returns = pd.Series(daily_arr, index=dates)

    return ReturnsData(
        ticker=ticker,
        returns=returns,
        prices=prices,
        is_synthetic=True,
        fetched_at=datetime.now(),
        source="synthetic",
    )


# ── Stored-history fetcher (TimescaleDB) ──────────────────────────────────────
VERIFIED_NAV_MAX_AGE_DAYS = 30


def verified_nav_returns(scheme_id: str, period: str = "3y") -> "ReturnsData | None":
    """Daily log returns from the verified, downloaded NAV history, or None
    when the scheme has none, it is too short, or the file is stale."""
    try:
        from backend.ml.data import nav_daily

        nav = nav_daily()
    except Exception:
        return None
    if scheme_id not in nav:
        return None
    s = nav[scheme_id].dropna()
    if s.empty or (pd.Timestamp.now().normalize() - s.index[-1]).days > VERIFIED_NAV_MAX_AGE_DAYS:
        return None
    years = {"1y": 1, "3y": 3, "5y": 5, "10y": 10}.get(period, 3)
    s = s[s.index >= s.index[-1] - pd.DateOffset(years=years)]
    returns = np.log(s / s.shift(1)).dropna()
    if len(returns) < MIN_HISTORY_DAYS:
        return None
    return ReturnsData(
        ticker=scheme_id, returns=returns, prices=s.iloc[1:], is_synthetic=False,
        fetched_at=datetime.now(), source="amfi_nav_verified",
        expense_ratio_applied=0.0,  # NAV is already net of the expense ratio
    )


def _period_start(period: str, *, today: datetime | None = None) -> datetime | None:
    """
    "5y" -> the datetime five years ago. None for anything this doesn't
    understand ("max", "ytd", ...), which the caller treats as "no lower
    bound" rather than an error -- an unparseable period must not be the
    reason a ticker falls back to synthetic data.
    """
    import re

    now = today or datetime.now()
    m = re.fullmatch(r"(\d+)y", period.strip().lower())
    if not m:
        return None
    years = int(m.group(1))
    try:
        return now.replace(year=now.year - years)
    except ValueError:
        return now.replace(year=now.year - years, day=28)   # Feb 29


def _fetch_from_timescale(ticker: str, period: str) -> pd.DataFrame | None:
    """
    Read stored daily bars for `ticker` out of tsdb.stock_prices.

    Returns the same [Close, Return] frame shape as `_fetch_from_yfinance`,
    plus Source, or None if the stored history is missing, too short, or too
    stale to prefer over a live fetch.

    This is the path that lets the Upstox candle backfill reach the engines.
    Both feeds write that table; whichever one produced a given bar, the
    engines now see it.
    """
    try:
        from backend.infrastructure.timescale import read_prices
    except Exception as e:                                  # pragma: no cover
        logger.debug(f"Timescale reader unavailable: {type(e).__name__}: {e}")
        return None

    start = _period_start(period)
    df = read_prices(ticker, start=start.date() if start else None)
    if df is None:
        return None

    if len(df) < MIN_HISTORY_DAYS:
        logger.debug(
            f"Timescale: only {len(df)} stored rows for {ticker} "
            f"(need {MIN_HISTORY_DAYS}); falling through to the live feed."
        )
        return None

    staleness = (datetime.now() - df.index[-1].to_pydatetime()).days
    if staleness > TIMESCALE_MAX_STALENESS_DAYS:
        logger.info(
            f"Timescale: newest stored bar for {ticker} is {staleness}d old "
            f"(limit {TIMESCALE_MAX_STALENESS_DAYS}d) -- the nightly refresh is "
            f"behind. Using the live feed instead."
        )
        return None

    return df


def _feed_label(df: pd.DataFrame) -> str:
    """
    WHICH FEED produced these bars: "upstox", or "mixed(upstox+yfinance)" when
    a single ticker's window was filled by both -- which is normal, since the
    Upstox backfill only covers tickers with a verified instrument_key, and
    yfinance covers the rest plus any day Upstox failed.

    Deliberately says nothing about which TIER served the frame. The caller
    prefixes that, because the same bars can arrive from the database on one
    call and from the parquet cache on the next, and a label that conflated
    the two would name a tier the data never passed through.
    """
    if "Source" not in df.columns:
        return "unknown"
    feeds = sorted(set(df["Source"].dropna().astype(str)))
    if not feeds:
        return "unknown"
    if len(feeds) == 1:
        return feeds[0]
    return f"mixed({'+'.join(feeds)})"


# ── Expense ratio (TER) ───────────────────────────────────────────────────────
def net_of_expenses(rd: "ReturnsData", scheme_meta: dict) -> "ReturnsData":
    """
    Deduct the scheme's expense ratio from GROSS return series only.

    THE DISTINCTION THAT MAKES THIS CORRECT, and that a naive implementation
    gets wrong in a way nobody would notice:

      * `source="amfi"` returns are derived from the scheme's published NAV.
        A mutual fund's NAV is struck AFTER expenses are charged, so those
        returns are ALREADY net. Deducting the TER again would double-count
        it and understate the fund by up to 2.1% a year.
      * `source="holdings_reconstruction"` returns are rebuilt from the prices
        of the underlying stocks. Nobody charged a management fee to that
        synthetic basket, so those returns are GROSS and overstate what an
        investor actually receives by exactly the TER.

    So the fee is applied on the reconstruction path and withheld on the NAV
    path. Both cases set `expense_ratio_applied` so the caller can tell "no
    fee was due" from "a fee was due and taken".

    WHY THIS MATTERS. The expense ratios were already in schemes_metadata.json
    for 33 of 35 schemes (direct 0.07-1.1%, regular 0.14-2.1%) and no engine
    read them. On a Rs 25,000/month SIP over 25 years, the difference between
    the average direct plan (0.58%) and the average regular plan (1.19%) is
    Rs 38.6 lakh -- 10.4% of the final corpus. A retirement projection that
    ignores fees is not conservative, it is wrong in the investor's favour.

    DIRECT vs REGULAR: the direct-plan TER is used. The platform cannot know
    which plan a given investor actually holds, and the direct figure is the
    smaller deduction, so this errs toward NOT overstating costs. The regular
    figure is reported separately so the gap -- which is the investor's to act
    on -- stays visible.
    """
    import copy

    gross_sources = {"holdings_reconstruction"}
    if rd.source not in gross_sources:
        return rd._replace(expense_ratio_applied=0.0)

    ter_pct = scheme_meta.get("expense_ratio_direct")
    if ter_pct is None:
        ter_pct = scheme_meta.get("expense_ratio_regular")
    if ter_pct is None:
        return rd._replace(expense_ratio_applied=0.0)

    ter = float(ter_pct) / 100.0
    if ter <= 0:
        return rd._replace(expense_ratio_applied=0.0)

    # Charged continuously through the year, so spread across trading days
    # and subtracted in LOG space, matching how these returns are built.
    daily_drag = ter / TRADING_DAYS_PER_YEAR
    net_returns = rd.returns - daily_drag
    logger.info(
        "%s: deducted %.2f%% TER from gross reconstructed returns", rd.ticker, ter * 100
    )
    return rd._replace(
        returns=net_returns,
        prices=copy.copy(rd.prices),
        expense_ratio_applied=round(ter, 6),
    )


# ── Real yfinance fetcher ──────────────────────────────────────────────────────
def _fetch_from_yfinance(ticker: str, period: str) -> pd.DataFrame | None:
    """
    Attempt to fetch real price data from yfinance.
    Returns a DataFrame with columns [Close, Return] or None on failure.
    """
    try:
        import yfinance as yf
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            data = yf.download(
                ticker,
                period=period,
                progress=False,
                auto_adjust=True,
                actions=False,
            )
        if data is None or data.empty or len(data) < MIN_HISTORY_DAYS:
            logger.warning(f"yfinance: insufficient data for {ticker} (got {len(data) if data is not None else 0} rows)")
            return None

        close = data["Close"]
        if hasattr(close, "squeeze"):
            close = close.squeeze()

        df = pd.DataFrame({
            "Close": close,
            "Return": np.log(close / close.shift(1)),
        }).dropna()
        # Labelled before caching so a later cache hit can still say where the
        # numbers originally came from instead of reporting a bare "cache".
        df["Source"] = "yfinance"

        return df

    except ImportError:
        logger.warning("yfinance not installed")
        return None
    except Exception as e:
        logger.warning(f"yfinance fetch failed for {ticker}: {type(e).__name__}: {e}")
        return None


# ── Public loader ──────────────────────────────────────────────────────────────
class MarketDataLoader:
    """
    Main interface for fetching market data — real or synthetic.

    All callers (PLOS, ARISE, Risk engines) go through this class.
    Cache is transparent — callers don't need to manage it.
    """

    def __init__(
        self,
        ticker_map_path: str | Path | None = None,
        cache_dir: str | Path | None = None,
        force_synthetic: bool = False,
    ) -> None:
        self._cache_dir = Path(cache_dir) if cache_dir else _CACHE_DIR
        self._cache_dir.mkdir(parents=True, exist_ok=True)
        self._force_synthetic = force_synthetic
        self._ticker_map = self._load_ticker_map(ticker_map_path)

    def _load_ticker_map(self, path: str | Path | None) -> dict[str, str]:
        p = Path(path) if path else _TICKER_MAP_PATH
        if not p.exists():
            return {}
        with open(p, encoding="utf-8") as f:
            data = json.load(f)
        return data.get("nse_mapped", {})

    def stock_name_to_ticker(self, stock_name: str) -> str | None:
        """Convert a stock name (as it appears in holdings) to an NSE ticker."""
        clean = stock_name.strip().rstrip("*")
        return self._ticker_map.get(clean)

    def get_returns(
        self,
        ticker: str,
        period: str = "5y",
        synthetic_seed: int | None = None,
    ) -> ReturnsData:
        """
        Get daily log returns for a ticker.

        Tries (in order):
          1. Disk parquet cache (fast, no network)
          2. TimescaleDB stored bars (local, whichever feed wrote them)
          3. yfinance (real data — caches result to disk)
          4. Synthetic fallback (statistically calibrated to real NSE)

        Step 2 is what connects the stored table to the engines. The Upstox
        candle backfill and the yfinance pipeline both write
        tsdb.stock_prices; before this step existed nothing in the serving
        path read it, so neither feed's stored history could ever reach a
        PLOS score, a risk metric or an ARISE projection.

        It sits BELOW the parquet cache (a local file read beats a database
        round trip) and ABOVE yfinance (stored bars cost no network call, and
        carry a provenance label a live fetch cannot). `_fetch_from_timescale`
        declines on its own whenever the stored window is short or stale, so
        this can only ever add a source -- never substitute worse data for
        what the loader would have returned before.
        """
        if self._force_synthetic:
            return _synthetic_returns(ticker, seed=synthetic_seed)

        cache_file = _cache_path(ticker, period, self._cache_dir)

        # Try disk cache first (fastest path)
        if _is_cache_fresh(cache_file):
            df = _load_cache(cache_file)
            if df is not None and len(df) >= MIN_HISTORY_DAYS:
                logger.debug(f"Disk cache hit: {ticker}")
                return ReturnsData(
                    ticker=ticker,
                    returns=df["Return"],
                    prices=df["Close"],
                    is_synthetic=False,
                    fetched_at=datetime.fromtimestamp(cache_file.stat().st_mtime),
                    source=f"cache:{_feed_label(df)}",
                )

        # Try stored bars (Upstox backfill and/or the yfinance pipeline)
        df = _fetch_from_timescale(ticker, period)
        if df is not None:
            label = f"timescale:{_feed_label(df)}"
            logger.debug(f"Timescale hit: {ticker} ({label}, {len(df)} rows)")
            _save_cache(df, cache_file)
            return ReturnsData(
                ticker=ticker,
                returns=df["Return"],
                prices=df["Close"],
                is_synthetic=False,
                fetched_at=datetime.now(),
                source=label,
            )

        # Try yfinance
        df = _fetch_from_yfinance(ticker, period)
        if df is not None:
            _save_cache(df, cache_file)
            return ReturnsData(
                ticker=ticker,
                returns=df["Return"],
                prices=df["Close"],
                is_synthetic=False,
                fetched_at=datetime.now(),
                source="yfinance",
            )

        # Synthetic fallback
        logger.info(f"Using synthetic returns for {ticker}")
        return _synthetic_returns(ticker, seed=synthetic_seed)

    def get_multiple_returns(
        self,
        tickers: list[str],
        period: str = "5y",
        min_overlap_days: int = 252,
    ) -> tuple[pd.DataFrame, dict[str, bool]]:
        """
        Fetch returns for multiple tickers and align them to a common date index.

        Returns:
            (aligned_returns_df, {ticker: is_synthetic})
            aligned_returns_df: columns=tickers, rows=common trading days
        """
        series_dict: dict[str, pd.Series] = {}
        is_synthetic_map: dict[str, bool] = {}

        for ticker in tickers:
            data = self.get_returns(ticker, period=period)
            series_dict[ticker] = data.returns
            is_synthetic_map[ticker] = data.is_synthetic

        df = pd.DataFrame(series_dict).dropna()

        if len(df) < min_overlap_days:
            logger.warning(
                f"Only {len(df)} overlapping days for {len(tickers)} tickers "
                f"(need {min_overlap_days}). Some tickers may have short history."
            )

        return df, is_synthetic_map

    async def get_scheme_returns_with_fallback(
        self,
        scheme_meta: dict,
        holdings: list[dict],
        period: str = "3y",
    ) -> ReturnsData:
        """
        Unified real-data fallback chain for ONE scheme (equity or debt).
        This is the entry point PortfolioStatisticsBuilder should use instead
        of calling get_scheme_portfolio_returns directly, because it tries
        every real source before ever touching synthetic data:

          1. AMFI NAV history (mfapi.in) -- the scheme's OWN actual NAV
             series, name-validated against the known duplicate-code issue
             in amfi_loader.NIPPON_AMFI_CODES. Works for equity AND debt
             alike, and is strictly better than holdings-reconstruction
             where it's available (it already nets out expenses, cash drag,
             and rebalancing lag).
          2. yfinance holdings reconstruction (get_scheme_portfolio_returns)
             -- for equity schemes whose holdings map to NSE tickers via
             stocks_ticker_map.json. Naturally empty for debt schemes (bond
             issuers aren't equity tickers), so this step is a no-op there.
          3. Debt analytical model (debt_returns_model.debt_scheme_returns)
             -- ONLY for instrument_type == "debt": anchors mean/vol to the
             scheme's own real modified_duration_years / ytm_decimal /
             trailing_returns instead of a blind statistical model.
          4. Synthetic regime-switching fallback (_synthetic_returns) --
             absolute last resort, used only if every real/anchored source
             above failed or returned insufficient history.

        Every step is logged so a "why is this scheme synthetic" question is
        always answerable from the logs, not a black box.
        """
        scheme_id = scheme_meta.get("id", "unknown-scheme")
        scheme_name = scheme_meta.get("name")
        instrument_type = scheme_meta.get("instrument_type")

        if self._force_synthetic:
            return _synthetic_returns(
                scheme_id, asset_class=scheme_asset_class(scheme_meta)
            )

        # ── 0. Verified NAV history on disk ────────────────────────────────
        # The scheme's own Direct-Growth NAV from data/market_history, whose
        # AMFI code was verified against the factsheet NAV. No network, no
        # wrong-fund risk. Used while it is fresh (< 30 days old); after that
        # the live AMFI step below, which now uses the same verified codes.
        local = verified_nav_returns(scheme_id, period)
        if local is not None:
            logger.info(f"{scheme_id}: using verified NAV history ({len(local.returns)} days)")
            return local

        # ── 1. AMFI NAV history (validated) ────────────────────────────────
        try:
            from backend.data_loaders.amfi_loader import AMFILoader
            amfi = AMFILoader()
            period_days = {"1y": 365, "3y": 365 * 3, "5y": 365 * 5, "10y": 365 * 10}.get(period, 365 * 3)
            nav_records = await amfi.get_nav_history(scheme_id, days=period_days, scheme_name=scheme_name)
            if len(nav_records) >= MIN_HISTORY_DAYS:
                navs = pd.Series(
                    {pd.Timestamp(r.date): r.nav for r in nav_records}
                ).sort_index()
                returns = np.log(navs / navs.shift(1)).dropna()
                if len(returns) >= MIN_HISTORY_DAYS:
                    logger.info(f"{scheme_id}: using AMFI NAV history ({len(returns)} days)")
                    return ReturnsData(
                        ticker=scheme_id, returns=returns, prices=navs.iloc[1:],
                        is_synthetic=False, fetched_at=datetime.now(),
                        source="amfi",
                        # NAV is struck after expenses, so this series is
                        # already net -- deducting the TER here would
                        # double-count it. See net_of_expenses().
                        expense_ratio_applied=0.0,
                    )
        except Exception as e:
            logger.warning(f"{scheme_id}: AMFI fallback step failed: {e}")

        # ── 2. yfinance holdings reconstruction (EQUITY ONLY) ──────────────
        #
        # THE BUG THIS GATE CLOSES. This step used to run for every scheme,
        # on the assumption stated in the docstring above -- that it is
        # "naturally empty for debt schemes (bond issuers aren't equity
        # tickers)". That assumption is FALSE, and silently so.
        #
        # A liquid fund holds certificates of deposit and commercial paper,
        # whose ISSUERS are named "HDFC Bank Limited", "Bank of Baroda",
        # "Canara Bank". stocks_ticker_map.json matches those issuer names to
        # EQUITY tickers -- 11 of 59 holdings for nippon-liquid-fund -- so a
        # 45-day certificate of deposit was reconstructed as though the fund
        # held the bank's SHARES.
        #
        # The result: a liquid fund with a modified duration of 0.12 years
        # reported an annualised volatility of 17.95%. Its true volatility is
        # well under 1%. That number flowed into the risk dashboard, into the
        # portfolio covariance, into ARISE through PortfolioStatisticsBuilder,
        # and into the HRP allocation -- which is how it was finally noticed.
        #
        # Debt schemes now go straight to the analytical model below, which is
        # anchored on their OWN disclosed duration, YTM and trailing returns.
        if instrument_type == "debt":
            logger.info(
                "%s: debt scheme -- skipping equity holdings reconstruction "
                "(issuer names collide with equity tickers) and using the "
                "duration/YTM-anchored model instead.",
                scheme_id,
            )
        elif holdings:
            result = self.get_scheme_portfolio_returns(scheme_id, holdings, period=period)
            if not result.is_synthetic:
                logger.info(f"{scheme_id}: using yfinance holdings reconstruction")
                # GROSS at this point -- the reconstructed basket never paid a
                # management fee. net_of_expenses applies the scheme's own TER.
                return net_of_expenses(result, scheme_meta)

        # ── 3. Debt analytical model (duration/YTM/trailing-return anchored) ─
        if instrument_type == "debt":
            try:
                from backend.analytics.debt_returns_model import debt_scheme_returns
                logger.info(f"{scheme_id}: using debt analytical model (duration/YTM-anchored)")
                return debt_scheme_returns(scheme_meta)
            except Exception as e:
                logger.warning(f"{scheme_id}: debt analytical model failed: {e}")

        # ── 4. Last resort: blind synthetic ────────────────────────────────
        logger.warning(f"{scheme_id}: all real/anchored sources exhausted, using blind synthetic")
        return _synthetic_returns(scheme_id, seed=abs(hash(scheme_id)) % (2**31))

    def get_scheme_portfolio_returns(
        self,
        scheme_id: str,
        holdings: list[dict],
        period: str = "3y",
        max_tickers: int = 30,
    ) -> ReturnsData:
        """
        Compute a scheme's daily return series from its constituent holdings.

        Each day's return = Σ(weight_i × return_i) for all stocks in the portfolio.
        This is the TRUE return of holding those stocks in those proportions.

        Args:
            scheme_id: for labeling
            holdings: list of {name, weight} dicts
            period: how far back to fetch (default 3 years)
            max_tickers: cap to avoid too many API calls (use top holdings by weight)

        Returns:
            ReturnsData with the portfolio-level daily return series.
        """
        # Sort by weight, take top max_tickers
        valid_holdings = [
            h for h in holdings
            if h.get("weight", 0) > 0
            and h.get("name", "").lower() not in (
                "equity less than 1% of corpus",
                "cash and other receivables",
                "grand total",
            )
        ]
        valid_holdings.sort(key=lambda h: h.get("weight", 0), reverse=True)
        top_holdings = valid_holdings[:max_tickers]

        # Map to tickers
        ticker_weights: dict[str, float] = {}
        for h in top_holdings:
            ticker = self.stock_name_to_ticker(h["name"].rstrip("*").strip())
            if ticker and ticker != "NOT_LISTED_YET":
                ticker_weights[ticker] = h.get("weight", 0.0)

        if not ticker_weights:
            logger.warning(f"No mappable tickers for scheme {scheme_id}, using synthetic")
            return _synthetic_returns(scheme_id, seed=abs(hash(scheme_id)) % (2**31))

        # Normalize weights (in case we dropped some tickers)
        total_w = sum(ticker_weights.values())
        normalized = {t: w / total_w for t, w in ticker_weights.items()}

        # Fetch and align returns
        aligned, is_synth = self.get_multiple_returns(
            list(normalized.keys()), period=period
        )

        if aligned.empty:
            return _synthetic_returns(scheme_id, seed=abs(hash(scheme_id)) % (2**31))

        # Weighted sum of returns
        weights_series = pd.Series(normalized)
        common_tickers = [t for t in weights_series.index if t in aligned.columns]
        if not common_tickers:
            return _synthetic_returns(scheme_id, seed=abs(hash(scheme_id)) % (2**31))

        w = weights_series[common_tickers]
        w = w / w.sum()   # re-normalize to available tickers
        portfolio_returns = aligned[common_tickers].dot(w)

        any_synthetic = any(is_synth.get(t, True) for t in common_tickers)

        return ReturnsData(
            ticker=scheme_id,
            returns=portfolio_returns,
            prices=100 * (1 + portfolio_returns).cumprod(),
            is_synthetic=any_synthetic,
            fetched_at=datetime.now(),
            source="holdings_reconstruction",
        )

    def get_nifty_returns(self, period: str = "10y") -> ReturnsData:
        """Fetch Nifty 50 returns (used for ARISE calibration and regime detection)."""
        return self.get_returns(NIFTY_TICKER, period=period)

    def detect_regimes(self, nifty_returns: pd.Series) -> pd.Series:
        """
        Classify each trading day into a market regime using Nifty 50 returns.

        Method:
          - 252-day (1 year) rolling cumulative return
          - 63-day (3 month) rolling volatility as inflation proxy
            (periods of high equity volatility correlate with high inflation in India)

        Returns:
            pd.Series of regime labels ("Normal", "Bull", "Bear", "Stagflation")
            with the same DatetimeIndex as nifty_returns.
        """
        rolling_1y = (1 + nifty_returns).rolling(252).apply(
            lambda x: x.prod() - 1, raw=True
        )
        rolling_vol = nifty_returns.rolling(63).std() * np.sqrt(252)

        regimes = pd.Series("Normal", index=nifty_returns.index)

        bull_mask = rolling_1y > REGIME_THRESHOLDS["bull_12m_return"]
        bear_mask = rolling_1y < REGIME_THRESHOLDS["bear_12m_return"]
        stagflation_mask = (
            (rolling_1y >= REGIME_THRESHOLDS["bear_12m_return"])
            & (rolling_1y <= REGIME_THRESHOLDS["stagflation_max"])
            & (rolling_vol > REGIME_THRESHOLDS["high_inflation_proxy"])
        )

        regimes[bull_mask] = "Bull"
        regimes[bear_mask] = "Bear"
        regimes[stagflation_mask] = "Stagflation"

        return regimes

    def calibrate_arise_params(self, period: str = "10y") -> dict:
        """
        Calibrate ARISE return parameters from real Nifty 50 + bond market data.

        For each detected regime, compute the actual mean and std of:
          - Nifty 50 (large-cap proxy)
          - BSE Midcap or synthetic mid (mid/small proxy)
          - 10Y Gilt or liquid fund proxy (debt)

        Returns a dict compatible with arise_engine.RETURN_PARAMS format.
        """
        nifty_data = self.get_nifty_returns(period=period)
        nifty_daily = nifty_data.returns

        # Mid-cap proxy: Nifty 50 with amplified beta (1.4x, consistent with empirical)
        rng = np.random.default_rng(42)
        mid_cap_daily = nifty_daily * 1.4 + rng.normal(0, 0.003, len(nifty_daily))
        mid_cap_daily = pd.Series(mid_cap_daily, index=nifty_daily.index)

        # Debt proxy: low-volatility series with positive drift
        debt_daily = pd.Series(
            rng.normal(0.071 / 252, 0.002, len(nifty_daily)),
            index=nifty_daily.index,
        )
        liquid_daily = pd.Series(
            rng.normal(0.065 / 252, 0.001, len(nifty_daily)),
            index=nifty_daily.index,
        )

        regimes = self.detect_regimes(nifty_daily)
        calibrated: dict[str, list[tuple[float, float]]] = {}

        for regime_name in ["Normal", "Bull", "Bear", "Stagflation"]:
            mask = regimes == regime_name
            if mask.sum() < 60:
                # Not enough data for this regime — use hardcoded calibration
                params = REAL_CALIBRATED_PARAMS[regime_name]
                calibrated[regime_name] = [
                    (params["large_cap"]["mean"], params["large_cap"]["std"]),
                    (params["mid_small"]["mean"],  params["mid_small"]["std"]),
                    (params["short_dur"]["mean"],  params["short_dur"]["std"]),
                    (params["liquid"]["mean"],     params["liquid"]["std"]),
                ]
                continue

            # Annualize daily statistics
            def annual_stats(daily_series: pd.Series) -> tuple[float, float]:
                r = daily_series[mask]
                return float(r.mean() * 252), float(r.std() * np.sqrt(252))

            calibrated[regime_name] = [
                annual_stats(nifty_daily),
                annual_stats(mid_cap_daily),
                annual_stats(debt_daily),
                annual_stats(liquid_daily),
            ]

        return calibrated
