"""
The rewritten `market.download_prices` task: Upstox-first for every stock with
a verified instrument_key, per-ticker fallback to yfinance for the rest --
and, critically, a graceful degrade to 100% yfinance when Upstox isn't
configured at all, so this integration can never make price collection fail
harder than it did before Upstox existed.
"""
from __future__ import annotations

from datetime import date

import pytest

from backend.tasks.market_tasks import _period_to_from_date


class TestPeriodParsing:
    def test_years_are_subtracted_from_today(self):
        result = _period_to_from_date("5y", today=date(2026, 7, 14))
        assert result == date(2021, 7, 14)

    def test_ten_years_parses(self):
        result = _period_to_from_date("10y", today=date(2026, 7, 14))
        assert result == date(2016, 7, 14)

    def test_handles_leap_day_gracefully(self):
        """Feb 29 minus a non-multiple-of-4 year count has no exact target
        date -- must not raise, must fall back to Feb 28."""
        result = _period_to_from_date("5y", today=date(2024, 2, 29))
        assert result == date(2019, 2, 28)

    def test_unsupported_period_string_is_a_clear_error(self):
        with pytest.raises(ValueError, match="Unsupported period"):
            _period_to_from_date("5 years")
        with pytest.raises(ValueError, match="Unsupported period"):
            _period_to_from_date("max")


class TestDownloadPricesTask:
    def test_degrades_to_pure_yfinance_when_upstox_is_not_configured(
        self, monkeypatch, tmp_path
    ):
        """
        THE CORE GUARANTEE. Upstox being unconfigured (no service account, no
        token) must not make this task fail or skip tickers -- it must fall
        back to exactly the pre-Upstox yfinance behaviour for every ticker.
        """
        import json

        from backend.core.config import settings
        from backend.data_loaders import yfinance_pipeline
        from backend.infrastructure import timescale
        from backend.tasks import market_tasks

        ticker_map_path = tmp_path / "stocks_ticker_map.json"
        ticker_map_path.write_text(json.dumps({"nse_mapped": {"Fake Ltd": "FAKE.NS"}}))

        monkeypatch.setattr(settings, "UPSTOX_SERVICE_ACCOUNT_USER_ID", None)
        monkeypatch.setattr(settings, "TICKER_MAP_PATH", ticker_map_path)
        monkeypatch.setattr(market_tasks, "_all_tickers", lambda: ["FAKE.NS"])
        monkeypatch.setattr(timescale, "init_timescale", lambda: None)

        yfinance_calls = []

        class FakeResult:
            error = None
            rows_stored = 42

        def fake_run_ticker_pipeline(ticker, period):
            yfinance_calls.append(ticker)
            return FakeResult()

        monkeypatch.setattr(
            yfinance_pipeline, "run_ticker_pipeline", fake_run_ticker_pipeline
        )
        # No stock has a resolved Upstox mapping in this test -> every ticker
        # takes the fallback path regardless of the service-account check.
        monkeypatch.setattr(
            market_tasks, "_stock_names_with_upstox_mapping", lambda: {}
        )

        result = market_tasks.download_prices.run(period="5y")

        assert yfinance_calls == ["FAKE.NS"]
        assert result["upstox_ok"] == 0
        assert result["yfinance_ok"] == 1
        assert result["tickers_ok"] == 1

    def test_upstox_success_does_not_call_yfinance_for_that_ticker(self, monkeypatch, tmp_path):
        """A ticker that succeeds via Upstox must not ALSO hit yfinance --
        that would be wasted quota and duplicate (idempotent, but pointless) writes."""
        import json

        from backend.core.config import settings
        from backend.data_loaders import yfinance_pipeline
        from backend.infrastructure import timescale
        from backend.tasks import market_tasks

        ticker_map_path = tmp_path / "stocks_ticker_map.json"
        ticker_map_path.write_text(json.dumps({"nse_mapped": {"OK Ltd": "OK.NS"}}))

        monkeypatch.setattr(settings, "UPSTOX_SERVICE_ACCOUNT_USER_ID", "SVC1")
        monkeypatch.setattr(settings, "TICKER_MAP_PATH", ticker_map_path)
        monkeypatch.setattr(market_tasks, "_all_tickers", lambda: ["OK.NS"])
        monkeypatch.setattr(timescale, "init_timescale", lambda: None)
        monkeypatch.setattr(
            market_tasks, "_stock_names_with_upstox_mapping", lambda: {"OK Ltd": "NSE_EQ|X"}
        )
        monkeypatch.setattr(
            market_tasks,
            "_download_one_via_upstox",
            lambda *a, **k: (10, None),  # success, 10 rows
        )

        yfinance_calls = []

        def fail_if_called(*a, **k):
            yfinance_calls.append(a)
            raise AssertionError("should not have fallen back to yfinance")

        monkeypatch.setattr(yfinance_pipeline, "run_ticker_pipeline", fail_if_called)

        result = market_tasks.download_prices.run(period="5y")

        assert yfinance_calls == []
        assert result["upstox_ok"] == 1
        assert result["rows_stored"] == 10
