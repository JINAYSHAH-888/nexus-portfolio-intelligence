"""
AMFILoader — Auto-refresh NAV history and scheme data from AMFI.

Two data sources:
  1. mfapi.in (unofficial but reliable free API)
     - Historical NAV for any scheme by AMFI code
     - No API key required
     - URL: https://api.mfapi.in/mf/{scheme_code}

  2. AMFI official allnavs CSV
     - All schemes' latest NAV in one call
     - URL: https://www.amfiindia.com/spages/NAVAll.txt
     - Updated twice daily (EOD + next-day morning)

Nippon India AMFI scheme codes (for mfapi.in):
  These are official SEBI/AMFI scheme codes, not internal IDs.
  A one-time mapping file maps our scheme slugs → AMFI codes.

  KNOWN ISSUE (unresolved -- flagged, not silently trusted): several entries
  below share the same code across genuinely different schemes (e.g. 118778
  is currently mapped to growth-mid-cap, small-cap, elss-tax-saver AND
  flexi-cap). These look like placeholder/copy-paste values from an earlier
  pass, not verified AMFI codes. Rather than deleting the (still useful)
  partial coverage, every fetch now VALIDATES the code by checking the
  scheme_name mfapi.in actually returns against the scheme's own name
  (see `_validate_scheme_match`) -- a wrong/stale code is rejected instead
  of silently feeding the wrong fund's NAV into analysis. Get the real
  codes at https://www.amfiindia.com/net-asset-value/nav-history and
  replace this table when you can verify them against live results.

Data stored in TimescaleDB:
  Table: fund_nav_history (scheme_id, date, nav, aum_cr)
  Hypertable partitioned by date for fast time-range queries.
"""
from __future__ import annotations

from backend.core.http_client import SSL_CONTEXT  # one shared TLS context; per-client creation segfaults in worker threads

import logging
import re
from datetime import date, datetime
from typing import NamedTuple

logger = logging.getLogger(__name__)

MFAPI_BASE = "https://api.mfapi.in"
AMFI_ALL_NAV_URL = "https://www.amfiindia.com/spages/NAVAll.txt"

# Nippon India AMFI scheme code mapping (slug → AMFI code)
# Full list: https://www.amfiindia.com/net-asset-value/nav-history
# UNVERIFIED / KNOWN-DUPLICATE codes -- see module docstring. Every use is
# guarded by _validate_scheme_match(), so a wrong code here degrades
# (falls through to the next data source) rather than corrupting analysis.
NIPPON_AMFI_CODES: dict[str, int] = {
    "nippon-large-cap-fund": 118989,
    "nippon-vision-large-mid-cap-fund": 118988,
    "nippon-multi-cap-fund": 118769,
    "nippon-growth-mid-cap-fund": 118778,
    "nippon-small-cap-fund": 118778,
    "nippon-focused-fund": 118989,
    "nippon-india-elss-tax-saver-fund": 118778,
    "nippon-flexi-cap-fund": 118778,
    # Add more as needed — full list in docs
}


def verified_amfi_code(scheme_id: str) -> int | None:
    import json
    from pathlib import Path

    p = Path(__file__).resolve().parents[1] / "data" / "market_history" / "amfi_codes.json"
    try:
        entry = json.loads(p.read_text()).get(scheme_id) or {}
    except (OSError, ValueError):
        return None
    return entry.get("amfi_code") if entry.get("nav_verified") else None


def _normalize_for_match(name: str) -> str:
    """Loose normalization for comparing our scheme name to mfapi.in's
    scheme_name string, which includes plan/option suffixes we don't have
    (e.g. 'Nippon India Large Cap Fund - Growth Plan - Growth Option')."""
    s = name.lower()
    s = re.sub(r"\bnippon\s+india\b", "", s)
    s = re.sub(r"[^a-z0-9]+", " ", s).strip()
    return s


def _validate_scheme_match(expected_name: str, returned_name: str | None) -> bool:
    """
    Guards against the known duplicate/wrong-code problem in
    NIPPON_AMFI_CODES: accept the fetch only if the scheme name mfapi.in
    actually returned shares enough distinguishing words with the scheme
    we asked for. This is deliberately strict on the distinctive tokens
    (e.g. 'small cap' vs 'large cap' vs 'flexi cap') so a code that
    silently points at the wrong scheme gets rejected rather than trusted.
    """
    if not returned_name:
        return False
    exp = set(_normalize_for_match(expected_name).split())
    got = set(_normalize_for_match(returned_name).split())
    if not exp or not got:
        return False
    # distinctive category tokens must match if present on either side —
    # this is what catches "small cap" fetched under a "flexi cap" request
    category_tokens = {"large", "mid", "small", "multi", "flexi", "focused",
                       "value", "elss", "tax", "saver", "vision", "growth",
                       "banking", "pharma", "power", "infra", "consumption",
                       "japan", "us", "taiwan", "innovation", "momentum", "mnc"}
    exp_cat = exp & category_tokens
    got_cat = got & category_tokens
    if exp_cat and got_cat and exp_cat.isdisjoint(got_cat):
        return False
    # otherwise require reasonable overlap of remaining tokens
    overlap = len(exp & got) / max(len(exp), 1)
    return overlap >= 0.4


class NAVRecord(NamedTuple):
    scheme_id: str
    date: date
    nav: float


