"""Market data routes + Celery task triggers."""
from __future__ import annotations

from fastapi import APIRouter, status

from backend.core.deps import CurrentProfile, CurrentSelection, CurrentUser, DbSession
from backend.services import market_service

router = APIRouter(prefix="/market", tags=["market"])


@router.get("/portfolio-value")
async def portfolio_value(
    profile: CurrentProfile, selection: CurrentSelection
) -> dict:
    return await market_service.portfolio_value(profile, selection)


@router.get("/dashboard")
async def dashboard(profile: CurrentProfile, selection: CurrentSelection) -> dict:
    return await market_service.dashboard(profile, selection)


@router.get("/pnl", deprecated=True)
async def pnl(user: CurrentUser, db: DbSession) -> dict:
    """
    DEPRECATED -- use `GET /api/v1/portfolio/pnl`.

    Kept as an alias so existing clients don't break. It now delegates to the
    real transaction-ledger P&L (mark-to-market, FIFO-matched realised gains,
    XIRR, and an indicative capital-gains tax) instead of the old honest refusal.
    """
    from backend.services import portfolio_service

    return await portfolio_service.portfolio_pnl(db, user)


@router.get("/fundamentals/{ticker}")
async def fundamentals(ticker: str, _user: CurrentUser) -> dict:
    return await market_service.get_fundamentals(ticker.upper())


@router.get("/nav/{scheme_id}")
async def nav(scheme_id: str, _user: CurrentUser) -> dict:
    return await market_service.get_nav(scheme_id)


# ── Celery triggers ───────────────────────────────────────────────────────────
# These enqueue and return immediately with a task id. They are fire-and-forget
# refresh jobs, not request-scoped work -- downloading 221 tickers inline would
# blow every sane HTTP timeout.
@router.post("/tasks/download-prices", status_code=status.HTTP_202_ACCEPTED)
async def trigger_download_prices(_user: CurrentUser) -> dict:
    from backend.tasks.market_tasks import download_prices

    task = download_prices.delay()
    return {"task_id": str(task.id), "status": "queued", "task": "download_prices"}


@router.post("/tasks/download-nav", status_code=status.HTTP_202_ACCEPTED)
async def trigger_download_nav(_user: CurrentUser) -> dict:
    from backend.tasks.market_tasks import download_nav

    task = download_nav.delay()
    return {"task_id": str(task.id), "status": "queued", "task": "download_nav"}


@router.post("/tasks/refresh-holdings", status_code=status.HTTP_202_ACCEPTED)
async def trigger_refresh_holdings(_user: CurrentUser) -> dict:
    from backend.tasks.market_tasks import refresh_holdings

    task = refresh_holdings.delay()
    return {"task_id": str(task.id), "status": "queued", "task": "refresh_holdings"}


@router.post("/tasks/refresh-upstox-isin-map", status_code=status.HTTP_202_ACCEPTED)
async def trigger_refresh_upstox_isin_map(_user: CurrentUser) -> dict:
    """
    Manual trigger for the normally-nightly ISIN re-sync. Useful right after
    connecting Upstox for the first time, or to force-check a scheme you
    expect to have reopened, without waiting for 06:30 IST.
    """
    from backend.tasks.market_tasks import refresh_upstox_isin_map

    task = refresh_upstox_isin_map.delay()
    return {"task_id": str(task.id), "status": "queued", "task": "refresh_upstox_isin_map"}
