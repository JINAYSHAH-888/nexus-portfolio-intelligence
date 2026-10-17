"""
Transparency endpoint.

Every number this product reports comes from one of four places: statute (law),
live market data (market), a modelling choice (model), or a stand-in for data we
do not have (estimate). This endpoint publishes all of them.

It exists because "are these numbers real or assumed?" is the first question any
reviewer, auditor or compliance officer will ask, and the answer should be one
HTTP call rather than an archaeology expedition through six modules.
"""
from __future__ import annotations

from fastapi import APIRouter

from backend.core import assumptions

router = APIRouter(prefix="/meta", tags=["meta"])


@router.get("/assumptions")
async def get_assumptions() -> dict:
    """
    The complete assumption set of the platform, grouped by provenance.

    Public and unauthenticated on purpose: an investor is entitled to know what
    the model assumes before trusting a retirement projection, and none of it is
    sensitive.
    """
    return assumptions.as_dict()
