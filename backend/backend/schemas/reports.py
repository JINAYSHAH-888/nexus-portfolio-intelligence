"""Pydantic schemas for Phase 4 report endpoints."""
from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel


class ReportResponse(BaseModel):
    id: uuid.UUID
    user_id: uuid.UUID
    status: str
    scheme_ids: list[str]
    file_size_bytes: int | None
    error_message: str | None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class ReportListResponse(BaseModel):
    reports: list[ReportResponse]
    count: int
