"""
Portfolio service -- the transaction ledger and everything derived from it.

This is what replaces `pnl_available: false`. With a real cost basis on record,
current value, unrealised gain, realised gain, XIRR and an indicative tax
liability are all genuinely derivable -- so we derive them instead of refusing.

Where a number still cannot be known honestly (no transactions recorded yet),
the endpoint still says so. The principle didn't change; the data did.
"""
from __future__ import annotations

import logging
import uuid
from collections import defaultdict
from datetime import date
from decimal import Decimal, InvalidOperation

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from backend.analytics.portfolio_accounting import (
    InsufficientUnitsError,
    Lot,
    RealisedGain,
    SchemeHolding,
    compute_tax,
    match_fifo,
    xirr,
)
from backend.core.errors import ConflictError, NotFoundError, ValidationError
from backend.models import Transaction, User
from backend.services import engine_registry, market_service

logger = logging.getLogger(__name__)

BUY_TYPES = ("buy", "sip")


def _scheme(scheme_id: str) -> dict:
    engine = engine_registry.get_filter_engine()
    s = engine.get_scheme_by_id(scheme_id)
    if s is None:
        raise NotFoundError(f"Unknown scheme '{scheme_id}'.")
    return s


def _is_equity(scheme_id: str) -> bool:
    return _scheme(scheme_id).get("instrument_type") == "equity"


# ── Recording ─────────────────────────────────────────────────────────────────
async def _find_by_ref(
    db: AsyncSession, user: User, external_ref: str
) -> Transaction | None:
    result = await db.execute(
        select(Transaction).where(
            Transaction.user_id == user.id,
            Transaction.external_ref == external_ref,
        )
    )
    return result.scalar_one_or_none()


async def record_transaction(
    db: AsyncSession,
    user: User,
    *,
    scheme_id: str,
    txn_type: str,
    units: Decimal,
    nav: Decimal,
    txn_date: date,
    amount: Decimal | None = None,
    external_ref: str | None = None,
) -> Transaction:
    scheme = _scheme(scheme_id)

    if units <= 0 or nav <= 0:
        raise ValidationError("units and nav must both be positive.")
    if txn_date > date.today():
        raise ValidationError("A transaction cannot be dated in the future.")

    # ── Idempotency ───────────────────────────────────────────────────────────
    # Checked BEFORE the insert, not by catching the UNIQUE violation afterwards.
    # Once an IntegrityError escapes an async commit, SQLAlchemy's greenlet
    # context is gone and any further query on that session raises
    # MissingGreenlet -- so "insert, catch, then look up the original" simply
    # cannot work on an async session. Pre-checking also keeps the exception out
    # of the hot path.
    if external_ref:
        existing = await _find_by_ref(db, user, external_ref)
        if existing is not None:
            logger.info(
                "Idempotent replay of transaction external_ref=%s for user %s",
                external_ref,
                user.id,
            )
            return existing

    # The AMC's contract note is the source of truth for the rupee figure --
    # units * nav can differ by paise because of rounding at their end. We only
    # DERIVE the amount when the caller didn't give us one, and we cross-check a
    # supplied one so a fat-fingered amount can't silently corrupt the basis.
    derived = (units * nav).quantize(Decimal("0.01"))
    if amount is None:
        amount = derived
    elif abs(amount - derived) > max(derived * Decimal("0.01"), Decimal("1")):
        raise ValidationError(
            f"amount ({amount}) is inconsistent with units x nav ({derived}). "
            f"Check the contract note."
        )

    if txn_type == "sell":
        # Validate against the CURRENT open position before writing anything --
        # a sell for more units than held would corrupt every subsequent FIFO match.
        held = await _open_units(db, user, scheme_id)
        if units > held:
            raise ValidationError(
                f"Cannot sell {units} units of {scheme['name']}: only {held} held."
            )

    txn = Transaction(
        user_id=user.id,
        scheme_id=scheme_id,
        txn_type=txn_type,
        units=units,
        nav=nav,
        amount=amount,
        txn_date=txn_date,
        external_ref=external_ref,
    )
    db.add(txn)
    try:
        await db.commit()
    except IntegrityError as e:
        await db.rollback()
        # We already pre-checked, so reaching here means a genuinely CONCURRENT
        # double-submit of the same external_ref lost the race at the DB
        # constraint. The important guarantee holds -- no duplicate row was
        # written, so the cost basis is intact. The loser gets a 409 rather than
        # the original row; retrying the GET will show it.
        raise ConflictError(
            "A transaction with this external_ref already exists (concurrent submit)."
        ) from e

    await db.refresh(txn)
    return txn


async def _open_units(db: AsyncSession, user: User, scheme_id: str) -> Decimal:
    txns = await list_transactions(db, user, scheme_id=scheme_id)
    total = Decimal(0)
    for t in txns:
        total += t.units if t.txn_type in BUY_TYPES else -t.units
    return total


