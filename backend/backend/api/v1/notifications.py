"""RM notification routes."""
from __future__ import annotations

import uuid

from fastapi import APIRouter, Query

from backend.core.deps import CurrentUser, DbSession
from backend.schemas.notifications import NotificationListResponse, NotificationResponse
from backend.services import notification_service

router = APIRouter(prefix="/notifications", tags=["notifications"])


@router.get("/mine", response_model=NotificationListResponse)
async def mine(
    user: CurrentUser,
    db: DbSession,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
) -> NotificationListResponse:
    items, total, unread = await notification_service.list_for_user(
        db, user, limit=limit, offset=offset
    )
    return NotificationListResponse(
        notifications=[NotificationResponse.model_validate(n) for n in items],
        count=total,
        unread_count=unread,
    )


@router.post("/{notification_id}/read", response_model=NotificationResponse)
async def mark_read(
    notification_id: uuid.UUID, user: CurrentUser, db: DbSession
) -> NotificationResponse:
    n = await notification_service.mark_read(db, user, notification_id)
    return NotificationResponse.model_validate(n)
