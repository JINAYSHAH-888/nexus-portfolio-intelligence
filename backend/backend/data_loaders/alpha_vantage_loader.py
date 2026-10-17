"""
Alpha Vantage Integration — Financial Statements and Fundamental Data.

PURPOSE:
  Provides fundamental analysis data for stocks held in mutual fund schemes.
  This data enriches the PLOS engine with two additional scoring layers:

    L07_fundamental_quality
        ROE > 20% = excellent quality
        Debt/Equity < 0.5 = low leverage
        Revenue growth YoY = business momentum
        High-quality holdings increase scheme quality score
        Two schemes with similar quality profiles = higher overlap risk

    L08_valuation_bucket
        P/E ratio bucket: Value (<15), Blend (15-25), Growth (>25)
        Two schemes in same valuation bucket = higher style overlap
        Detected via Alpha Vantage PERatio field

DATA FETCHED PER STOCK:
  From OVERVIEW endpoint:
    - PERatio (Price-to-Earnings)
    - PriceToBookRatio (P/B)
    - ReturnOnEquityTTM (ROE)
    - QuarterlyRevenueGrowthYOY
    - MarketCapitalization
    - Sector / Industry
    - 52WeekHigh / 52WeekLow

  From INCOME_STATEMENT endpoint (if needed):
    - Annual EPS growth
    - Operating margin trend

API LIMITS (free tier):
  5 calls per minute, 500 calls per day.
  Strategy: cache in Redis for 90 days (fundamentals are quarterly).
  Only fetch top-10 holdings per scheme → max 100 unique stocks.
  With 90-day cache: ~1-2 API calls per unique stock per quarter.
"""
from __future__ import annotations

from backend.core.http_client import SSL_CONTEXT  # one shared TLS context; per-client creation segfaults in worker threads

import asyncio
import logging
from enum import Enum
from typing import NamedTuple

logger = logging.getLogger(__name__)

AV_BASE = "https://www.alphavantage.co/query"
AV_CACHE_TTL = 86400 * 90      # 90 days
AV_RATE_DELAY = 12.5            # 5 calls/min = 12.5s between calls


class ValuationBucket(str, Enum):
    VALUE   = "value"       # P/E < 15
    BLEND   = "blend"       # P/E 15–25
    GROWTH  = "growth"      # P/E > 25
    UNKNOWN = "unknown"


class QualityTier(str, Enum):
    HIGH     = "high"       # ROE > 20%, D/E < 0.5
    MEDIUM   = "medium"
    LOW      = "low"
    UNKNOWN  = "unknown"


class FundamentalData(NamedTuple):
    ticker: str
    pe_ratio: float | None
    pb_ratio: float | None
    roe: float | None
    debt_to_equity: float | None
    market_cap_cr: float | None
    revenue_growth_yoy: float | None
    sector: str | None
    industry: str | None
    week_52_high: float | None
    week_52_low: float | None
    valuation_bucket: ValuationBucket
    quality_tier: QualityTier
    is_cached: bool


def _classify_valuation(pe: float | None) -> ValuationBucket:
    if pe is None: return ValuationBucket.UNKNOWN
    if pe < 15:    return ValuationBucket.VALUE
    if pe <= 25:   return ValuationBucket.BLEND
    return ValuationBucket.GROWTH


def _classify_quality(roe: float | None, de: float | None) -> QualityTier:
    if roe is None and de is None: return QualityTier.UNKNOWN
    roe = roe or 0.0
    de  = de  or 999.0
    if roe > 0.20 and de < 0.5:  return QualityTier.HIGH
    if roe > 0.10 and de < 1.5:  return QualityTier.MEDIUM
    return QualityTier.LOW


