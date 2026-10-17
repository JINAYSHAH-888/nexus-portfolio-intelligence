"""
Loads the verified scheme_id -> Upstox instrument_key map built by
scripts/build_upstox_isin_map.py.

This module NEVER attempts to re-match names at runtime. Matching is a
build-time, human-reviewable process for a reason (see that script's
docstring for the real 63%-NAV-drift bug it exists to prevent); at runtime we
only ever read the committed, verified result.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from functools import lru_cache

from backend.core.config import settings

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class UpstoxMfMapping:
    scheme_id: str
    instrument_key: str | None
    match_method: str
    purchase_allowed: bool | None
    redemption_allowed: bool | None
    minimum_purchase_amount: float | None
    additional_purchase_amount: float | None

    @property
    def is_resolved(self) -> bool:
        return self.instrument_key is not None


@lru_cache(maxsize=1)
def _raw() -> dict[str, dict]:
    if not settings.ISIN_MAP_PATH.exists():
        logger.warning(
            "%s does not exist -- Upstox MF identity/tradability data is "
            "unavailable. Run scripts/build_upstox_isin_map.py.",
            settings.ISIN_MAP_PATH,
        )
        return {}
    with open(settings.ISIN_MAP_PATH, encoding="utf-8") as f:
        return json.load(f)


def get_mapping(scheme_id: str) -> UpstoxMfMapping | None:
    row = _raw().get(scheme_id)
    if row is None:
        return None
    return UpstoxMfMapping(
        scheme_id=scheme_id,
        instrument_key=row.get("instrument_key"),
        match_method=row.get("match_method", "unknown"),
        purchase_allowed=row.get("purchase_allowed"),
        redemption_allowed=row.get("redemption_allowed"),
        minimum_purchase_amount=row.get("minimum_purchase_amount"),
        additional_purchase_amount=row.get("additional_purchase_amount"),
    )


def all_mappings() -> dict[str, UpstoxMfMapping]:
    return {sid: get_mapping(sid) for sid in _raw()}


def reset_cache() -> None:
    """Used by tests / after re-running the build script in the same process."""
    _raw.cache_clear()
