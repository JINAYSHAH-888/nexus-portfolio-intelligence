"""
Domain exceptions + their HTTP mapping.

Services raise these; they know nothing about HTTP. A single exception handler
in main.py turns them into RFC-7807-ish JSON. This keeps `raise
HTTPException(...)` out of the service layer, so services stay callable from
Celery tasks and tests without dragging FastAPI in.
"""
from __future__ import annotations

from typing import Any


class DomainError(Exception):
    """Base class. `status_code` and `code` drive the HTTP response."""

    status_code: int = 400
    code: str = "domain_error"

    def __init__(self, message: str, *, details: Any | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.details = details

    def to_dict(self) -> dict[str, Any]:
        body: dict[str, Any] = {"error": self.code, "message": self.message}
        if self.details is not None:
            body["details"] = self.details
        return body


class NotFoundError(DomainError):
    status_code = 404
    code = "not_found"


class ConflictError(DomainError):
    status_code = 409
    code = "conflict"


class ValidationError(DomainError):
    status_code = 422
    code = "validation_error"


class AuthenticationError(DomainError):
    status_code = 401
    code = "authentication_error"


class PermissionError_(DomainError):
    status_code = 403
    code = "permission_denied"


class RateLimitError(DomainError):
    status_code = 429
    code = "rate_limited"


class UpstreamError(DomainError):
    """A third-party data provider (yfinance/AMFI/Alpha Vantage) failed."""

    status_code = 503
    code = "upstream_unavailable"


# ── Specific, reused domain errors ────────────────────────────────────────────
class ProfileIncompleteError(DomainError):
    """
    Phase-2/3 endpoints are gated on a completed Deep Profile.

    409 rather than 403: the request is well-formed and the caller is
    authorised, but the resource is in the wrong *state* for it. The client
    should send the user back to the profile step, not to a login screen.
    """

    status_code = 409
    code = "profile_incomplete"

    def __init__(
        self,
        message: str = (
            "Your Deep Profile is incomplete. Complete it via POST /api/v1/profile "
            "before running analysis."
        ),
        *,
        missing_fields: list[str] | None = None,
    ) -> None:
        super().__init__(message, details={"missing_fields": missing_fields or []})


class SelectionNotFoundError(NotFoundError):
    """
    Analysis endpoints need at least one selected scheme.

    The message is pinned by CLAUDE.md §6: 'Returns 404 "Scheme selection not
    found" if selection is missing.' Tests assert on it -- don't reword it.
    """

    def __init__(self, message: str = "Scheme selection not found") -> None:
        super().__init__(message)


class InsufficientDataError(DomainError):
    """
    An engine could not build usable statistics from the selected schemes.

    e.g. PortfolioStatisticsBuilder.build() raises ValueError("No usable return
    data...") when every fallback tier fails. That is a real, explainable state,
    not a server bug -- so it surfaces as 422 with the engine's own reason
    rather than as an opaque 500.
    """

    status_code = 422
    code = "insufficient_data"
