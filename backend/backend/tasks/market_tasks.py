"""
Background market-data refresh jobs.

These are the only place in the system that writes to the Timescale
hypertables. They are deliberately tolerant: a single dead ticker must not
abort a 221-ticker run, so failures are counted and reported, not raised.
Writes are idempotent (ON CONFLICT DO NOTHING), so a retried or overlapping
run cannot double-insert.
"""
from __future__ import annotations

import asyncio
import gzip
import json
import logging
import re
from datetime import date
from typing import Any

from backend.core.config import settings
from backend.tasks.celery_app import celery_app

logger = logging.getLogger(__name__)


def _all_tickers() -> list[str]:
    with open(settings.TICKER_MAP_PATH, encoding="utf-8") as f:
        data = json.load(f)
    return sorted(set(data.get("nse_mapped", {}).values()))


def _period_to_from_date(period: str, *, today: date | None = None) -> date:
    """
    Translates yfinance-style period strings ("5y", "10y", "1y") into an
    explicit from_date, since Upstox's Historical Candle Data V3 needs a real
    date range (yfinance accepts the period string directly and resolves it
    internally -- Upstox has no equivalent shorthand).
    """
    today = today or date.today()
    m = re.fullmatch(r"(\d+)y", period.strip().lower())
    if not m:
        raise ValueError(f"Unsupported period '{period}'; expected e.g. '5y', '10y'.")
    years = int(m.group(1))
    try:
        return today.replace(year=today.year - years)
    except ValueError:
        # Feb 29 on a non-leap target year.
        return today.replace(year=today.year - years, day=28)


def _stock_names_with_upstox_mapping() -> dict[str, str]:
    """stock_name -> instrument_key, for every holding with a VERIFIED Upstox
    mapping (see build_upstox_equity_map.py). Anything absent here falls back
    to yfinance -- this function never guesses a key."""
    from backend.integrations.upstox import equity_mapping

    with open(settings.TICKER_MAP_PATH, encoding="utf-8") as f:
        tickers = json.load(f)["nse_mapped"]
    out: dict[str, str] = {}
    for stock_name in tickers:
        key = equity_mapping.instrument_key_for(stock_name)
        if key:
            out[stock_name] = key
    return out


def _all_schemes() -> list[dict[str, Any]]:
    with open(settings.SCHEMES_PATH, encoding="utf-8") as f:
        return json.load(f)


def _download_one_via_upstox(
    stock_name: str, instrument_key: str, *, from_date: date, to_date: date
) -> tuple[int, str | None]:
    """
    Returns (rows_stored, error). Never raises -- a single bad ticker in a
    221-name batch must not abort the run, matching this module's existing
    tolerance policy (see the module docstring).
    """
    from backend.core.async_utils import run_async_safely
    from backend.core.config import settings as _settings
    from backend.infrastructure.timescale import SOURCE_UPSTOX, bulk_insert_prices
    from backend.integrations.upstox.candles import (
        candles_to_dataframe,
        chunk_date_range,
        get_historical_candles,
    )
    from backend.integrations.upstox.rest import UpstoxRestClient
    from backend.integrations.upstox.token_store import get_service_token_sync

    try:
        token = get_service_token_sync()
    except RuntimeError as e:
        return 0, f"upstox_unavailable: {e}"

    client = UpstoxRestClient(token, upstox_user_id=_settings.UPSTOX_SERVICE_ACCOUNT_USER_ID)

    all_candles = []
    try:
        for chunk_start, chunk_end in chunk_date_range(
            from_date, to_date, unit="days", interval=1
        ):
            candles = run_async_safely(
                get_historical_candles(
                    client,
                    instrument_key,
                    unit="days",
                    interval=1,
                    from_date=chunk_start,
                    to_date=chunk_end,
                )
            )
            all_candles.extend(candles)
    except Exception as e:
        return 0, str(e)

    df = candles_to_dataframe(all_candles)
    if df.empty:
        return 0, "no candles returned"

    with open(settings.TICKER_MAP_PATH, encoding="utf-8") as f:
        yf_ticker = json.load(f)["nse_mapped"].get(stock_name)
    if not yf_ticker:
        return 0, "no yfinance-style ticker key to store under"

    rows = bulk_insert_prices(yf_ticker, df, source=SOURCE_UPSTOX)
    return rows, None


