"""
Regression tests for the infrastructure contracts and the bugs fixed while
building this layer. These are pure unit tests -- no HTTP, no DB.
"""
from __future__ import annotations

import asyncio
import json
import math

import pytest

from backend.core.async_utils import run_async_safely
from backend.core.json import SafeJSONResponse, sanitize


# ══════════════════════════════════════════════════════════════════════════════
# The debt-model bug (see scripts/normalize_scheme_metadata.py)
# ══════════════════════════════════════════════════════════════════════════════
class TestDebtDurationNormalisation:
    """
    `analytics/debt_returns_model.py::_anchor_vol` reads `modified_duration_years`
    and scales it into a volatility. The factsheet data ships
    `modified_duration: "10.22 Years"` -- a STRING under a DIFFERENT key -- so
    _anchor_vol found None for all 15 debt schemes and silently fell back to
    CREDIT_LIQUIDITY_VOL_FLOOR (0.002 = 20bps).

    Net effect before the fix: a 10.22-year-duration gilt fund was modelled as
    *exactly as volatile as an overnight fund*. That understates debt risk by
    ~60x on the long end, and it propagates into the covariance matrix, the
    VaR/CVaR dashboard, and ARISE's ruin probability.
    """

    def test_duration_strings_parse_to_years(self):
        from scripts.normalize_scheme_metadata import parse_duration_years

        assert parse_duration_years("1 Days") == pytest.approx(1 / 365)
        assert parse_duration_years("45 Days") == pytest.approx(45 / 365)
        assert parse_duration_years("10.22 Years") == pytest.approx(10.22)
        assert parse_duration_years("6 Months") == pytest.approx(0.5)
        assert parse_duration_years(None) is None
        assert parse_duration_years("garbage") is None
        assert parse_duration_years(3.5) == pytest.approx(3.5)  # already numeric

    def test_ytm_strings_parse_to_decimals(self):
        from scripts.normalize_scheme_metadata import parse_pct_decimal

        assert parse_pct_decimal("7.90%") == pytest.approx(0.079)
        assert parse_pct_decimal("5.39%") == pytest.approx(0.0539)
        assert parse_pct_decimal(7.9) == pytest.approx(0.079)   # bare percentage
        assert parse_pct_decimal(0.079) == pytest.approx(0.079)  # already a decimal
        assert parse_pct_decimal(None) is None

    def test_every_debt_scheme_now_has_the_keys_the_engine_reads(self):
        from backend.core.config import settings

        schemes = json.loads(settings.SCHEMES_PATH.read_text())
        debt = [s for s in schemes if s["instrument_type"] == "debt"]
        assert len(debt) == 15

        for s in debt:
            assert s.get("modified_duration_years") is not None, s["id"]
            assert s.get("ytm_decimal") is not None, s["id"]
            # The original factsheet strings must survive untouched -- this is a
            # purely additive normalisation, not a destructive rewrite.
            assert isinstance(s["modified_duration"], str)
            assert isinstance(s["ytm"], str)

    def test_long_duration_funds_are_no_longer_modelled_as_riskless(self):
        from backend.analytics.debt_returns_model import (
            CREDIT_LIQUIDITY_VOL_FLOOR,
            _anchor_vol,
        )
        from backend.core.config import settings

        schemes = {s["id"]: s for s in json.loads(settings.SCHEMES_PATH.read_text())}

        gilt_vol, gilt_src = _anchor_vol(schemes["nippon-gilt-fund"])
        overnight_vol, _ = _anchor_vol(schemes["nippon-overnight-fund"])

        # The fix: duration now drives the vol, instead of everything hitting the floor.
        assert gilt_src == "duration_scaled"
        assert gilt_vol > CREDIT_LIQUIDITY_VOL_FLOOR * 10
        assert gilt_vol == pytest.approx(8.66 * 0.012, rel=1e-3)  # ~10.4%

        # An overnight fund genuinely IS near-riskless -- it should still sit at
        # the floor. The fix must not inflate risk indiscriminately.
        assert overnight_vol == pytest.approx(CREDIT_LIQUIDITY_VOL_FLOOR)

        # And the ordering that was previously collapsed now holds.
        assert gilt_vol > overnight_vol

    def test_debt_return_series_are_differentiated_across_schemes(self):
        """
        Before the fix every debt scheme got identical (flat-6%, 20bps) params,
        so their return series were statistically indistinguishable -- which
        would make any debt-vs-debt correlation meaningless.
        """
        from backend.analytics.debt_returns_model import debt_scheme_returns
        from backend.core.config import settings

        schemes = {s["id"]: s for s in json.loads(settings.SCHEMES_PATH.read_text())}

        gilt = debt_scheme_returns(schemes["nippon-gilt-fund"], n_days=1260, seed=7)
        overnight = debt_scheme_returns(schemes["nippon-overnight-fund"], n_days=1260, seed=7)

        assert gilt.returns.std() > overnight.returns.std() * 5

    def test_normalisation_is_idempotent(self):
        from backend.core.config import settings
        from scripts.normalize_scheme_metadata import normalise

        schemes = json.loads(settings.SCHEMES_PATH.read_text())
        _once, changes = normalise([dict(s) for s in schemes])
        assert changes == [], "already-normalised data should need no changes"


