"""
Phase 2: filter -> select -> allocate.

The FilterEngine is a pure function of the profile; this module is the thin
layer that feeds it profile fields and persists what the investor picks.
"""
from __future__ import annotations

import logging
from dataclasses import asdict
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.core.errors import NotFoundError, ValidationError
from backend.models import InvestorProfile, SchemeSelection, User
from backend.services import engine_registry

logger = logging.getLogger(__name__)


def _apply_real_tradability(
    eligible: list[dict[str, Any]], filtered_out: list[dict[str, Any]]
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], int]:
    """
    Re-check FilterEngine's eligibility against Upstox's REAL, live tradability
    facts, without touching the engine.

    FilterEngine's min_investment gate uses a hardcoded ₹500 (equity) /
    ₹5,000 (debt) assumption baked into the factsheet snapshot. The real
    numbers, from the Upstox MF instrument master, differ per scheme -- e.g.
    the overnight fund's real minimum is ₹100, not ₹5,000 -- and some schemes
    are CLOSED TO NEW PURCHASE entirely (four of our 35, as of this file's
    vintage: Small Cap and three international feeder funds). A closed scheme
    sailing through as "eligible" would let an investor select, allocate, and
    run a full ARISE projection on a fund they cannot actually buy.

    Additive and defensive: if the map is missing or Upstox is disabled, this
    is a no-op and FilterEngine's original result stands unchanged -- the
    platform must keep working with zero Upstox configuration.
    """
    from backend.integrations.upstox.mf_mapping import get_mapping

    still_eligible: list[dict[str, Any]] = []
    moved = 0

    for scheme in eligible:
        mapping = get_mapping(scheme["id"])
        if mapping is None or not mapping.is_resolved:
            still_eligible.append(scheme)  # no Upstox data -> defer to the engine's own call
            continue

        if mapping.purchase_allowed is False:
            filtered_out.append(
                {
                    "scheme_id": scheme["id"],
                    "scheme_name": scheme["name"],
                    "reasons": [
                        "Closed to new purchase on Upstox as of the current "
                        "instrument master (purchase_allowed=false). This is a "
                        "live tradability fact, not a suitability judgement -- "
                        "the scheme may reopen."
                    ],
                }
            )
            moved += 1
            continue

        # Real minimum, if we have one, is a stricter (or looser) fact than
        # the factsheet-snapshot assumption FilterEngine already applied.
        scheme["upstox_min_investment"] = mapping.minimum_purchase_amount
        scheme["upstox_purchasable"] = True
        still_eligible.append(scheme)

    return still_eligible, filtered_out, moved


def filter_schemes(profile: InvestorProfile) -> dict[str, Any]:
    engine = engine_registry.get_filter_engine()

    result = engine.filter_eligible_schemes(
        investor_age=profile.age,
        monthly_income=profile.monthly_income,
        investment_amount=profile.investment_amount or 0,
        goal=profile.goal or "growth",
        risk_appetite=profile.risk_appetite or "moderate",
        existing_holding_ids=[],
    )

    eligible = [asdict(s) for s in result.eligible_schemes]
    filtered_out = [asdict(f) for f in result.filtered_out]
    eligible, filtered_out, closed_to_purchase = _apply_real_tradability(eligible, filtered_out)

    summary = dict(result.filter_summary)
    # A DISTINCT key, not folded into by_investment_amount: these schemes are
    # closed to purchase entirely (a live Upstox tradability fact), which is a
    # different reason than "your investment amount is below the minimum" and
    # must not be conflated with it in the summary.
    summary["closed_to_purchase"] = closed_to_purchase

    return {
        "eligible_count": len(eligible),
        "total_schemes": result.total_schemes,
        "eligible_schemes": eligible,
        "filtered_out": filtered_out,
        "filter_summary": summary,
    }