@celery_app.task(
    name="market.download_prices",
    bind=True,
    max_retries=2,
    default_retry_delay=300,
)
def download_prices(self, period: str = "5y") -> dict[str, Any]:
    """
    Download + store daily OHLCV for every mapped NSE stock.

    UPSTOX-FIRST, YFINANCE-FALLBACK. For every stock with a verified Upstox
    instrument_key (build_upstox_equity_map.py; currently 211 of 221), history
    is pulled via Historical Candle Data V3 -- real, sourced, and not subject
    to yfinance's unofficial-API breakage risk. Anything without a verified
    mapping (the ~10 stale/renamed tickers already known from that mapper, or
    any Upstox failure for an individual name) falls back to the ORIGINAL
    yfinance pipeline, per ticker -- so total coverage never regresses versus
    before this integration existed.

    If the Upstox service account has no valid token today (not configured,
    or past the 03:30 IST daily reset), this degrades to the OLD, pre-Upstox
    behaviour -- 100% yfinance -- rather than failing the whole run. Upstox
    being unavailable must never take price data collection down entirely.

    Both paths write into the SAME `tsdb.stock_prices` table via the SAME
    `bulk_insert_prices` (idempotent, ON CONFLICT DO NOTHING), so nothing
    downstream has to branch on which feed a given day's bar came from -- but
    each bar is now LABELLED with its source, because a blended table whose
    provenance can't be recovered is exactly the kind of quiet assumption
    core/assumptions.py exists to prevent.
    """
    from backend.data_loaders.yfinance_pipeline import run_ticker_pipeline
    from backend.infrastructure.timescale import init_timescale

    tickers = _all_tickers()
    logger.info("download_prices: %d tickers, period=%s", len(tickers), period)

    try:
        init_timescale()
    except Exception as e:
        logger.error("Timescale init failed: %s", e)
        raise self.retry(exc=e) from e

    try:
        from_date = _period_to_from_date(period)
    except ValueError as e:
        raise ValueError(str(e)) from e
    to_date = date.today()

    upstox_map = _stock_names_with_upstox_mapping()
    with open(settings.TICKER_MAP_PATH, encoding="utf-8") as f:
        tickers_map = json.load(f)["nse_mapped"]
    ticker_to_name = {v: k for k, v in tickers_map.items()}

    upstox_ok = upstox_failed = 0
    yfinance_ok = yfinance_failed = 0
    rows_stored = 0
    failures: list[dict[str, str]] = []

    for ticker in tickers:
        stock_name = ticker_to_name.get(ticker)
        instrument_key = upstox_map.get(stock_name) if stock_name else None

        if instrument_key:
            rows, error = _download_one_via_upstox(
                stock_name, instrument_key, from_date=from_date, to_date=to_date
            )
            if error is None:
                upstox_ok += 1
                rows_stored += rows
                continue
            upstox_failed += 1
            logger.info(
                "Upstox candle fetch failed for %s (%s); falling back to yfinance: %s",
                ticker,
                instrument_key,
                error,
            )

        # Fallback: no Upstox mapping, or Upstox failed for this name.
        result = run_ticker_pipeline(ticker, period)
        if result.error is None:
            yfinance_ok += 1
            rows_stored += result.rows_stored
        else:
            yfinance_failed += 1
            failures.append({"ticker": ticker, "error": result.error})

    total_ok = upstox_ok + yfinance_ok
    logger.info(
        "download_prices complete: %d/%d ok (upstox=%d, yfinance=%d), %d rows stored",
        total_ok,
        len(tickers),
        upstox_ok,
        yfinance_ok,
        rows_stored,
    )

    return {
        "tickers_total": len(tickers),
        "tickers_ok": total_ok,
        "tickers_failed": len(tickers) - total_ok,
        "upstox_ok": upstox_ok,
        "upstox_fell_back_to_yfinance": upstox_failed,
        "yfinance_ok": yfinance_ok,
        "yfinance_failed": yfinance_failed,
        "rows_stored": rows_stored,
        "failures": failures[:20],
    }


