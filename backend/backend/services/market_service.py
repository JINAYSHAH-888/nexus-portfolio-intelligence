"""
Market endpoints: portfolio value, dashboard, P&L, fundamentals, NAV.

Everything here reaches an external provider (yfinance / AMFI / Alpha Vantage),
so everything here is written to degrade rather than fail. CLAUDE.md §3 is
explicit that the sandbox has no egress to those hosts and that the fallback
tiers are the tested path -- an endpoint that 500s when Yahoo is unreachable
would be a bug, not honest error reporting. Each response therefore carries
its own provenance (`data_source`, `is_delayed`, `is_estimated`) so the client
can render the caveat instead of the API pretending.
"""
from __future__ import annotations

import logging
from typing import Any

from backend.core.errors import NotFoundError, UpstreamError
from backend.models import InvestorProfile, SchemeSelection
from backend.services import engine_registry

logger = logging.getLogger(__name__)


def _scheme_or_404(scheme_id: str) -> dict[str, Any]:
    engine = engine_registry.get_filter_engine()
    scheme = engine.get_scheme_by_id(scheme_id)
    if scheme is None:
        raise NotFoundError(f"Unknown scheme '{scheme_id}'.")
    return scheme


async def get_nav(scheme_id: str) -> dict[str, Any]:
    """
    Latest NAV for a scheme.

    Tier 1: live AMFI (mfapi.in), name-validated upstream in amfi_loader to
            catch the known duplicate-AMFI-code contamination bug.
    Tier 2: the NAV disclosed on the scheme's own factsheet, clearly labelled
            stale with its as-of date -- a real, sourced number, not a guess.
    """
    scheme = _scheme_or_404(scheme_id)
    amfi = engine_registry.get_amfi_loader()

    nav: float | None = None
    try:
        nav = await amfi.get_nippon_nav(scheme_id)
    except Exception as e:
        logger.warning("AMFI NAV lookup failed for %s: %s", scheme_id, e)

    if nav is not None:
        return {
            "scheme_id": scheme_id,
            "scheme_name": scheme["name"],
            "nav": nav,
            "as_of": None,
            "data_source": "amfi_live",
            "is_estimated": False,
        }

    fallback = scheme.get("nav_regular_growth")
    if fallback is None:
        raise UpstreamError(
            f"AMFI is unreachable and no factsheet NAV is on record for '{scheme_id}'."
        )

    return {
        "scheme_id": scheme_id,
        "scheme_name": scheme["name"],
        "nav": fallback,
        "as_of": scheme.get("nav_as_of"),
        "data_source": "factsheet_disclosed",
        "is_estimated": False,
        "warning": (
            "Live AMFI NAV was unavailable; this is the NAV disclosed on the "
            f"scheme factsheet as of {scheme.get('nav_as_of')}, not today's NAV."
        ),
    }


async def get_fundamentals(ticker: str) -> dict[str, Any]:
    """Alpha Vantage -> yfinance fallback (the loader handles the chain)."""
    from backend.data_loaders.alpha_vantage_loader import AlphaVantageLoader

    loader = AlphaVantageLoader()
    try:
        data, source = await loader.get_fundamentals_with_fallback(ticker)
    except Exception as e:
        raise UpstreamError(f"Fundamentals lookup failed for {ticker}: {e}") from e

    if data is None:
        raise NotFoundError(
            f"No fundamentals available for '{ticker}' from any configured provider."
        )

    payload = data._asdict() if hasattr(data, "_asdict") else dict(data)
    payload["ticker"] = ticker
    payload["data_source"] = source
    # Enums (ValuationBucket/QualityTier) -> plain strings for JSON.
    for k, v in list(payload.items()):
        if hasattr(v, "value"):
            payload[k] = v.value
    return payload


async def portfolio_value(
    profile: InvestorProfile, selection: SchemeSelection
) -> dict[str, Any]:
    """
    Current value of the investor's holding, per scheme.

    Honest framing: the platform records the amount the investor *intends* to
    invest and their chosen weights. It does not (yet) store units purchased or
    a purchase-date NAV, so a true mark-to-market P&L is not derivable. Rather
    than invent a cost basis, we report the invested amount allocated by weight,
    alongside each scheme's current NAV, and say plainly that P&L needs a
    transaction record.
    """
    weights = selection.weights or {}
    invested_total = float(profile.investment_amount or 0)

    holdings: list[dict[str, Any]] = []
    for sid in selection.selected_scheme_ids:
        scheme = _scheme_or_404(sid)
        w = float(weights.get(sid, 0.0))
        allocated = invested_total * w

        nav_info = await get_nav(sid)
        nav = nav_info["nav"]
        units = (allocated / nav) if nav else 0.0

        holdings.append(
            {
                "scheme_id": sid,
                "scheme_name": scheme["name"],
                "instrument_type": scheme.get("instrument_type"),
                "weight": round(w, 6),
                "allocated_amount": round(allocated, 2),
                "nav": nav,
                "nav_source": nav_info["data_source"],
                "implied_units": round(units, 4),
            }
        )

    return {
        "invested_amount": invested_total,
        "current_value": invested_total,
        "holdings": holdings,
        "pnl_available": False,
        "note": (
            "Current value equals the invested amount because this platform stores "
            "an intended allocation, not a transaction history (units purchased and "
            "purchase NAV). A true mark-to-market P&L requires those records; they "
            "are not fabricated here."
        ),
    }


async def dashboard(
    profile: InvestorProfile, selection: SchemeSelection
) -> dict[str, Any]:
    """One-shot summary for the front page."""
    from backend.data_loaders.live_price_loader import is_market_open
    from backend.services import analysis_service

    value = await portfolio_value(profile, selection)
    prices = await analysis_service.get_prices_for_selection(selection, limit=10)

    equity = sum(
        1
        for h in value["holdings"]
        if h["instrument_type"] == "equity"
    )
    debt = len(value["holdings"]) - equity

    return {
        "investor": {
            "name": profile.full_name,
            "age": profile.age,
            "years_to_retirement": profile.years_to_retirement,
            "goal": profile.goal,
            "risk_appetite": profile.risk_appetite,
        },
        "portfolio": {
            "scheme_count": len(value["holdings"]),
            "equity_schemes": equity,
            "debt_schemes": debt,
            "invested_amount": value["invested_amount"],
            "current_value": value["current_value"],
            "holdings": value["holdings"],
        },
        "market": {
            "is_open": is_market_open(),
            "data_delayed": True,
            "top_holdings": prices["prices"][:10],
        },
        "pnl_available": False,
    }


async def pnl(profile: InvestorProfile, selection: SchemeSelection) -> dict[str, Any]:
    """
    P&L.

    Deliberately returns `pnl_available: false` with an explanation instead of
    a plausible-looking number. Computing gain/loss requires a cost basis (units
    and purchase NAV), which this platform does not record. Returning zeros -- or
    worse, backing into a "return" from a factsheet CAGR -- would put a fabricated
    figure in front of an investor. CLAUDE.md's first principle is that every
    number traces to real data or a labelled estimate; there is no honest estimate
    available here.
    """
    value = await portfolio_value(profile, selection)
    return {
        "pnl_available": False,
        "reason": (
            "P&L requires a transaction history (units purchased and purchase-date "
            "NAV). This platform stores an intended allocation only. Connect a "
            "folio/transaction feed to enable mark-to-market P&L."
        ),
        "invested_amount": value["invested_amount"],
        "holdings": value["holdings"],
    }
