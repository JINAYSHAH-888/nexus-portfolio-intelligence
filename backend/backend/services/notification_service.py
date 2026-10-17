"""RM notifications."""
from __future__ import annotations

import logging
import uuid

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.core.errors import NotFoundError, PermissionError_
from backend.models import Notification, User

logger = logging.getLogger(__name__)


async def create(
    db: AsyncSession,
    *,
    investor_user_id: uuid.UUID,
    notification_type: str,
    subject: str,
    body: str,
    rm_user_id: uuid.UUID | None = None,
    delivery_channel: str = "in_app",
    related_object_type: str | None = None,
    related_object_id: str | None = None,
    commit: bool = True,
) -> Notification:
    n = Notification(
        investor_user_id=investor_user_id,
        rm_user_id=rm_user_id,
        notification_type=notification_type,
        subject=subject,
        body=body,
        delivery_channel=delivery_channel,
        related_object_type=related_object_type,
        related_object_id=related_object_id,
    )
    db.add(n)
    # `commit=False` lets a caller fold the notification into its own
    # transaction, so a notification can't survive a rolled-back action.
    if commit:
        await db.commit()
        await db.refresh(n)
    else:
        await db.flush()
    return n


async def list_for_user(
    db: AsyncSession, user: User, *, limit: int = 50, offset: int = 0
) -> tuple[list[Notification], int, int]:
    """Returns (page, total, unread_total)."""
    base = select(Notification).where(Notification.investor_user_id == user.id)

    total = await db.scalar(
        select(func.count()).select_from(base.subquery())
    )
    unread = await db.scalar(
        select(func.count())
        .select_from(Notification)
        .where(
            Notification.investor_user_id == user.id,
            Notification.is_read.is_(False),
        )
    )

    result = await db.execute(
        base.order_by(Notification.created_at.desc()).limit(limit).offset(offset)
    )
    return list(result.scalars()), int(total or 0), int(unread or 0)


async def mark_read(
    db: AsyncSession, user: User, notification_id: uuid.UUID
) -> Notification:
    n = await db.get(Notification, notification_id)
    if n is None:
        raise NotFoundError("Notification not found.")
    # Without this check, any authenticated user could flip the read flag on
    # anyone else's notification just by guessing a UUID (IDOR).
    if n.investor_user_id != user.id:
        raise PermissionError_("This notification does not belong to you.")

    if not n.is_read:
        n.is_read = True
        await db.commit()
        await db.refresh(n)
    return n
