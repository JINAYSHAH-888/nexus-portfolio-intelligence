"""
A JSON response class that cannot emit invalid JSON.

Python's `json.dumps` defaults to `allow_nan=True`, which serialises float NaN
and Infinity as the bare tokens `NaN`, `Infinity`, `-Infinity`. None of those
are valid JSON (RFC 8259), and `JSON.parse` in every browser throws on them.

This is not hypothetical here. The numeric stack produces NaN/Inf legitimately:

  * `ARISEResult.milevsky_ruin_prob` and friends DEFAULT to float("nan").
  * A correlation against a zero-variance series (an overnight debt fund with a
    near-flat NAV) is 0/0 -> NaN, and lands straight in
    `PortfolioRiskResult.correlation_matrix`.
  * Sharpe/Sortino divide by a volatility that can be ~0 for the same reason.

Without this, a perfectly correct 200 response would arrive at the browser and
blow up in `JSON.parse` -- the worst kind of bug, because the server logs look
clean. We convert the non-finite values to `null` at the serialisation boundary,
which is the honest representation: "this statistic is undefined here", not 0.0
(which would be a silent, plausible-looking lie).
"""
from __future__ import annotations

import math
from typing import Any

from fastapi.responses import JSONResponse


def sanitize(obj: Any) -> Any:
    """Recursively replace non-finite floats with None."""
    if isinstance(obj, float):
        return None if (math.isnan(obj) or math.isinf(obj)) else obj
    if isinstance(obj, dict):
        return {k: sanitize(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [sanitize(v) for v in obj]
    return obj


class SafeJSONResponse(JSONResponse):
    """Default response class for the app. Sanitises, then forbids NaN outright."""

    def render(self, content: Any) -> bytes:
        import json

        return json.dumps(
            sanitize(content),
            ensure_ascii=False,
            allow_nan=False,  # belt and braces: raise rather than emit invalid JSON
            separators=(",", ":"),
        ).encode("utf-8")
