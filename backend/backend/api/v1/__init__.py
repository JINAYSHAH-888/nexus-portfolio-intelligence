"""Aggregates every v1 router. Order matches the product flow in CLAUDE.md §1."""
from __future__ import annotations

from fastapi import APIRouter

from backend.api.v1 import (
    analysis,
    auth,
    market,
    meta,
    notifications,
    portfolio,
    profile,
    reports,
    schemes,
    upstox_auth,
    websocket,
)

api_router = APIRouter()

api_router.include_router(meta.router)
api_router.include_router(auth.router)
api_router.include_router(upstox_auth.router)
api_router.include_router(profile.router)
api_router.include_router(schemes.router)
api_router.include_router(analysis.router)
api_router.include_router(portfolio.router)
api_router.include_router(market.router)
api_router.include_router(reports.router)
api_router.include_router(notifications.router)
api_router.include_router(websocket.router)

__all__ = ["api_router"]
