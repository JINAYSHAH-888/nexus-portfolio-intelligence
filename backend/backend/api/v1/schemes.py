"""Phase 2: scheme filtering, selection and allocation."""
from __future__ import annotations

from fastapi import APIRouter

from backend.core.deps import CurrentProfile, CurrentUser, DbSession
from backend.core.errors import NotFoundError, SelectionNotFoundError
from backend.schemas.schemes import (
    AllocateWeightsRequest,
    FilterResultResponse,
    SchemeDetailResponse,
    SelectedSchemesResponse,
    SelectSchemesRequest,
    SelectSchemesResponse,
)
from backend.services import scheme_service

router = APIRouter(prefix="/schemes", tags=["schemes"])


@router.get("/filtered", response_model=FilterResultResponse)
async def filtered(profile: CurrentProfile) -> FilterResultResponse:
    """
    Eligible schemes for this investor, plus every hard-blocked scheme and why.

    Risk and (non-tax-saving) goal mismatches are DISCLOSED, not blocked -- each
    eligible scheme carries `risk_flag` / `risk_flag_level` / `subtier`. See
    CLAUDE.md §4: hard-gating on the riskometer left conservative and moderate
    investors with zero eligible equity schemes, because all 20 real Nippon
    equity funds genuinely carry risk_score 6.
    """
    return FilterResultResponse(**scheme_service.filter_schemes(profile))


@router.post("/select", response_model=SelectSchemesResponse)
async def select(
    payload: SelectSchemesRequest,
    user: CurrentUser,
    _profile: CurrentProfile,
    db: DbSession,
) -> SelectSchemesResponse:
    """Select schemes. Weights are optional; omitted means equal-weight."""
    selection = await scheme_service.select_schemes(
        db,
        user,
        scheme_ids=payload.selected_scheme_ids,
        weights=payload.weights,
    )
    hydrated = scheme_service.hydrate(selection)
    return SelectSchemesResponse(
        selected_count=len(hydrated["selected_scheme_ids"]),
        schemes=hydrated["schemes"],
        weights=hydrated["weights"],
        message=(
            f"{len(hydrated['selected_scheme_ids'])} scheme(s) selected. "
            + (
                "Weights applied as supplied."
                if payload.weights
                else "Equal-weighted by default -- call POST /schemes/allocate to set "
                "a real allocation before running ARISE."
            )
        ),
    )


@router.post("/allocate", response_model=SelectedSchemesResponse)
async def allocate(
    payload: AllocateWeightsRequest,
    user: CurrentUser,
    _profile: CurrentProfile,
    db: DbSession,
) -> SelectedSchemesResponse:
    """Set allocation weights. Must cover exactly the selected schemes and sum to 1.0."""
    selection = await scheme_service.allocate_weights(db, user, weights=payload.weights)
    return SelectedSchemesResponse(**scheme_service.hydrate(selection))


@router.get("/selected", response_model=SelectedSchemesResponse)
async def selected(user: CurrentUser, db: DbSession) -> SelectedSchemesResponse:
    selection = await scheme_service.get_selection(db, user)
    if selection is None or not selection.selected_scheme_ids:
        raise SelectionNotFoundError()
    return SelectedSchemesResponse(**scheme_service.hydrate(selection))


@router.get("/{scheme_id}", response_model=SchemeDetailResponse)
async def scheme_detail(scheme_id: str, _user: CurrentUser) -> SchemeDetailResponse:
    """
    Full single-scheme detail: everything /schemes/filtered's list view has,
    plus sector allocation, trailing SIP returns, fund manager, investment
    philosophy, load structure, and top holdings -- for the per-scheme
    dashboard view. Requires only a valid login, not a completed profile,
    since none of this data is investor-specific.

    Registered LAST so it can never shadow the static routes above
    (/filtered, /select, /allocate, /selected) -- FastAPI matches routes in
    registration order, and a dynamic /{scheme_id} declared earlier would
    have swallowed a request to /schemes/filtered with scheme_id="filtered".
    """
    detail = scheme_service.get_scheme_detail(scheme_id)
    if detail is None:
        raise NotFoundError(f"Unknown scheme '{scheme_id}'.")
    return SchemeDetailResponse(**detail)
