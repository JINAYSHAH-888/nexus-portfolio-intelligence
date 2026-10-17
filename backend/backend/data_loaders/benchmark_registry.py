"""
Each scheme's OWN declared benchmark, and an honest account of how well we can
actually track it.

THE PROBLEM THIS SOLVES
-----------------------
`risk_engine` measured beta, alpha, tracking error and the Information Ratio
against Nifty 50 for EVERY scheme. Of the 35 schemes in this platform, exactly
6 are benchmarked to a Nifty-50 index. The other 29 are measured against an
index their manager is not managing to -- a small-cap fund judged against
large-cap Nifty 50 produces a beta that describes nothing, and SEBI's mandated
Information Ratio disclosure is explicitly relative to the scheme's OWN
benchmark.

WHY THIS IS A REGISTRY AND NOT A TICKER MAP
-------------------------------------------
The declared benchmarks are TOTAL RETURN indices (TRI) -- they reinvest
dividends. Free market data gives us PRICE indices, which do not. For Indian
equity that gap is roughly 1-1.5% a year, always in the benchmark's favour, so
a fund measured against a price proxy will look better than it is: alpha is
overstated by about the dividend yield.

That is a real limitation and the platform's convention is to state such things
rather than bury them (see core/assumptions.py, and /market/pnl returning
`pnl_available: false` with a reason instead of a plausible zero). So every
mapping carries a `quality` describing exactly what the investor is getting:

    exact         the proxy IS the declared index
    price_proxy   right index, price-return instead of total-return
    close_proxy   a closely-related index (e.g. Nifty 100 for a BSE 100 TRI)
    none          no defensible free proxy exists

CRISIL and NIFTY DEBT INDICES
-----------------------------
Thirteen of the 31 benchmarks are CRISIL/NIFTY debt indices. None is available
from any free source. Rather than substitute an equity index -- which is what
using Nifty 50 for a liquid fund amounted to -- these map to `none`, and the
benchmark-relative metrics come back None with `benchmark_reason` explaining
why. A missing number that says why it is missing is worth more than a
confident number that is wrong.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class BenchmarkMapping:
    """How a declared benchmark is actually sourced."""
    declared: str                 # exactly as the factsheet states it
    ticker: str | None            # what we can really fetch, None if nothing fits
    quality: str                  # exact | price_proxy | close_proxy | none
    note: str                     # one line an investor could read

    @property
    def usable(self) -> bool:
        return self.ticker is not None


_TRI = "Declared benchmark is a TOTAL RETURN index; this proxy is PRICE-only, so it excludes dividends (~1-1.5%/yr for Indian equity). Alpha against it is overstated by roughly that much."
_DEBT = "CRISIL/NIFTY debt indices are not published on any free data source. Benchmark-relative metrics are withheld rather than computed against an unrelated equity index."

# Declared benchmark -> what we can honestly source for it.
_REGISTRY: dict[str, BenchmarkMapping] = {}


def _add(declared: str, ticker: str | None, quality: str, note: str) -> None:
    _REGISTRY[declared.strip().upper()] = BenchmarkMapping(declared, ticker, quality, note)


# ── Indian broad equity ────────────────────────────────────────────────────────
_add("Nifty 500 TRI", "^CRSLDX", "price_proxy", _TRI)
_add("BSE 500 TRI", "^CRSLDX", "close_proxy",
     "BSE 500 is not freely available; Nifty 500 (price) is the closest broad proxy. " + _TRI)
_add("NIFTY 500 Multicap 50:25:25 TRI", "^CRSLDX", "close_proxy",
     "Multicap 50:25:25 has a fixed large/mid/small split that plain Nifty 500 does not. " + _TRI)
_add("BSE 100 TRI", "^CNX100", "close_proxy",
     "BSE 100 is not freely available; Nifty 100 (price) is the closest large-cap proxy. " + _TRI)
_add("BSE 200 TRI", "^CNX100", "close_proxy",
     "BSE 200 is not freely available; Nifty 100 (price) is the nearest available. " + _TRI)
_add("Nifty LargeMidcap 250 TRI", "^CNX100", "close_proxy",
     "No free LargeMidcap 250 series; Nifty 100 (price) covers the large-cap half only. " + _TRI)

# ── Indian size segments ───────────────────────────────────────────────────────
_add("NIFTY Midcap 150 TRI", "^NSEMDCP50", "close_proxy",
     "Nifty Midcap 50 (price) is a narrower slice of the same segment. " + _TRI)
_add("Nifty Smallcap 250 TRI", None, "none",
     "No free small-cap index series is reliably available. Measuring a small-cap fund "
     "against a large-cap index would produce a beta that describes nothing, so "
     "benchmark-relative metrics are withheld.")

# ── Indian sector / thematic ───────────────────────────────────────────────────
_add("Nifty Financial Services TRI", "^NSEBANK", "close_proxy",
     "Bank Nifty (price) covers banks but not the wider financial-services universe. " + _TRI)
_add("BSE Healthcare TRI", None, "none", "No free healthcare index series available.")
_add("Nifty India Consumption TRI", None, "none", "No free consumption index series available.")
_add("Nifty Infrastructure TRI", None, "none", "No free infrastructure index series available.")
_add("Nifty MNC TRI", None, "none", "No free MNC index series available.")

# ── International ──────────────────────────────────────────────────────────────
_add("S&P 500 TRI", "^GSPC", "price_proxy", _TRI)
_add("S&P Japan 500 TRI", "^N225", "close_proxy",
     "Nikkei 225 (price) is a different Japanese index but the closest freely available. " + _TRI)
_add("Taiwan Capitalization Weighted Stock Index (TAIEX) TRI", "^TWII", "price_proxy", _TRI)

# ── Debt: deliberately unmapped ────────────────────────────────────────────────
for _debt in (
    "CRISIL Banking and PSU Debt A-II Index",
    "CRISIL Credit Risk Debt B-II Index",
    "CRISIL Liquid Overnight Index",
    "CRISIL Long Duration Debt A-III Index",
    "CRISIL Low Duration Debt A-I Index",
    "CRISIL Medium Duration Debt A-III Index",
    "CRISIL Short Duration Debt A-II Index",
    "CRISIL Ultra Short Duration Debt A-I Index",
    "NIFTY All Duration G-Sec Index",
    "NIFTY Composite Debt Index A-III",
    "NIFTY Corporate Bond Index A-II",
    "NIFTY Liquid Index A-I",
    "NIFTY Medium to Long Duration Debt Index A-III",
    "NIFTY Money Market Index A-I",
    "NIFTY Short Duration Debt Index A-II",
):
    _add(_debt, None, "none", _DEBT)


UNKNOWN = BenchmarkMapping(
    declared="(unknown)", ticker=None, quality="none",
    note="This scheme's declared benchmark is not in the registry, so no "
         "benchmark-relative metric is computed for it.",
)


def resolve(declared_benchmark: str | None) -> BenchmarkMapping:
    """Declared benchmark string -> how we source it. Never raises."""
    if not declared_benchmark:
        return UNKNOWN
    hit = _REGISTRY.get(declared_benchmark.strip().upper())
    if hit is None:
        logger.info("Benchmark not in registry: %r", declared_benchmark)
        return BenchmarkMapping(declared_benchmark, None, "none", UNKNOWN.note)
    return hit


def coverage(schemes_path: str | Path) -> dict:
    """
    How much of the scheme universe we can actually benchmark. Used by the
    assumptions endpoint and by tests that guard against silent regressions
    in the registry.
    """
    with open(Path(schemes_path), encoding="utf-8") as f:
        rows = json.load(f)
    rows = rows if isinstance(rows, list) else list(rows.values())

    by_quality: dict[str, int] = {}
    for r in rows:
        q = resolve(r.get("benchmark")).quality
        by_quality[q] = by_quality.get(q, 0) + 1
    return {
        "schemes": len(rows),
        "by_quality": by_quality,
        "usable": sum(v for k, v in by_quality.items() if k != "none"),
    }
