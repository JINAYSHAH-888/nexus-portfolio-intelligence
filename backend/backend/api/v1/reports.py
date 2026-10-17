"""Phase 4: PDF report generation and download."""
from __future__ import annotations

import uuid
from pathlib import Path

from fastapi import APIRouter, status
from fastapi.responses import FileResponse

from backend.core.deps import CurrentProfile, CurrentSelection, CurrentUser, DbSession
from backend.core.errors import NotFoundError, ValidationError
from backend.schemas.reports import ReportListResponse, ReportResponse
from backend.services import report_service

router = APIRouter(prefix="/reports", tags=["reports"])


@router.post("/generate", response_model=ReportResponse, status_code=status.HTTP_201_CREATED)
async def generate(
    user: CurrentUser,
    profile: CurrentProfile,
    selection: CurrentSelection,
    db: DbSession,
) -> ReportResponse:
    """
    Build a PDF from the investor's live overlap/PLOS/risk analysis.

    Generated through the same service functions the API serves, so the report
    can never disagree with the dashboard the investor just looked at.
    """
    report = await report_service.create_report(db, user, profile, selection)
    return ReportResponse.model_validate(report)


@router.get("", response_model=ReportListResponse)
async def list_reports(user: CurrentUser, db: DbSession) -> ReportListResponse:
    reports = await report_service.list_reports(db, user)
    return ReportListResponse(
        reports=[ReportResponse.model_validate(r) for r in reports],
        count=len(reports),
    )


@router.get("/{report_id}", response_model=ReportResponse)
async def get_report(
    report_id: uuid.UUID, user: CurrentUser, db: DbSession
) -> ReportResponse:
    report = await report_service.get_report(db, user, report_id)
    return ReportResponse.model_validate(report)


@router.get("/{report_id}/download")
async def download(report_id: uuid.UUID, user: CurrentUser, db: DbSession) -> FileResponse:
    # Ownership is enforced inside get_report(). Reports carry the investor's
    # income, corpus and holdings -- serving them off a guessable UUID without
    # an owner check would be a direct PII leak.
    report = await report_service.get_report(db, user, report_id)

    if report.status != "completed" or not report.file_path:
        raise ValidationError(
            f"Report is '{report.status}', not ready for download."
            + (f" Error: {report.error_message}" if report.error_message else "")
        )

    path = Path(report.file_path)
    if not path.exists():
        raise NotFoundError("Report file is missing from storage; regenerate it.")

    return FileResponse(
        path,
        media_type="application/pdf",
        filename=f"nippon-portfolio-report-{report_id}.pdf",
    )