@celery_app.task(name="market.download_nav", bind=True, max_retries=2, default_retry_delay=300)
def download_nav(self, days: int = 3650) -> dict[str, Any]:
    """Pull AMFI NAV history for all 35 schemes and persist it."""
    from backend.data_loaders.amfi_loader import AMFILoader
    from backend.infrastructure.timescale import bulk_insert_nav, init_timescale

    schemes = _all_schemes()

    try:
        init_timescale()
    except Exception as e:
        logger.error("Timescale init failed: %s", e)
        raise self.retry(exc=e) from e

    loader = AMFILoader()

    async def _run() -> dict[str, int]:
        out: dict[str, int] = {}
        for s in schemes:
            try:
                # scheme_name is passed so amfi_loader can name-validate the
                # response -- that check is what catches the known duplicate
                # AMFI-code bug where one scheme's code returns another's NAVs.
                records = await loader.get_nav_history(
                    s["id"], days=days, scheme_name=s["name"]
                )
                if records:
                    out[s["id"]] = bulk_insert_nav(
                        s["id"],
                        [{"date": r.date, "nav": r.nav} for r in records],
                    )
            except Exception as e:
                logger.warning("NAV fetch failed for %s: %s", s["id"], e)
        return out

    # Celery workers are sync processes with no running loop, so asyncio.run is
    # the right call here (not the run_async_safely bridge).
    stored = asyncio.run(_run())

    return {
        "schemes_total": len(schemes),
        "schemes_with_nav": len(stored),
        "rows_stored": sum(stored.values()),
    }


@celery_app.task(name="market.refresh_holdings")
def refresh_holdings() -> dict[str, Any]:
    """
    Re-parse AMC factsheet PDFs into holdings.

    amc_pdf_pipeline reads LOCAL PDF files only -- it does not fetch them from
    Nippon's site (CLAUDE.md §2). With no PDFs staged, this is correctly a
    no-op rather than a failure.
    """
    from pathlib import Path

    from backend.data_loaders.amc_pdf_pipeline import run_monthly_pipeline

    pdf_dir = Path(settings.REPORTS_DIR).parent / "factsheets"
    if not pdf_dir.exists() or not any(pdf_dir.glob("*.pdf")):
        logger.info("No factsheet PDFs staged at %s; nothing to refresh.", pdf_dir)
        return {
            "status": "skipped",
            "reason": f"No PDFs found at {pdf_dir}.",
            "pdfs_processed": 0,
        }

    result = run_monthly_pipeline(pdf_dir)
    return {"status": "completed", "result": str(result)}


def _diff_isin_map(old: dict[str, Any], new: dict[str, Any]) -> list[str]:
    """
    What actually changed between yesterday's map and today's -- an audit
    trail, not just a pass/fail. The two facts worth a human's attention:
    an instrument_key changing (should essentially never happen for an
    existing ISIN; worth a raised eyebrow if it does) and a purchase_allowed
    flip (a scheme opening or closing to new money is real product news --
    see the notification hook this feeds, once wired).
    """
    changes: list[str] = []
    for sid, new_row in new.items():
        old_row = old.get(sid)
        if old_row is None:
            changes.append(f"{sid}: newly mapped -> {new_row['instrument_key']}")
            continue
        if old_row.get("instrument_key") != new_row.get("instrument_key"):
            changes.append(
                f"{sid}: instrument_key changed "
                f"{old_row.get('instrument_key')} -> {new_row.get('instrument_key')}"
            )
        old_pa, new_pa = old_row.get("purchase_allowed"), new_row.get("purchase_allowed")
        if old_pa != new_pa:
            changes.append(f"{sid}: purchase_allowed {old_pa} -> {new_pa}")
    for sid in old:
        if sid not in new:
            changes.append(f"{sid}: dropped from the map")
    return changes