# ══════════════════════════════════════════════════════════════════════════════
# The sync/async bridge the engines depend on
# ══════════════════════════════════════════════════════════════════════════════
class TestRunAsyncSafely:
    async def _coro(self, value: int = 42) -> int:
        await asyncio.sleep(0)
        return value

    def test_works_with_no_running_loop(self):
        """Celery workers / CLI / sync tests: the cheap asyncio.run path."""
        assert run_async_safely(self._coro(7)) == 7

    async def test_works_from_inside_a_running_loop(self):
        """
        The dangerous case. An engine calling this from inside a coroutine would
        hit `RuntimeError: asyncio.run() cannot be called from a running event
        loop` under a naive implementation, or deadlock under a careless one.
        It must offload to the bridge thread and return normally.
        """
        result = await asyncio.to_thread(lambda: run_async_safely(self._coro(99)))
        assert result == 99

        # And directly, with a loop live in THIS thread:
        assert run_async_safely(self._coro(123)) == 123

    def test_propagates_exceptions(self):
        async def explode():
            raise ValueError("boom")

        with pytest.raises(ValueError, match="boom"):
            run_async_safely(explode())

    def test_times_out_instead_of_hanging_forever(self):
        """
        The loaders do network I/O. An unbounded wait would hang a request
        forever if a provider black-holes the connection.
        """
        async def forever():
            await asyncio.sleep(30)

        with pytest.raises((asyncio.TimeoutError, TimeoutError)):
            run_async_safely(forever(), timeout=0.05)


# ══════════════════════════════════════════════════════════════════════════════
# JSON safety
# ══════════════════════════════════════════════════════════════════════════════
class TestJsonSanitisation:
    def test_nan_and_inf_become_null(self):
        assert sanitize(float("nan")) is None
        assert sanitize(float("inf")) is None
        assert sanitize(float("-inf")) is None
        assert sanitize(1.5) == 1.5

    def test_sanitises_nested_structures(self):
        payload = {
            "corr": [[1.0, float("nan")], [float("nan"), 1.0]],
            "meta": {"sharpe": float("inf"), "ok": 0.3},
        }
        out = sanitize(payload)
        assert out["corr"] == [[1.0, None], [None, 1.0]]
        assert out["meta"] == {"sharpe": None, "ok": 0.3}

    def test_rendered_body_is_strictly_valid_json(self):
        body = SafeJSONResponse(content={}).render({"x": float("nan"), "y": [float("inf")]})
        text = body.decode()
        assert "NaN" not in text and "Infinity" not in text
        # json.loads with parse_constant would raise on a bare NaN token.
        assert json.loads(text, parse_constant=lambda c: (_ for _ in ()).throw(
            AssertionError(f"invalid JSON constant: {c}")
        )) == {"x": None, "y": [None]}

    def test_zero_is_preserved(self):
        """0.0 must survive -- turning a real zero into null would be a bug of
        exactly the kind this class exists to prevent, in reverse."""
        out = sanitize({"a": 0.0, "b": -0.0})
        assert out["a"] == 0.0 and not math.isnan(out["a"])