def _scheme_infos(scheme_ids: list[str]) -> list[dict[str, Any]]:
    """
    Hydrate scheme IDs into SchemeInfoResponse-shaped dicts.

    These come straight from metadata, so `risk_flag` is None and
    `risk_flag_level` is "match": the informed-consent flags are a property of
    a scheme *relative to a given investor*, produced by the filter pass, not
    an intrinsic attribute of the scheme. The select/selected responses show
    the scheme's own facts; /schemes/filtered is where the personalised
    disclosure lives.
    """
    from backend.engines.filter_engine import equity_subtier

    engine = engine_registry.get_filter_engine()
    out: list[dict[str, Any]] = []
    for sid in scheme_ids:
        s = engine.get_scheme_by_id(sid)
        if s is None:
            continue
        instrument_type = s.get("instrument_type", "unknown")
        out.append(
            {
                "id": s["id"],
                "name": s["name"],
                "category": s.get("category"),
                "instrument_type": instrument_type,
                "risk_rating": s.get("risk_rating"),
                "risk_score_1_to_6": s.get("risk_score_1_to_6"),
                "riskometer": s.get("riskometer"),
                "aum_cr": s.get("aum_cr"),
                "nav_regular_growth": s.get("nav_regular_growth"),
                "expense_ratio_regular": s.get("expense_ratio_regular"),
                "min_investment": s.get("min_investment"),
                "suitable_for": list(s.get("suitable_for") or []),
                "benchmark": s.get("benchmark"),
                "holdings_count": s.get("holdings_count", 0),
                "subtier": (
                    equity_subtier(s.get("std_deviation"))
                    if instrument_type == "equity"
                    else None
                ),
                "risk_flag": None,
                "risk_flag_level": "match",
            }
        )
    return out


def _validate_scheme_ids(scheme_ids: list[str]) -> None:
    engine = engine_registry.get_filter_engine()
    unknown = [sid for sid in scheme_ids if engine.get_scheme_by_id(sid) is None]
    if unknown:
        raise ValidationError(
            f"Unknown scheme ID(s): {', '.join(unknown)}. "
            f"Only the {engine.total_schemes} Nippon India schemes in this "
            f"platform can be selected.",
            details={"unknown_scheme_ids": unknown},
        )


async def get_selection(db: AsyncSession, user: User) -> SchemeSelection | None:
    result = await db.execute(
        select(SchemeSelection).where(SchemeSelection.user_id == user.id)
    )
    return result.scalar_one_or_none()


async def select_schemes(
    db: AsyncSession,
    user: User,
    *,
    scheme_ids: list[str],
    weights: dict[str, float] | None = None,
) -> SchemeSelection:
    _validate_scheme_ids(scheme_ids)

    if weights is not None:
        _validate_weights(scheme_ids, weights)
        final_weights = dict(weights)
    else:
        # Equal-weight default. Stored as FRACTIONS summing to 1.0 -- the
        # contract PortfolioStatisticsBuilder.build() enforces downstream.
        equal = 1.0 / len(scheme_ids)
        final_weights = dict.fromkeys(scheme_ids, equal)

    selection = await get_selection(db, user)
    if selection is None:
        selection = SchemeSelection(
            user_id=user.id,
            selected_scheme_ids=scheme_ids,
            weights=final_weights,
        )
        db.add(selection)
    else:
        selection.selected_scheme_ids = scheme_ids
        selection.weights = final_weights

    await db.commit()
    await db.refresh(selection)
    return selection


def _validate_weights(scheme_ids: list[str], weights: dict[str, float]) -> None:
    """
    Weights must cover exactly the selected schemes and sum to 1.0.

    AllocateWeightsRequest already checks the sum, but not the key set -- and a
    weights dict naming a scheme that isn't selected (or omitting one that is)
    would sail through Pydantic and then blow up much later inside
    PortfolioStatisticsBuilder.build() with 'weights must cover exactly the
    given scheme_ids'. Catch it here, where we can name the offending IDs.
    """
    selected = set(scheme_ids)
    given = set(weights)

    missing = selected - given
    extra = given - selected
    if missing or extra:
        raise ValidationError(
            "Weights must cover exactly your selected schemes.",
            details={
                "missing_weights_for": sorted(missing),
                "weights_for_unselected_schemes": sorted(extra),
            },
        )

    total = sum(weights.values())
    if not (0.995 <= total <= 1.005):
        raise ValidationError(
            f"Portfolio weights must sum to 1.0 (100%), got {total:.4f}."
        )
    if any(w < 0 for w in weights.values()):
        raise ValidationError("Portfolio weights must not be negative.")