class AlphaVantageLoader:
    """
    Fetches company fundamentals from Alpha Vantage.
    Results cached in Redis for 90 days to stay within free tier limits.
    """

    def __init__(self) -> None:
        from backend.core.config import settings
        self._api_key = settings.ALPHA_VANTAGE_API_KEY

    def is_configured(self) -> bool:
        return bool(self._api_key)

    def _av_symbol(self, ticker: str) -> str:
        """RELIANCE.NS → RELIANCE.BSE (Alpha Vantage uses BSE suffix for Indian stocks)"""
        return ticker.replace(".NS", ".BSE").replace(".BO", ".BSE")

    async def get_fundamentals(self, ticker: str) -> FundamentalData | None:
        """
        Get fundamental data for a stock.
        Checks Redis cache first (90-day TTL).
        Falls back to API call if not cached.
        """
        from backend.infrastructure.redis_client import cache_get, cache_set

        cache_key = f"av:fundamentals:{ticker}"
        cached = await cache_get(cache_key)
        if cached:
            return FundamentalData(
                ticker=cached["ticker"],
                pe_ratio=cached.get("pe_ratio"),
                pb_ratio=cached.get("pb_ratio"),
                roe=cached.get("roe"),
                debt_to_equity=cached.get("debt_to_equity"),
                market_cap_cr=cached.get("market_cap_cr"),
                revenue_growth_yoy=cached.get("revenue_growth_yoy"),
                sector=cached.get("sector"),
                industry=cached.get("industry"),
                week_52_high=cached.get("week_52_high"),
                week_52_low=cached.get("week_52_low"),
                valuation_bucket=ValuationBucket(cached.get("valuation_bucket", "unknown")),
                quality_tier=QualityTier(cached.get("quality_tier", "unknown")),
                is_cached=True,
            )

        if not self.is_configured():
            logger.debug(f"Alpha Vantage not configured — skipping fundamentals for {ticker}")
            return None

        data = await self._fetch_overview(ticker)

        if data:
            payload = {
                "ticker": data.ticker,
                "pe_ratio": data.pe_ratio,
                "pb_ratio": data.pb_ratio,
                "roe": data.roe,
                "debt_to_equity": data.debt_to_equity,
                "market_cap_cr": data.market_cap_cr,
                "revenue_growth_yoy": data.revenue_growth_yoy,
                "sector": data.sector,
                "industry": data.industry,
                "week_52_high": data.week_52_high,
                "week_52_low": data.week_52_low,
                "valuation_bucket": data.valuation_bucket.value,
                "quality_tier": data.quality_tier.value,
            }
            await cache_set(cache_key, payload, ttl=AV_CACHE_TTL)

        return data

    @staticmethod
    def _yfinance_fundamentals(ticker: str) -> FundamentalData | None:
        """
        Fallback fundamentals from yfinance's Ticker.info -- weaker coverage
        than Alpha Vantage's OVERVIEW endpoint (no explicit revenue_growth_yoy
        field, industry taxonomy differs) but real numbers for the fields
        that matter most to L07/L08 (P/E, P/B, ROE, D/E), and free/no rate
        limit -- useful when Alpha Vantage is unconfigured, rate-limited
        (5 calls/min on the free tier), or simply down.
        """
        try:
            import yfinance as yf
            info = yf.Ticker(ticker).info
            if not info or info.get("regularMarketPrice") is None:
                return None

            pe = info.get("trailingPE")
            pb = info.get("priceToBook")
            roe = info.get("returnOnEquity")             # already a decimal fraction
            de = info.get("debtToEquity")
            de = (de / 100.0) if de is not None else None  # yfinance reports D/E as a percent
            mcap = info.get("marketCap")
            mcap_cr = (mcap / 1e7) if mcap is not None else None   # INR -> Cr

            return FundamentalData(
                ticker=ticker, pe_ratio=pe, pb_ratio=pb, roe=roe, debt_to_equity=de,
                market_cap_cr=mcap_cr, revenue_growth_yoy=info.get("revenueGrowth"),
                sector=info.get("sector"), industry=info.get("industry"),
                week_52_high=info.get("fiftyTwoWeekHigh"), week_52_low=info.get("fiftyTwoWeekLow"),
                valuation_bucket=_classify_valuation(pe), quality_tier=_classify_quality(roe, de),
                is_cached=False,
            )
        except Exception as e:
            logger.warning(f"yfinance fundamentals fallback failed for {ticker}: {type(e).__name__}: {e}")
            return None

    async def get_fundamentals_with_fallback(self, ticker: str) -> tuple[FundamentalData | None, str]:
        """
        Try Alpha Vantage first (richer fields); if it's unconfigured,
        rate-limited, or the call fails, fall back to yfinance's Ticker.info
        instead of returning nothing to the PLOS L07/L08 layers.

        Returns (data, source) where source is one of:
          "alpha_vantage", "yfinance_fallback", "unavailable"
        """
        data = await self.get_fundamentals(ticker)
        if data is not None:
            return data, "alpha_vantage"

        logger.info(f"{ticker}: Alpha Vantage unavailable, falling back to yfinance fundamentals.")
        fallback = self._yfinance_fundamentals(ticker)
        if fallback is not None:
            return fallback, "yfinance_fallback"
        return None, "unavailable"

    async def _fetch_overview(self, ticker: str) -> FundamentalData | None:
        """Call Alpha Vantage OVERVIEW endpoint and parse response."""
        av_symbol = self._av_symbol(ticker)
        try:
            import httpx
            async with httpx.AsyncClient(timeout=15.0, verify=SSL_CONTEXT) as client:
                resp = await client.get(AV_BASE, params={
                    "function": "OVERVIEW",
                    "symbol": av_symbol,
                    "apikey": self._api_key,
                })
                resp.raise_for_status()
                data = resp.json()

            if not data or "Symbol" not in data:
                # Rate limit response looks like {"Note": "..."}
                if "Note" in data or "Information" in data:
                    logger.warning(f"Alpha Vantage rate limit hit for {ticker}")
                else:
                    logger.debug(f"Alpha Vantage: no data for {av_symbol}")
                return None

            def safe_float(key: str) -> float | None:
                val = data.get(key, "None")
                try:
                    f = float(val) if val not in ("None", "N/A", "-", "", None) else None
                    return f
                except (ValueError, TypeError):
                    return None

            market_cap_usd = safe_float("MarketCapitalization")
            market_cap_cr = round(market_cap_usd / 8_300_000, 2) if market_cap_usd else None

            pe = safe_float("PERatio")
            roe = safe_float("ReturnOnEquityTTM")
            de = safe_float("DebtToEquityRatio") if "DebtToEquityRatio" in data else None

            return FundamentalData(
                ticker=ticker,
                pe_ratio=pe,
                pb_ratio=safe_float("PriceToBookRatio"),
                roe=roe,
                debt_to_equity=de,
                market_cap_cr=market_cap_cr,
                revenue_growth_yoy=safe_float("QuarterlyRevenueGrowthYOY"),
                sector=data.get("Sector") or None,
                industry=data.get("Industry") or None,
                week_52_high=safe_float("52WeekHigh"),
                week_52_low=safe_float("52WeekLow"),
                valuation_bucket=_classify_valuation(pe),
                quality_tier=_classify_quality(roe, de),
                is_cached=False,
            )

        except Exception as e:
            logger.warning(f"Alpha Vantage fetch failed for {ticker}: {type(e).__name__}: {e}")
            return None

    async def get_bulk_fundamentals(
        self,
        tickers: list[str],
        max_api_calls: int = 5,
    ) -> dict[str, FundamentalData]:
        """
        Fetch fundamentals for multiple tickers.
        Cached tickers are FREE (no API call). API calls are rate-limited to max_api_calls.
        """
        results: dict[str, FundamentalData] = {}
        api_calls = 0

        for ticker in tickers:
            from backend.infrastructure.redis_client import cache_get
            cached_raw = await cache_get(f"av:fundamentals:{ticker}")
            if cached_raw:
                fd = await self.get_fundamentals(ticker)
                if fd:
                    results[ticker] = fd
                continue

            if api_calls >= max_api_calls:
                logger.debug(f"AV rate limit cap ({max_api_calls} calls) reached. Skipping {ticker}.")
                continue

            fd = await self._fetch_overview(ticker)
            if fd:
                results[ticker] = fd
                api_calls += 1
                if api_calls < max_api_calls:
                    await asyncio.sleep(AV_RATE_DELAY)

        logger.info(f"Alpha Vantage: {len(results)} fundamentals ({api_calls} API calls, rest from cache)")
        return results

    def compute_quality_score(self, fundamentals: dict[str, FundamentalData]) -> float:
        """
        Compute average quality score (0..1) across a holdings set.
        Used as the raw score for PLOS layer L07_fundamental_quality.

        Scoring:
          QualityTier.HIGH    → 1.0
          QualityTier.MEDIUM  → 0.5
          QualityTier.LOW     → 0.1
          QualityTier.UNKNOWN → 0.5 (neutral)
        """
        tier_scores = {
            QualityTier.HIGH: 1.0,
            QualityTier.MEDIUM: 0.5,
            QualityTier.LOW: 0.1,
            QualityTier.UNKNOWN: 0.5,
        }
        scores = [tier_scores[fd.quality_tier] for fd in fundamentals.values()]
        return sum(scores) / len(scores) if scores else 0.5

    def compute_valuation_overlap(
        self,
        fundamentals_a: dict[str, FundamentalData],
        fundamentals_b: dict[str, FundamentalData],
    ) -> float:
        """
        Compute valuation overlap between two scheme's holdings.
        Used as PLOS layer L08_valuation_bucket.

        High overlap = both schemes heavily in Growth stocks
        or both heavily in Value stocks (same style = higher correlation risk).
        Returns 0..1 similarity score.
        """
        if not fundamentals_a or not fundamentals_b:
            return 0.5  # neutral

        def bucket_dist(funds: dict[str, FundamentalData]) -> dict[str, float]:
            buckets: dict[str, int] = {"value": 0, "blend": 0, "growth": 0, "unknown": 0}
            for fd in funds.values():
                buckets[fd.valuation_bucket.value] += 1
            total = sum(buckets.values())
            return {k: v / total for k, v in buckets.items()}

        dist_a = bucket_dist(fundamentals_a)
        dist_b = bucket_dist(fundamentals_b)

        # Cosine similarity between valuation distributions
        import math
        dot = sum(dist_a.get(k, 0) * dist_b.get(k, 0) for k in ["value", "blend", "growth"])
        mag_a = math.sqrt(sum(v**2 for v in dist_a.values()))
        mag_b = math.sqrt(sum(v**2 for v in dist_b.values()))

        if mag_a == 0 or mag_b == 0:
            return 0.5
        return float(dot / (mag_a * mag_b))