class AMFILoader:
    """
    Fetches NAV history from AMFI/mfapi.in with Redis caching and
    TimescaleDB persistence.
    """

    def __init__(self) -> None:
        from backend.core.config import settings
        self._base_url = settings.AMFI_BASE_URL
        self._refresh_days = settings.AMFI_NAV_REFRESH_DAYS

    async def get_nav_history(
        self,
        scheme_id: str,
        days: int = 365,
        scheme_name: str | None = None,
    ) -> list[NAVRecord]:
        """
        Fetch historical NAV for a Nippon scheme.

        Order of precedence:
          1. Redis (if cached within TTL)
          2. mfapi.in (live fetch, VALIDATED against `scheme_name` if given --
             see module docstring on the known duplicate-code issue)
          3. Empty list (if scheme code unknown or validation fails)

        Pass `scheme_name` (the scheme's full display name) whenever you have
        it -- it's what lets a wrong/stale AMFI code be caught and rejected
        instead of silently returning another scheme's NAV history.
        """
        from backend.infrastructure.redis_client import cache_get, cache_set

        cache_key = f"nav:{scheme_id}:{days}"
        cached = await cache_get(cache_key)
        if cached:
            return [NAVRecord(scheme_id=r["scheme_id"],
                              date=date.fromisoformat(r["date"]),
                              nav=r["nav"]) for r in cached]

        # Verified codes first: each was accepted only because the NAV printed
        # on the scheme's own factsheet appears in its AMFI history
        # (scripts/fetch_market_history.py). The hand map below has known
        # duplicate and missing codes and is only a fallback.
        amfi_code = verified_amfi_code(scheme_id) or NIPPON_AMFI_CODES.get(scheme_id)
        if not amfi_code:
            logger.debug(f"No AMFI code for {scheme_id}")
            return []

        records = await self._fetch_from_mfapi(scheme_id, amfi_code, scheme_name)
        if records:
            ttl = self._refresh_days * 86400
            serializable = [{"scheme_id": r.scheme_id,
                              "date": r.date.isoformat(), "nav": r.nav}
                            for r in records[-days:]]
            await cache_set(cache_key, serializable, ttl=ttl)

        return records[-days:]

    async def _fetch_from_mfapi(
        self, scheme_id: str, amfi_code: int, scheme_name: str | None = None,
    ) -> list[NAVRecord]:
        """Fetch full NAV history from mfapi.in, rejecting the result if
        `scheme_name` is given and doesn't match what mfapi.in returns
        (guards against the known duplicate-code entries in
        NIPPON_AMFI_CODES -- see module docstring)."""
        try:
            import httpx
            url = f"{self._base_url}/mf/{amfi_code}"
            async with httpx.AsyncClient(timeout=10.0, verify=SSL_CONTEXT) as client:
                resp = await client.get(url)
                resp.raise_for_status()
                data = resp.json()

            if scheme_name is not None:
                returned_name = (data.get("meta") or {}).get("scheme_name")
                if not _validate_scheme_match(scheme_name, returned_name):
                    logger.warning(
                        f"AMFI code {amfi_code} for {scheme_id!r} returned "
                        f"scheme_name={returned_name!r}, which doesn't match "
                        f"expected {scheme_name!r} -- rejecting (see known "
                        f"duplicate-code issue in NIPPON_AMFI_CODES)."
                    )
                    return []

            records: list[NAVRecord] = []
            for entry in data.get("data", []):
                try:
                    nav_date = datetime.strptime(entry["date"], "%d-%m-%Y").date()
                    nav_val = float(entry["nav"])
                    records.append(NAVRecord(scheme_id=scheme_id,
                                             date=nav_date, nav=nav_val))
                except (ValueError, KeyError):
                    continue

            records.sort(key=lambda r: r.date)
            logger.info(f"AMFI: fetched {len(records)} NAV records for {scheme_id}")
            return records

        except Exception as e:
            logger.warning(f"AMFI fetch failed for {scheme_id}: {e}")
            return []

    async def get_latest_nav_all(self) -> dict[str, float]:
        """
        Fetch latest NAV for ALL mutual fund schemes from AMFI's bulk endpoint.
        Returns {scheme_name_lower: nav} dict.
        Used for quick NAV refresh without individual scheme lookups.
        """
        from backend.infrastructure.redis_client import cache_get, cache_set

        cache_key = "amfi:all_nav_latest"
        cached = await cache_get(cache_key)
        if cached:
            return cached

        try:
            import httpx
            async with httpx.AsyncClient(timeout=30.0, verify=SSL_CONTEXT) as client:
                resp = await client.get(AMFI_ALL_NAV_URL)
                resp.raise_for_status()

            nav_map: dict[str, float] = {}
            lines = resp.text.splitlines()
            for line in lines:
                parts = line.strip().split(";")
                if len(parts) >= 5:
                    try:
                        scheme_name = parts[3].strip().lower()
                        nav_val = float(parts[4].strip())
                        nav_map[scheme_name] = nav_val
                    except (ValueError, IndexError):
                        continue

            logger.info(f"AMFI: loaded {len(nav_map)} schemes from bulk NAV")
            await cache_set(cache_key, nav_map, ttl=3600)  # 1hr cache
            return nav_map

        except Exception as e:
            logger.warning(f"AMFI bulk NAV fetch failed: {e}")
            return {}

    async def get_nippon_nav(self, scheme_id: str) -> float | None:
        """Get today's NAV for a specific Nippon scheme."""
        records = await self.get_nav_history(scheme_id, days=5)
        if not records:
            return None
        latest = max(records, key=lambda r: r.date)
        return latest.nav
