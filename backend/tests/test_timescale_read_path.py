"""
The TimescaleDB read path: stored bars reaching the engines, and the
provenance label that survives the trip.

Before this path existed, `market.download_prices` wrote every bar it
collected -- Upstox candles first, yfinance for the rest -- into
`tsdb.stock_prices`, and NOTHING in the serving path ever read that table.
`MarketDataLoader` went parquet -> yfinance -> synthetic, so no stored bar
could reach a PLOS score, a risk metric or an ARISE projection. These tests
pin the two halves of the fix:

  * stored bars DO reach `get_returns`, carrying which feed wrote them;
  * they are refused whenever using them would be WORSE than the live fetch
    the loader would otherwise have done -- which is the only way adding a
    source can be a safe change.
"""

from __future__ import annotations

from datetime import datetime, timedelta

import numpy as np
import pandas as pd
import pytest

from backend.data_loaders import market_data as md
from backend.data_loaders.market_data import (
    MIN_HISTORY_DAYS,
    TIMESCALE_MAX_STALENESS_DAYS,
    MarketDataLoader,
    _feed_label,
    _fetch_from_timescale,
    _period_start,
)


def _stored_frame(n_rows: int, *, newest: datetime, source: str = "upstox") -> pd.DataFrame:
    """The shape `timescale.read_prices` returns: Close/Return/Source, DatetimeIndex."""
    idx = pd.bdate_range(end=newest, periods=n_rows)
    rng = np.random.default_rng(42)
    close = 100 * np.exp(np.cumsum(rng.normal(0, 0.01, n_rows)))
    return pd.DataFrame(
        {"Close": close, "Return": rng.normal(0, 0.01, n_rows), "Source": source},
        index=idx,
    )


class TestPeriodStart:
    def test_parses_year_strings(self):
        start = _period_start("5y", today=datetime(2026, 7, 14))
        assert start == datetime(2021, 7, 14)

    def test_leap_day_does_not_raise(self):
        assert _period_start("5y", today=datetime(2024, 2, 29)) == datetime(2019, 2, 28)

    def test_unparseable_period_is_no_lower_bound_not_an_error(self):
        """An odd period string must not be the reason a ticker degrades to
        synthetic data -- it just means 'read the whole stored history'."""
        assert _period_start("max") is None
        assert _period_start("ytd") is None


class TestFeedLabel:
    def test_single_feed(self):
        assert _feed_label(_stored_frame(10, newest=datetime.now())) == "upstox"

    def test_mixed_feeds_are_named_not_hidden(self):
        """One ticker's window filled by both feeds is NORMAL -- the Upstox
        backfill only covers verified instrument_keys. The label must say so
        rather than silently picking a winner."""
        df = _stored_frame(10, newest=datetime.now())
        df.loc[df.index[:5], "Source"] = "yfinance"
        assert _feed_label(df) == "mixed(upstox+yfinance)"

    def test_unlabelled_frame_is_unknown_not_a_guess(self):
        df = _stored_frame(10, newest=datetime.now()).drop(columns=["Source"])
        assert _feed_label(df) == "unknown"

    def test_feed_label_names_no_tier(self):
        """The same bars can arrive from the database on one call and the
        parquet cache on the next; the feed name must not imply either."""
        label = _feed_label(_stored_frame(10, newest=datetime.now()))
        assert "timescale" not in label
        assert "cache" not in label


class TestStalenessGuard:
    """THE core safety property. The stored table is filled by a nightly job
    that may be behind, failing, or never scheduled. Serving months-old bars
    in preference to a live fetch would make the engines quietly staler than
    before this read path existed."""

    def test_stale_stored_history_is_refused(self, monkeypatch):
        stale = datetime.now() - timedelta(days=TIMESCALE_MAX_STALENESS_DAYS + 30)
        monkeypatch.setattr(
            "backend.infrastructure.timescale.read_prices",
            lambda ticker, start=None: _stored_frame(MIN_HISTORY_DAYS + 50, newest=stale),
        )
        assert _fetch_from_timescale("RELIANCE.NS", "5y") is None

    def test_fresh_stored_history_is_served(self, monkeypatch):
        monkeypatch.setattr(
            "backend.infrastructure.timescale.read_prices",
            lambda ticker, start=None: _stored_frame(
                MIN_HISTORY_DAYS + 50, newest=datetime.now()
            ),
        )
        out = _fetch_from_timescale("RELIANCE.NS", "5y")
        assert out is not None
        assert list(out.columns) == ["Close", "Return", "Source"]

    def test_short_history_is_refused(self, monkeypatch):
        """Too few bars to compute a covariance matrix worth trusting."""
        monkeypatch.setattr(
            "backend.infrastructure.timescale.read_prices",
            lambda ticker, start=None: _stored_frame(
                MIN_HISTORY_DAYS - 10, newest=datetime.now()
            ),
        )
        assert _fetch_from_timescale("RELIANCE.NS", "5y") is None

    def test_absent_table_is_not_an_error(self, monkeypatch):
        monkeypatch.setattr(
            "backend.infrastructure.timescale.read_prices",
            lambda ticker, start=None: None,
        )
        assert _fetch_from_timescale("RELIANCE.NS", "5y") is None


class TestReadPricesIsSafeWithoutPostgres:
    def test_declines_on_a_non_postgres_dsn(self):
        """The suite runs on SQLite, which has no tsdb schema. read_prices
        must return None rather than raising once per ticker."""
        from backend.core.config import settings
        from backend.infrastructure.timescale import read_prices

        assert settings.sync_database_url.startswith("sqlite")
        assert read_prices("RELIANCE.NS") is None