async def allocate_weights(
    db: AsyncSession, user: User, *, weights: dict[str, float]
) -> SchemeSelection:
    selection = await get_selection(db, user)
    if selection is None or not selection.selected_scheme_ids:
        raise NotFoundError(
            "Select schemes via POST /api/v1/schemes/select before allocating weights."
        )

    _validate_weights(list(selection.selected_scheme_ids), weights)

    selection.weights = dict(weights)
    await db.commit()
    await db.refresh(selection)
    return selection


def hydrate(selection: SchemeSelection) -> dict[str, Any]:
    return {
        "id": selection.id,
        "user_id": selection.user_id,
        "selected_scheme_ids": list(selection.selected_scheme_ids),
        "weights": dict(selection.weights),
        "schemes": _scheme_infos(list(selection.selected_scheme_ids)),
    }


def get_scheme_detail(scheme_id: str) -> dict[str, Any] | None:
    """
    Full single-scheme detail for the per-scheme dashboard.

    Reads the SAME already-loaded FilterEngine metadata and OverlapEngine
    holdings every other endpoint uses -- no new data source, just a route
    that was missing to expose fields (fund_manager, sector_allocation,
    sip_returns, ...) that existed in the data all along but had nowhere to
    surface. Unauthenticated-safe: nothing here is investor-specific.
    """
    engine = engine_registry.get_filter_engine()
    s = engine.get_scheme_by_id(scheme_id)
    if s is None:
        return None

    overlap = engine_registry.get_overlap_engine()
    holdings = overlap._holdings.get(scheme_id, [])  # noqa: SLF001 - read-only, JSON-sourced
    top_holdings = sorted(holdings, key=lambda h: h.get("weight", 0), reverse=True)[:15]

    from backend.engines.filter_engine import equity_subtier

    instrument_type = s.get("instrument_type", "unknown")

    upstox_purchasable = None
    upstox_min_investment = None
    try:
        from backend.integrations.upstox.mf_mapping import get_mapping

        mapping = get_mapping(scheme_id)
        if mapping and mapping.is_resolved:
            upstox_purchasable = mapping.purchase_allowed
            upstox_min_investment = mapping.minimum_purchase_amount
    except Exception:
        pass  # Upstox data is enrichment, never a hard dependency of this endpoint

    return {
        **{k: s.get(k) for k in (
            "id", "name", "category", "risk_rating", "risk_score_1_to_6",
            "riskometer", "benchmark_riskometer", "aum_cr", "aum_monthly_avg_cr",
            "nav_regular_growth", "nav_direct_growth", "nav_as_of",
            "expense_ratio_regular", "expense_ratio_direct", "min_investment",
            "max_investment", "min_age", "max_age", "min_monthly_income",
            "benchmark", "holdings_count", "fund_manager",
            "investment_philosophy", "inception_date", "data_vintage",
            "type_description", "product_label", "entry_load", "exit_load",
            "load_structure", "portfolio_turnover", "std_deviation", "beta",
            "sharpe_ratio", "ytm", "modified_duration", "macaulay_duration",
            "avg_maturity",
        )},
        "instrument_type": instrument_type,
        "suitable_for": list(s.get("suitable_for") or []),
        "subtier": equity_subtier(s.get("std_deviation")) if instrument_type == "equity" else None,
        "sector_allocation": list(s.get("sector_allocation") or []),
        "sip_returns": dict(s.get("sip_returns") or {}),
        "trailing_returns": dict(s.get("trailing_returns") or {}),
        "top_holdings": top_holdings,
        "upstox_purchasable": upstox_purchasable,
        "upstox_min_investment": upstox_min_investment,
    }