@celery_app.task(
    name="market.refresh_upstox_isin_map",
    bind=True,
    max_retries=3,
    default_retry_delay=600,
)
def refresh_upstox_isin_map(self) -> dict[str, Any]:
    """
    Daily re-sync of backend/data/upstox_isin_map.json against Upstox's BOD
    instrument master (refreshed by Upstox ~06:00 IST; scheduled here for
    06:30 IST to give their refresh time to land).

    HARD FAIL, ON PURPOSE, if any scheme comes back UNRESOLVED or AMBIGUOUS.
    The alternative -- write whatever resolved and leave the rest on the old
    mapping, or worse, accept a lower-confidence match to keep the count at
    35/35 -- is exactly how the original 63%-NAV-drift bug would have shipped
    silently if this job had existed and been "tolerant" instead of strict.
    A failed task leaves YESTERDAY'S KNOWN-GOOD MAP untouched and alerts
    (via Celery's own failure signal) rather than serving a wrong ISIN with
    nobody watching.

    The write itself is atomic (temp file + os.replace) so a crash mid-write
    can never leave a truncated, half-written map on disk for the next request
    to read.
    """
    import json
    import os
    import tempfile

    from backend.integrations.upstox import mf_mapping
    from backend.integrations.upstox.instruments_downloader import download_bytes
    from scripts.build_upstox_isin_map import build_from_instruments

    try:
        raw = download_bytes(settings.UPSTOX_INSTRUMENTS_MF_URL)
    except Exception as e:
        logger.error("Failed to download the Upstox MF instrument file: %s", e)
        raise self.retry(exc=e) from e

    try:
        mf_instruments = json.loads(gzip.decompress(raw))
    except (OSError, json.JSONDecodeError) as e:
        # A corrupt or non-gzip response is a data problem, not a transient
        # network one -- retrying the same bad bytes won't help, but retrying
        # the DOWNLOAD (Upstox may have been serving a bad file transiently)
        # is still worth one attempt before giving up.
        logger.error("Downloaded MF instrument file is not valid gzip+JSON: %s", e)
        raise self.retry(exc=e) from e

    results, problems = build_from_instruments(mf_instruments)

    if problems:
        logger.error(
            "Upstox ISIN map refresh FAILED: %d scheme(s) unresolved. "
            "Yesterday's map is left untouched. Problems: %s",
            len(problems),
            problems,
        )
        # Raise, don't just return an error dict -- this must surface as a
        # Celery task FAILURE so it alerts through whatever failure channel
        # (Flower, Sentry, on-call) is wired to this deployment.
        raise RuntimeError(
            f"{len(problems)} scheme(s) failed to resolve; map left unchanged: {problems}"
        )

    old = (
        json.loads(settings.ISIN_MAP_PATH.read_text())
        if settings.ISIN_MAP_PATH.exists()
        else {}
    )
    changes = _diff_isin_map(old, results)

    tmp_fd, tmp_path = tempfile.mkstemp(
        dir=settings.ISIN_MAP_PATH.parent, suffix=".tmp", prefix="isin_map_"
    )
    try:
        with os.fdopen(tmp_fd, "w", encoding="utf-8") as f:
            json.dump(results, f, indent=2, ensure_ascii=False)
            f.write("\n")
        os.replace(tmp_path, settings.ISIN_MAP_PATH)  # atomic on the same filesystem
    except BaseException:
        # Clean up the temp file on any failure between mkstemp and replace --
        # os.replace itself is atomic, but we might fail before reaching it.
        if os.path.exists(tmp_path):
            os.unlink(tmp_path)
        raise

    mf_mapping.reset_cache()  # the in-process lru_cache must not keep serving the old map

    if changes:
        logger.info("Upstox ISIN map updated with %d change(s): %s", len(changes), changes)
    else:
        logger.info("Upstox ISIN map refreshed: no changes, all 35 schemes stable.")

    return {"resolved": len(results), "changes": changes}