class TestGetReturnsOrdering:
    def test_stored_bars_reach_the_loader_with_their_source(self, monkeypatch, tmp_path):
        """THE POINT OF THE WHOLE CHANGE: an Upstox-written bar arrives at the
        engines' loader, labelled as Upstox."""
        monkeypatch.setattr(
            "backend.infrastructure.timescale.read_prices",
            lambda ticker, start=None: _stored_frame(
                MIN_HISTORY_DAYS + 50, newest=datetime.now(), source="upstox"
            ),
        )
        monkeypatch.setattr(
            md, "_fetch_from_yfinance", lambda t, p: pytest.fail("yfinance must not be reached")
        )

        result = MarketDataLoader(cache_dir=tmp_path).get_returns("RELIANCE.NS")
        assert result.source == "timescale:upstox"
        assert result.is_synthetic is False
        assert len(result.returns) >= MIN_HISTORY_DAYS

    def test_falls_through_to_yfinance_when_stored_history_is_unusable(
        self, monkeypatch, tmp_path
    ):
        """Adding a source must never REMOVE one."""
        monkeypatch.setattr(
            "backend.infrastructure.timescale.read_prices",
            lambda ticker, start=None: None,
        )
        live = pd.DataFrame(
            {"Close": [100.0] * 300, "Return": [0.001] * 300, "Source": "yfinance"},
            index=pd.bdate_range(end=datetime.now(), periods=300),
        )
        monkeypatch.setattr(md, "_fetch_from_yfinance", lambda t, p: live)

        result = MarketDataLoader(cache_dir=tmp_path).get_returns("RELIANCE.NS")
        assert result.source == "yfinance"
        assert result.is_synthetic is False

    def test_parquet_cache_still_wins_over_the_database(self, monkeypatch, tmp_path):
        """A local file read beats a database round trip; the new step sits
        BELOW the cache, not above it."""
        cached = pd.DataFrame(
            {"Close": [100.0] * 300, "Return": [0.001] * 300, "Source": "yfinance"},
            index=pd.bdate_range(end=datetime.now(), periods=300),
        )
        cached.to_parquet(md._cache_path("RELIANCE.NS", "5y", tmp_path))

        monkeypatch.setattr(
            "backend.infrastructure.timescale.read_prices",
            lambda ticker, start=None: pytest.fail("database must not be reached on a cache hit"),
        )

        result = MarketDataLoader(cache_dir=tmp_path).get_returns("RELIANCE.NS")
        # Served from the CACHE tier, carrying the feed that originally
        # produced the bars -- not "timescale:", a tier it never came from.
        assert result.source == "cache:yfinance"

    def test_cached_upstox_bars_still_name_upstox(self, monkeypatch, tmp_path):
        """Provenance must survive the round trip through parquet, or the
        label decays to whatever tier happened to serve it."""
        cached = pd.DataFrame(
            {"Close": [100.0] * 300, "Return": [0.001] * 300, "Source": "upstox"},
            index=pd.bdate_range(end=datetime.now(), periods=300),
        )
        cached.to_parquet(md._cache_path("RELIANCE.NS", "5y", tmp_path))

        result = MarketDataLoader(cache_dir=tmp_path).get_returns("RELIANCE.NS")
        assert result.source == "cache:upstox"

    def test_force_synthetic_still_short_circuits_everything(self, monkeypatch, tmp_path):
        monkeypatch.setattr(
            "backend.infrastructure.timescale.read_prices",
            lambda ticker, start=None: pytest.fail("must not read when forced synthetic"),
        )
        result = MarketDataLoader(cache_dir=tmp_path, force_synthetic=True).get_returns("X.NS")
        assert result.is_synthetic is True
        assert result.source == "synthetic"


class TestWriteSideCarriesSource:
    def test_normalise_frame_stamps_every_row(self):
        from backend.infrastructure.timescale import SOURCE_UPSTOX, _normalise_price_frame

        idx = pd.bdate_range(end=datetime.now(), periods=5)
        df = pd.DataFrame({"Close": [1.0, 2.0, 3.0, 4.0, 5.0], "Volume": [1] * 5}, index=idx)
        rows = _normalise_price_frame("X.NS", df, SOURCE_UPSTOX)

        assert rows
        assert {r["source"] for r in rows} == {"upstox"}

    def test_source_defaults_to_unknown_for_un_updated_callers(self):
        """The module docstring promises a two-positional-argument contract;
        `source` is keyword-only so that promise still holds."""
        from backend.infrastructure.timescale import UNKNOWN_SOURCE, _normalise_price_frame

        idx = pd.bdate_range(end=datetime.now(), periods=3)
        df = pd.DataFrame({"Close": [1.0, 2.0, 3.0]}, index=idx)
        rows = _normalise_price_frame("X.NS", df)

        assert {r["source"] for r in rows} == {UNKNOWN_SOURCE}


class TestCacheDirIsHonoured:
    def test_constructor_cache_dir_is_actually_used(self, monkeypatch, tmp_path):
        """`cache_dir` was stored and never read, so every caller that passed
        one -- tests above all -- silently shared the real price_cache."""
        monkeypatch.setattr(
            "backend.infrastructure.timescale.read_prices",
            lambda ticker, start=None: _stored_frame(
                MIN_HISTORY_DAYS + 50, newest=datetime.now()
            ),
        )
        monkeypatch.setattr(
            md, "_fetch_from_yfinance", lambda t, p: pytest.fail("should not reach yfinance")
        )

        MarketDataLoader(cache_dir=tmp_path).get_returns("RELIANCE.NS")

        written = list(tmp_path.glob("*.parquet"))
        assert written, "the fetched frame should have been cached into cache_dir"
        assert written[0].name == "RELIANCE_NS_5y.parquet"
