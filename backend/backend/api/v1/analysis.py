"""
Phase 3: overlap / PLOS / risk / ARISE / prices.

Every route here is gated on the full chain -- valid JWT (CurrentUser) +
completed Deep Profile (CurrentProfile) + a non-empty selection
(CurrentSelection) -- per CLAUDE.md §6. The dependencies enforce it; the
handlers never re-check it by hand.
"""
from __future__ import annotations

from fastapi import APIRouter

from backend.core.deps import CurrentProfile, CurrentSelection, CurrentUser, DbSession
from backend.schemas.analysis import (
    ARISERequest,
    ARISEResponse,
    CompareRequest,
    CompareResponse,
    OverlapResponse,
    PLOSResponse,
    RiskResponse,
)
from backend.services import analysis_service, scheme_service

router = APIRouter(prefix="/analysis", tags=["analysis"])


@router.get("/overlap", response_model=OverlapResponse)
async def overlap(
    _profile: CurrentProfile, selection: CurrentSelection
) -> OverlapResponse:
    """Jaccard overlap heatmap + network graph across the selected schemes."""
    return OverlapResponse(**await analysis_service.compute_overlap(selection))


@router.get("/plos", response_model=PLOSResponse)
async def plos(_profile: CurrentProfile, selection: CurrentSelection) -> PLOSResponse:
    """10-layer Portfolio Level Overlap Score, plus a top-5 diversification pick."""
    return PLOSResponse(**await analysis_service.compute_plos(selection))


@router.get("/risk", response_model=RiskResponse)
async def risk(_profile: CurrentProfile, selection: CurrentSelection) -> RiskResponse:
    """Ledoit-Wolf covariance, VaR/CVaR (95/99, parametric + historical), GBM Monte Carlo."""
    return RiskResponse(**await analysis_service.compute_risk(selection))


@router.post("/arise", response_model=ARISEResponse)
async def arise(
    payload: ARISERequest,
    profile: CurrentProfile,
    selection: CurrentSelection,
) -> ARISEResponse:
    """
    ARISE v4 retirement projection for the investor's REAL selected portfolio.

    Student-t(df=5) AR(1) mean-reverting returns anchored on the shrunk,
    evidence-capped portfolio CAGR/volatility from PortfolioStatisticsBuilder --
    not a raw historical bootstrap (CLAUDE.md §5/§10).

    `milevsky_ruin_prob` is a closed-form cross-check, not the headline: it
    assumes a fixed corpus at retirement with no accumulation-phase sequence
    risk, so it is EXPECTED to diverge from the Monte Carlo when the
    accumulation phase is long. Read `adaptive_success_rate` as the answer.
    """
    result = await analysis_service.run_arise(
        profile,
        selection,
        n_simulations=payload.n_simulations,
        life_expectancy=payload.life_expectancy,
        slab_rate=payload.slab_rate,
        strategy=payload.strategy,
        step_up=payload.step_up,
        monthly_sip=payload.monthly_sip,
        spending_model=payload.spending_model,
        glide_mode=payload.glide_mode,
        cape=payload.cape,
    )
    return ARISEResponse(**result)


@router.get("/prices")
async def prices(
    _profile: CurrentProfile, selection: CurrentSelection
) -> dict:
    """
    Delayed NSE quotes for the largest holdings across the selection.

    yfinance is delayed, never a true tick -- the response says so
    (`data_delayed: true`). An all-debt selection legitimately returns no
    quotes: bonds aren't listed equities.
    """
    return await analysis_service.get_prices_for_selection(selection)


@router.post("/compare", response_model=CompareResponse)
async def compare(
    payload: CompareRequest,
    user: CurrentUser,
    _profile: CurrentProfile,
    db: DbSession,
) -> CompareResponse:
    """
    "What happens to my portfolio if I swap this scheme?" -- computes
    overlap + PLOS for a HYPOTHETICAL scheme combination without touching the
    investor's actually-saved selection at all.

    If the investor already has a real saved selection, it's included
    automatically as `current`, so the frontend can render a direct
    before/after diff in one call. Nothing here is persisted -- to actually
    commit to the candidate combination, call POST /schemes/select separately.
    """
    current = await scheme_service.get_selection(db, user)
    result = await analysis_service.compare_selection(
        candidate_scheme_ids=payload.scheme_ids,
        candidate_weights=payload.weights,
        current_selection=current,
    )
    return CompareResponse(**result)
