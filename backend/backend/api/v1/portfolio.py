"""
The transaction ledger and everything derived from it.

This is the module that turns `pnl_available: false` into a real, auditable P&L:
once an investor's actual purchases are on record (units, NAV, date), the cost
basis exists and mark-to-market gain, XIRR and tax all follow.
"""
from __future__ import annotations

import uuid

from fastapi import APIRouter, Query, status

from backend.core.deps import CurrentUser, DbSession
from backend.schemas.transactions import (
    TransactionCreate,
    TransactionListResponse,
    TransactionResponse,
)
from backend.services import portfolio_service

router = APIRouter(prefix="/portfolio", tags=["portfolio"])


@router.post(
    "/transactions",
    response_model=TransactionResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_transaction(
    payload: TransactionCreate, user: CurrentUser, db: DbSession
) -> TransactionResponse:
    """
    Record a buy / SIP instalment / sell.

    Supply `external_ref` (e.g. the AMC folio + transaction number) to make the
    call idempotent: a retry with the same ref returns the ORIGINAL row rather
    than booking a duplicate purchase, which would silently double the cost
    basis and corrupt every tax figure downstream.
    """
    txn = await portfolio_service.record_transaction(
        db,
        user,
        scheme_id=payload.scheme_id,
        txn_type=payload.txn_type,
        units=payload.units,
        nav=payload.nav,
        txn_date=payload.txn_date,
        amount=payload.amount,
        external_ref=payload.external_ref,
    )
    return TransactionResponse.model_validate(txn)


@router.get("/transactions", response_model=TransactionListResponse)
async def list_transactions(
    user: CurrentUser,
    db: DbSession,
    scheme_id: str | None = Query(default=None),
) -> TransactionListResponse:
    txns = await portfolio_service.list_transactions(db, user, scheme_id=scheme_id)
    return TransactionListResponse(
        transactions=[TransactionResponse.model_validate(t) for t in txns],
        count=len(txns),
    )


@router.delete("/transactions/{txn_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_transaction(
    txn_id: uuid.UUID, user: CurrentUser, db: DbSession
) -> None:
    """Remove a mis-keyed transaction. Positions are replayed from the ledger, so
    the correction propagates to P&L and tax immediately."""
    await portfolio_service.delete_transaction(db, user, txn_id)


@router.get("/pnl")
async def pnl(
    user: CurrentUser,
    db: DbSession,
    slab_rate: float = Query(default=0.30, ge=0.0, le=0.42),
) -> dict:
    """
    Real mark-to-market P&L from the recorded cost basis.

    Includes unrealised gain per scheme, FIFO-matched realised gains, XIRR
    (money-weighted -- the return the investor actually experienced, unlike a
    naive value/cost ratio which flatters a SIP), and an indicative capital-gains
    tax under the Finance Act 2024 rules.

    Returns `pnl_available: false` only when no transactions have been recorded
    yet -- an empty state with a clear next action, not a permanent limitation.
    """
    return await portfolio_service.portfolio_pnl(db, user, slab_rate=slab_rate)


@router.get("/holdings")
async def holdings(user: CurrentUser, db: DbSession) -> dict:
    """Current open positions, valued at the latest available NAV."""
    result = await portfolio_service.portfolio_pnl(db, user)
    return {
        "pnl_available": result["pnl_available"],
        "holdings": result.get("holdings", []),
        "summary": result.get("summary"),
    }
