"""Pydantic schemas for notification endpoints."""
from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel


class NotificationResponse(BaseModel):
    id: uuid.UUID
    investor_user_id: uuid.UUID
    rm_user_id: uuid.UUID | None
    notification_type: str
    subject: str
    body: str
    delivery_channel: str
    is_read: bool
    related_object_type: str | None
    related_object_id: str | None
    created_at: datetime

    model_config = {"from_attributes": True}


class NotificationListResponse(BaseModel):
    notifications: list[NotificationResponse]
    count: int
    unread_count: int
