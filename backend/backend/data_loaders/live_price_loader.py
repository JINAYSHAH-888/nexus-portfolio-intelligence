"""
LivePriceLoader — near-real-time NSE quotes via yfinance.

Replaces the removed Upstox integration. yfinance has no true push/live feed
(it's a pull-based REST source, ~15 min delayed during market hours), so this
is honestly DELAYED data, not a real-time tick stream -- every quote this
loader returns has `is_live=False` for that reason, and every consumer
downstream (dashboard, portfolio value, WebSocket) must surface that instead
of implying real-time.

This keeps the same "live-display data never feeds analytics" boundary the
Upstox integration had: this loader is for dashboard/portfolio-value/
WebSocket display only. ARISE, Risk, and PLOS continue to use
yfinance_pipeline.py's own historical daily-bar pull, independently cached.

No API key, no OAuth, no daily token refresh -- yfinance requires none of
that, which is the whole reason this is simpler than what it replaces.
"""
from __future__ import annotations

import logging
import warnings
from datetime import datetime, time, timezone
from typing import NamedTuple
from zoneinfo import ZoneInfo

logger = logging.getLogger(__name__)

IST = ZoneInfo("Asia/Kolkata")
MARKET_OPEN = time(9, 15)
MARKET_CLOSE = time(15, 30)


class LiveQuote(NamedTuple):
    ticker: str
    ltp: float
    open: float
    high: float
    low: float
    close_prev: float
    change: float
    change_pct: float
    volume: int
    timestamp: datetime
    is_live: bool     # always False here -- yfinance is delayed, never a true tick


def is_market_open() -> bool:
    now = datetime.now(IST)
    if now.weekday() >= 5:
        return False
    return MARKET_OPEN <= now.time() <= MARKET_CLOSE


class LivePriceLoader:
    """
    Usage:
        loader = LivePriceLoader()
        quote = await loader.get_quote("RELIANCE.NS")
        quotes = await loader.get_bulk_quotes(["RELIANCE.NS", "TCS.NS"])
    """

    async def get_quote(self, ticker: str) -> LiveQuote | None:
        """Single-ticker quote from yfinance's most recent daily bar."""
        return self._fetch_one(ticker)

    async def get_bulk_quotes(self, tickers: list[str]) -> dict[str, LiveQuote]:
        """Bulk fetch -- yfinance supports multi-ticker downloads in one call,
        so this is one network round trip regardless of list size (unlike
        Upstox's per-request instrument-key batching, but the same idea)."""
        if not tickers:
            return {}
        try:
            import yfinance as yf
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                data = yf.download(tickers, period="5d", progress=False,
                                   auto_adjust=True, group_by="ticker")
        except Exception as e:
            logger.warning(f"Bulk yfinance quote fetch failed: {type(e).__name__}: {e}")
            return {}

        out: dict[str, LiveQuote] = {}
        for ticker in tickers:
            try:
                sub = data[ticker] if len(tickers) > 1 else data
                sub = sub.dropna()
                if sub.empty:
                    continue
                out[ticker] = self._quote_from_bars(ticker, sub)
            except Exception as e:
                logger.debug(f"Could not extract quote for {ticker}: {e}")
                continue

        for ticker, quote in out.items():
            await self._publish(ticker, quote)
        return out

    def _fetch_one(self, ticker: str) -> LiveQuote | None:
        try:
            import yfinance as yf
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                data = yf.download(ticker, period="5d", progress=False, auto_adjust=True)
            if data is None or data.empty:
                return None
            return self._quote_from_bars(ticker, data)
        except Exception as e:
            logger.warning(f"yfinance quote failed for {ticker}: {type(e).__name__}: {e}")
            return None

    @staticmethod
    def _scalar(row, col):
        v = row[col]
        return float(v.iloc[0] if hasattr(v, "iloc") else v)

    def _quote_from_bars(self, ticker: str, bars) -> LiveQuote:
        last = bars.iloc[-1]
        prev = bars.iloc[-2] if len(bars) > 1 else last
        close = self._scalar(last, "Close")
        prev_close = self._scalar(prev, "Close")
        change = close - prev_close
        return LiveQuote(
            ticker=ticker, ltp=close,
            open=self._scalar(last, "Open"), high=self._scalar(last, "High"),
            low=self._scalar(last, "Low"), close_prev=prev_close,
            change=round(change, 2),
            change_pct=round((change / prev_close * 100) if prev_close else 0.0, 2),
            volume=int(self._scalar(last, "Volume")),
            timestamp=datetime.now(timezone.utc), is_live=False,
        )

    async def _publish(self, ticker: str, quote: LiveQuote) -> None:
        try:
            from backend.infrastructure.redis_client import publish
            await publish("live_prices", {
                "ticker": ticker, "ltp": quote.ltp,
                "change_pct": quote.change_pct, "volume": quote.volume,
                "ts": quote.timestamp.isoformat(), "is_live": False,
            })
        except Exception as e:
            logger.debug(f"Redis publish skipped for {ticker}: {e}")
