"""
Determines WHICH instrument keys the shared upstream feed should subscribe to.

Deliberately separated from the streamer/socket code below: this is a pure
function over data (given a list of currently-selected schemes' holdings and
weights, return a set of instrument keys), so it's testable without a
websocket, a Celery worker, or a database connection in the test process.

ONE UPSTREAM CONNECTION SERVES EVERY INVESTOR (see feed_worker.py's module
docstring for why). So the subscription set is the UNION of every currently
active user's selected schemes' underlying holdings -- not any single
investor's. Ranking matters when that union exceeds the V3 subscription cap
(2,000 keys in `full` mode): we shard by aggregate portfolio weight across all
users, not by scheme order or alphabetical instrument key, so the
highest-conviction, most-held names are the ones that stay live if something
has to be dropped.
"""
from __future__ import annotations

import logging
from collections import defaultdict
from dataclasses import dataclass

from backend.core.config import settings

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class SchemeSelectionSnapshot:
    """The minimal shape resolve_subscription_keys() needs from a
    SchemeSelection row -- decoupled from the ORM model so this stays testable
    with plain data, and so a sync worker process never has to import the
    async ORM machinery just to read a few fields."""

    scheme_ids: list[str]
    weights: dict[str, float]


def aggregate_holding_weights(
    selections: list[SchemeSelectionSnapshot],
    holdings_by_scheme: dict[str, list[dict]],
) -> dict[str, float]:
    """
    stock_name -> summed (scheme_weight_in_selection * holding_weight_in_scheme)
    across EVERY investor's current selection.

    This is a genuine aggregate, not "the first user's portfolio": two
    investors each holding Large Cap at very different overall weights both
    contribute to the same stock's combined score, which is what should decide
    subscription priority under the cap -- a name held (even thinly) by many
    investors deserves a live tick before one held heavily by a single investor
    who hasn't even looked at the app today.
    """
    scores: dict[str, float] = defaultdict(float)

    for sel in selections:
        for scheme_id in sel.scheme_ids:
            scheme_weight = sel.weights.get(scheme_id, 0.0)
            if scheme_weight <= 0:
                continue
            for holding in holdings_by_scheme.get(scheme_id, []):
                name = holding.get("name")
                holding_weight = float(holding.get("weight") or 0.0)
                if name and holding_weight > 0:
                    scores[name] += scheme_weight * holding_weight

    return dict(scores)


def resolve_subscription_keys(
    selections: list[SchemeSelectionSnapshot],
    holdings_by_scheme: dict[str, list[dict]],
    instrument_key_for_stock: dict[str, str | None],
    *,
    mode_limit: int = 2000,
) -> tuple[list[str], list[str]]:
    """
    Returns (instrument_keys, dropped_for_capacity).

    Benchmark indices (NIFTY 50 etc.) always make the cut -- they're a fixed,
    tiny cost and every risk computation in this platform depends on them.
    Everything else is ranked by aggregate weight and truncated at
    `mode_limit - len(benchmarks)`.
    """
    scores = aggregate_holding_weights(selections, holdings_by_scheme)

    ranked_stocks = sorted(scores.items(), key=lambda kv: kv[1], reverse=True)

    benchmarks = list(settings.UPSTOX_BENCHMARK_INSTRUMENT_KEYS)
    budget = max(mode_limit - len(benchmarks), 0)

    resolved: list[str] = []
    unresolved_stocks: list[str] = []
    for name, _score in ranked_stocks:
        key = instrument_key_for_stock.get(name)
        if key is None:
            unresolved_stocks.append(name)
            continue
        resolved.append(key)

    kept = resolved[:budget]
    dropped = resolved[budget:]

    if dropped:
        logger.warning(
            "Subscription set exceeds the %d-key cap: keeping the %d "
            "highest-conviction names, dropping %d.",
            mode_limit,
            len(kept),
            len(dropped),
        )
    if unresolved_stocks:
        logger.debug(
            "%d held stock(s) have no Upstox instrument mapping and were "
            "skipped: %s",
            len(unresolved_stocks),
            unresolved_stocks[:10],
        )

    return benchmarks + kept, dropped
