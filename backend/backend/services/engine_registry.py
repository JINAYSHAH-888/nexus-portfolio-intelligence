"""
Process-wide singletons for the analytical engines.

Every engine parses `schemes_metadata.json` (35 schemes) and
`sample_holdings.json` (~6k lines) in its constructor, and PLOS/Risk also
memoise per-scheme return series. Constructing them per request would re-read
and re-parse both files on every call and throw away every cached price
series -- turning a warm ~200ms PLOS call into a cold multi-second one.

They are safe to share: after construction the loaded metadata is read-only,
and the caches are write-once memo dicts. A double-build race would at worst
recompute the same value twice, but the lock below removes even that.

Access is via functions rather than module-level instances so that importing
this module never does I/O -- which keeps `pytest --collect-only` and
`alembic` fast, and lets tests inject `force_synthetic=True` before anything
is built.
"""
from __future__ import annotations

import logging
import threading
from typing import Any

from backend.core.config import settings

logger = logging.getLogger(__name__)

_lock = threading.Lock()
_instances: dict[str, Any] = {}


def _get_or_build(key: str, factory) -> Any:
    inst = _instances.get(key)
    if inst is not None:
        return inst
    with _lock:
        # Re-check inside the lock: another thread may have built it while we
        # were waiting.
        inst = _instances.get(key)
        if inst is None:
            logger.info("Building engine singleton: %s", key)
            inst = factory()
            _instances[key] = inst
        return inst


def get_filter_engine():
    from backend.engines.filter_engine import FilterEngine

    return _get_or_build(
        "filter",
        lambda: FilterEngine(settings.SCHEMES_PATH),
    )


def get_overlap_engine():
    from backend.engines.overlap_engine import OverlapEngine

    return _get_or_build(
        "overlap",
        lambda: OverlapEngine(
            schemes_path=settings.SCHEMES_PATH,
            holdings_path=settings.HOLDINGS_PATH,
        ),
    )


def get_plos_engine():
    from backend.engines.plos_engine import PLOSEngine

    return _get_or_build(
        "plos",
        lambda: PLOSEngine(
            schemes_path=settings.SCHEMES_PATH,
            holdings_path=settings.HOLDINGS_PATH,
            price_period=settings.PRICE_PERIOD,
            force_synthetic=settings.MARKET_DATA_FORCE_SYNTHETIC,
        ),
    )


def get_risk_engine():
    from backend.engines.risk_engine import RiskEngine

    return _get_or_build(
        "risk",
        lambda: RiskEngine(
            schemes_path=settings.SCHEMES_PATH,
            holdings_path=settings.HOLDINGS_PATH,
            price_period=settings.PRICE_PERIOD,
            force_synthetic=settings.MARKET_DATA_FORCE_SYNTHETIC,
        ),
    )


def get_stats_builder():
    from backend.analytics.portfolio_statistics import PortfolioStatisticsBuilder

    # 10y (not 3y) on purpose: ARISE shrinks the sample CAGR toward a prior with
    # weight n/(n+tau), so a longer history earns more trust in the shrinkage
    # step and produces a less prior-dominated projection.
    return _get_or_build(
        "stats",
        lambda: PortfolioStatisticsBuilder(
            schemes_path=settings.SCHEMES_PATH,
            holdings_path=settings.HOLDINGS_PATH,
            price_period=settings.STATS_PRICE_PERIOD,
            force_synthetic=settings.MARKET_DATA_FORCE_SYNTHETIC,
        ),
    )


def get_market_loader():
    from backend.data_loaders.market_data import MarketDataLoader

    return _get_or_build(
        "market",
        lambda: MarketDataLoader(
            ticker_map_path=settings.TICKER_MAP_PATH,
            force_synthetic=settings.MARKET_DATA_FORCE_SYNTHETIC,
        ),
    )


def get_live_price_loader():
    from backend.data_loaders.live_price_loader import LivePriceLoader

    return _get_or_build("live_price", LivePriceLoader)


def get_amfi_loader():
    from backend.data_loaders.amfi_loader import AMFILoader

    return _get_or_build("amfi", AMFILoader)


def warm_up() -> None:
    """
    Build the cheap, deterministic engines at startup so the first real request
    doesn't pay the JSON-parse cost.

    Deliberately does NOT touch PLOS/Risk/stats: their constructors spin up a
    MarketDataLoader, and warming them would fire network calls during boot --
    turning a provider outage into a failed deploy.
    """
    get_filter_engine()
    get_overlap_engine()
    logger.info("Engine warm-up complete (filter, overlap).")


def reset() -> None:
    """Drop every singleton. Used by tests that change settings between cases."""
    with _lock:
        _instances.clear()
