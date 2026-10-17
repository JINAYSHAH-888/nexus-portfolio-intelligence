"""
Loads the verified stock-name -> Upstox NSE instrument_key map built by
scripts/build_upstox_equity_map.py.

Same discipline as mf_mapping.py: this module only ever reads the committed,
build-time-verified result. It never re-matches names at request time.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from functools import lru_cache

from backend.core.config import settings

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class UpstoxEquityMapping:
    stock_name: str
    instrument_key: str | None
    isin: str | None
    trading_symbol: str | None
    lot_size: int | None

    @property
    def is_resolved(self) -> bool:
        return self.instrument_key is not None


@lru_cache(maxsize=1)
def _raw() -> dict[str, dict]:
    if not settings.EQUITY_MAP_PATH.exists():
        logger.warning(
            "%s does not exist -- live equity prices for stock holdings are "
            "unavailable. Run scripts/build_upstox_equity_map.py.",
            settings.EQUITY_MAP_PATH,
        )
        return {}
    with open(settings.EQUITY_MAP_PATH, encoding="utf-8") as f:
        return json.load(f)


def get_mapping(stock_name: str) -> UpstoxEquityMapping | None:
    row = _raw().get(stock_name)
    if row is None:
        return None
    return UpstoxEquityMapping(
        stock_name=stock_name,
        instrument_key=row.get("instrument_key"),
        isin=row.get("isin"),
        trading_symbol=row.get("trading_symbol"),
        lot_size=row.get("lot_size"),
    )


def instrument_key_for(stock_name: str) -> str | None:
    m = get_mapping(stock_name)
    return m.instrument_key if m and m.is_resolved else None


def reset_cache() -> None:
    _raw.cache_clear()