async def list_transactions(
    db: AsyncSession, user: User, *, scheme_id: str | None = None
) -> list[Transaction]:
    stmt = select(Transaction).where(Transaction.user_id == user.id)
    if scheme_id:
        stmt = stmt.where(Transaction.scheme_id == scheme_id)
    # Oldest first: FIFO lot matching depends on this ordering.
    stmt = stmt.order_by(Transaction.txn_date, Transaction.created_at)
    result = await db.execute(stmt)
    return list(result.scalars())


async def delete_transaction(db: AsyncSession, user: User, txn_id: uuid.UUID) -> None:
    txn = await db.get(Transaction, txn_id)
    if txn is None:
        raise NotFoundError("Transaction not found.")
    if txn.user_id != user.id:
        # IDOR guard: a transaction is financial PII.
        raise NotFoundError("Transaction not found.")
    await db.delete(txn)
    await db.commit()


# ── Derivation ────────────────────────────────────────────────────────────────
async def build_positions(
    db: AsyncSession, user: User
) -> tuple[list[SchemeHolding], list[RealisedGain]]:
    """
    Replay the whole ledger into open positions + realised gains.

    Deliberately recomputed from the ledger on every read rather than kept as a
    running balance. A denormalised units/cost column drifts the moment anyone
    back-dates or corrects a transaction -- and an investor correcting a
    mis-keyed purchase is routine. The ledger is the only source of truth.
    """
    txns = await list_transactions(db, user)
    if not txns:
        return [], []

    by_scheme: dict[str, list[Transaction]] = defaultdict(list)
    for t in txns:
        by_scheme[t.scheme_id].append(t)

    holdings: list[SchemeHolding] = []
    all_gains: list[RealisedGain] = []

    for scheme_id, scheme_txns in by_scheme.items():
        scheme = _scheme(scheme_id)
        is_eq = scheme.get("instrument_type") == "equity"

        open_lots: list[Lot] = []
        for t in scheme_txns:
            if t.txn_type in BUY_TYPES:
                open_lots.append(
                    Lot(
                        txn_id=str(t.id),
                        scheme_id=scheme_id,
                        units=Decimal(t.units),
                        nav=Decimal(t.nav),
                        txn_date=t.txn_date,
                    )
                )
            else:
                try:
                    gains, remaining = match_fifo(
                        open_lots,
                        sell_units=Decimal(t.units),
                        sell_nav=Decimal(t.nav),
                        sell_date=t.txn_date,
                        is_equity=is_eq,
                    )
                except InsufficientUnitsError as e:
                    # Should be impossible (record_transaction validates), but a
                    # back-dated correction could create it. Surface it rather
                    # than silently producing negative holdings.
                    raise ValidationError(
                        f"Ledger for {scheme['name']} is inconsistent: {e}"
                    ) from e
                all_gains.extend(gains)
                open_lots = list(remaining)

        units = sum((lot.units for lot in open_lots), Decimal(0))
        if units <= 0:
            continue  # fully exited; its gains are already in all_gains

        cost_basis = sum((lot.cost for lot in open_lots), Decimal(0))

        nav_info = await market_service.get_nav(scheme_id)
        current_nav = Decimal(str(nav_info["nav"]))
        current_value = (units * current_nav).quantize(Decimal("0.01"))
        unrealised = current_value - cost_basis

        holdings.append(
            SchemeHolding(
                scheme_id=scheme_id,
                scheme_name=scheme["name"],
                instrument_type=scheme.get("instrument_type", "unknown"),
                units=units,
                cost_basis=cost_basis,
                avg_cost_nav=(cost_basis / units).quantize(Decimal("0.0001"))
                if units
                else Decimal(0),
                current_nav=current_nav,
                current_value=current_value,
                unrealised_gain=unrealised,
                unrealised_gain_pct=(
                    (unrealised / cost_basis * 100).quantize(Decimal("0.01"))
                    if cost_basis
                    else Decimal(0)
                ),
                nav_source=nav_info["data_source"],
                lots=open_lots,
            )
        )

    return holdings, all_gains


def _f(d: Decimal | None) -> float | None:
    """Decimal -> float, at the JSON boundary only."""
    if d is None:
        return None
    try:
        return float(d)
    except (InvalidOperation, TypeError):
        return None


async def portfolio_pnl(
    db: AsyncSession, user: User, *, slab_rate: float = 0.30
) -> dict:
    """
    The real thing: mark-to-market P&L, XIRR, and an indicative tax liability.
    """
    txns = await list_transactions(db, user)

    if not txns:
        # Still honest: without a ledger there IS no cost basis. But now this is
        # an empty state with a clear next action, not a permanent limitation.
        return {
            "pnl_available": False,
            "reason": (
                "No transactions recorded. Add your purchases via "
                "POST /api/v1/portfolio/transactions (units, NAV, date) and a full "
                "mark-to-market P&L, XIRR and tax estimate become available."
            ),
            "holdings": [],
        }

    holdings, realised = await build_positions(db, user)

    total_cost = sum((h.cost_basis for h in holdings), Decimal(0))
    total_value = sum((h.current_value for h in holdings), Decimal(0))
    unrealised = total_value - total_cost
    realised_total = sum((g.gain for g in realised), Decimal(0))

    # XIRR: buys are outflows, sells inflows, and today's holding value is a
    # terminal inflow (as if liquidated now).
    flows: list[tuple[date, float]] = []
    for t in txns:
        amt = float(t.amount)
        flows.append((t.txn_date, -amt if t.txn_type in BUY_TYPES else amt))
    if total_value > 0:
        flows.append((date.today(), float(total_value)))

    money_weighted = xirr(flows)

    invested = sum(
        (Decimal(t.amount) for t in txns if t.txn_type in BUY_TYPES), Decimal(0)
    )
    withdrawn = sum(
        (Decimal(t.amount) for t in txns if t.txn_type == "sell"), Decimal(0)
    )

    tax = compute_tax(realised, slab_rate=Decimal(str(slab_rate)))

    return {
        "pnl_available": True,
        "as_of": date.today().isoformat(),
        "summary": {
            "total_invested": _f(invested),
            "total_withdrawn": _f(withdrawn),
            "current_cost_basis": _f(total_cost),
            "current_value": _f(total_value),
            "unrealised_gain": _f(unrealised),
            "unrealised_gain_pct": _f(
                (unrealised / total_cost * 100).quantize(Decimal("0.01"))
                if total_cost
                else Decimal(0)
            ),
            "realised_gain": _f(realised_total),
            "total_gain": _f(unrealised + realised_total),
            # The number the investor actually experienced. A plain
            # value/cost - 1 flatters a SIP, because it pretends last month's
            # contribution has had a full year to compound.
            "xirr": round(money_weighted, 6) if money_weighted is not None else None,
            "xirr_note": (
                "Money-weighted annualised return (XIRR) over your actual cashflow "
                "dates, treating today's holding value as a terminal inflow."
                if money_weighted is not None
                else "XIRR is undefined until there is at least one outflow and one inflow."
            ),
        },
        "holdings": [
            {
                "scheme_id": h.scheme_id,
                "scheme_name": h.scheme_name,
                "instrument_type": h.instrument_type,
                "units": _f(h.units),
                "avg_cost_nav": _f(h.avg_cost_nav),
                "current_nav": _f(h.current_nav),
                "cost_basis": _f(h.cost_basis),
                "current_value": _f(h.current_value),
                "unrealised_gain": _f(h.unrealised_gain),
                "unrealised_gain_pct": _f(h.unrealised_gain_pct),
                "nav_source": h.nav_source,
                "open_lots": len(h.lots),
            }
            for h in sorted(holdings, key=lambda x: x.current_value, reverse=True)
        ],
        "realised_gains": [
            {
                "scheme_id": g.scheme_id,
                "units": _f(g.units),
                "buy_date": g.buy_date.isoformat(),
                "sell_date": g.sell_date.isoformat(),
                "cost": _f(g.cost),
                "proceeds": _f(g.proceeds),
                "gain": _f(g.gain),
                "holding_days": g.holding_days,
                "gain_type": g.gain_type,
            }
            for g in realised
        ],
        "tax": {
            "slab_rate_assumed": slab_rate,
            "equity_stcg": _f(tax.equity_stcg),
            "equity_ltcg": _f(tax.equity_ltcg),
            "equity_ltcg_exemption_used": _f(tax.equity_ltcg_exemption_used),
            "equity_ltcg_taxable": _f(tax.equity_ltcg_taxable),
            "debt_gains_at_slab": _f(tax.debt_stcg),
            "debt_ltcg": _f(tax.debt_ltcg),
            "tax_equity_stcg": _f(tax.tax_equity_stcg),
            "tax_equity_ltcg": _f(tax.tax_equity_ltcg),
            "tax_debt_slab": _f(tax.tax_debt_slab),
            "tax_debt_ltcg": _f(tax.tax_debt_ltcg),
            "total_tax": _f(tax.total_tax),
            "basis": (
                "Finance Act 2024 (w.e.f. 23 Jul 2024). Equity: STCG 20% (<=12m), "
                "LTCG 12.5% (>12m) above a Rs 1,25,000 annual aggregate exemption. "
                "Debt units acquired on/after 1 Apr 2023 are 'specified mutual funds' "
                "(s.50AA) -- ALL gains are slab-rate regardless of holding period. "
                "FIFO lot matching. Indicative only, not tax advice; set-off and "
                "carry-forward of losses (s.70/74) are not modelled."
            ),
        },
    }
