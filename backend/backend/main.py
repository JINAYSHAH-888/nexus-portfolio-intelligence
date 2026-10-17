"""
FastAPI application entrypoint.

    uvicorn backend.main:app --reload
"""
from __future__ import annotations

import logging
import time
import uuid
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.base import BaseHTTPMiddleware

from backend.api.v1 import api_router
from backend.core.async_utils import shutdown_bridge
from backend.core.config import settings
from backend.core.errors import DomainError, RateLimitError
from backend.core.json import SafeJSONResponse
from backend.core.logging_config import setup_logging
from backend.core.security import TokenError, decode_token
from backend.infrastructure import rate_limit, redis_client
from backend.infrastructure.db import dispose_engine
from backend.services import engine_registry

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    setup_logging()

    # Fail fast, loudly, at boot -- never at 3am under load. A placeholder
    # SECRET_KEY in production means every JWT is forgeable.
    problems = settings.validate_production_safety()
    if problems:
        for p in problems:
            logger.critical("UNSAFE PRODUCTION CONFIG: %s", p)
        raise RuntimeError(
            f"Refusing to start with {len(problems)} unsafe production setting(s). "
            "See the CRITICAL log lines above."
        )

    logger.info(
        "Starting %s (env=%s, redis=%s)",
        settings.APP_NAME,
        settings.ENVIRONMENT,
        settings.REDIS_ENABLED,
    )
    engine_registry.warm_up()

    yield

    logger.info("Shutting down.")
    await redis_client.close_redis()
    await dispose_engine()
    shutdown_bridge()


app = FastAPI(
    title=settings.APP_NAME,
    version="1.0.0",
    description=(
        "Institutional-grade mutual fund overlap, risk and retirement "
        "intelligence over 35 real Nippon India schemes (20 equity + 15 debt).\n\n"
        "Flow: register -> deep profile -> filter -> select -> allocate -> "
        "overlap / PLOS / risk / ARISE v4."
    ),
    lifespan=lifespan,
    default_response_class=SafeJSONResponse,  # NaN/Inf can never reach the client
    docs_url="/docs",
    redoc_url="/redoc",
    openapi_url="/openapi.json",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


class RequestContextMiddleware(BaseHTTPMiddleware):
    """Attach a request id + timing to every response and log line."""

    async def dispatch(self, request: Request, call_next):
        request_id = request.headers.get("X-Request-ID") or uuid.uuid4().hex[:12]
        request.state.request_id = request_id

        start = time.perf_counter()
        response = await call_next(request)
        elapsed_ms = (time.perf_counter() - start) * 1000

        response.headers["X-Request-ID"] = request_id
        response.headers["X-Response-Time-ms"] = f"{elapsed_ms:.1f}"

        logger.info(
            "%s %s -> %d (%.1fms)",
            request.method,
            request.url.path,
            response.status_code,
            elapsed_ms,
            extra={"request_id": request_id},
        )
        return response


class RateLimitMiddleware(BaseHTTPMiddleware):
    """
    Cluster-wide sliding-window rate limiting (see infrastructure/rate_limit.py).

    Backed by an atomic Redis Lua script so the limit holds across every API
    replica, not just this process. Falls back to a per-process limiter if Redis
    is unreachable -- a limiter that 500s when its cache dies has turned a minor
    degradation into an outage.

    Keyed on the authenticated user where one is present, IP otherwise: IP alone
    throttles a whole NATed office together while an attacker just rotates
    proxies.
    """

    EXEMPT = frozenset({"/health", "/health/ready", "/openapi.json", "/docs", "/redoc"})

    async def dispatch(self, request: Request, call_next):
        if not settings.RATE_LIMIT_ENABLED or request.url.path in self.EXEMPT:
            return await call_next(request)

        path = request.url.path
        limit, bucket = rate_limit.limit_for(path)

        ip = request.client.host if request.client else "unknown"
        user_id = _user_id_from_request(request)
        key = f"rl:{bucket}:{rate_limit.client_id(ip, user_id)}"

        result = await rate_limit.check(key, limit, window_s=60.0)

        if not result.allowed:
            logger.warning(
                "Rate limit hit: bucket=%s key=%s limit=%d backend=%s",
                bucket, key, limit, result.backend,
            )
            return SafeJSONResponse(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                content={
                    "error": "rate_limited",
                    "message": (
                        f"Rate limit exceeded for '{bucket}' ({limit}/min). "
                        f"Retry in {result.retry_after}s."
                    ),
                    "request_id": getattr(request.state, "request_id", None),
                },
                headers={
                    "Retry-After": str(result.retry_after),
                    "X-RateLimit-Limit": str(limit),
                    "X-RateLimit-Remaining": "0",
                },
            )

        response = await call_next(request)
        response.headers["X-RateLimit-Limit"] = str(limit)
        response.headers["X-RateLimit-Remaining"] = str(result.remaining)
        return response


def _user_id_from_request(request: Request) -> str | None:
    """
    Best-effort user id straight off the bearer token.

    Deliberately does NOT hit the database or fully authenticate -- the limiter
    runs before auth and must stay cheap. A forged token yields a bogus subject,
    but that only ever moves the attacker into a DIFFERENT bucket; it cannot
    raise their limit, and the request still has to pass real auth downstream.
    """
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        return None
    try:
        payload = decode_token(auth[7:], expected_type="access")
        return str(payload.get("sub")) or None
    except TokenError:
        return None


app.add_middleware(RateLimitMiddleware)
app.add_middleware(RequestContextMiddleware)


# ── Error handlers ────────────────────────────────────────────────────────────
@app.exception_handler(DomainError)
async def domain_error_handler(request: Request, exc: DomainError) -> SafeJSONResponse:
    """
    One handler for every domain exception. This is what keeps
    `raise HTTPException(...)` out of the service layer -- services raise
    meaningful domain errors and stay callable from Celery and tests without
    dragging FastAPI in.
    """
    body = exc.to_dict()
    body["request_id"] = getattr(request.state, "request_id", None)

    if isinstance(exc, RateLimitError):
        return SafeJSONResponse(status_code=exc.status_code, content=body)

    if exc.status_code >= 500:
        logger.error("%s: %s", exc.code, exc.message)
    else:
        logger.info("%s: %s", exc.code, exc.message)

    headers = {}
    if exc.status_code == status.HTTP_401_UNAUTHORIZED:
        headers["WWW-Authenticate"] = "Bearer"

    return SafeJSONResponse(status_code=exc.status_code, content=body, headers=headers)


@app.exception_handler(RequestValidationError)
async def validation_handler(
    request: Request, exc: RequestValidationError
) -> SafeJSONResponse:
    """
    Flatten Pydantic's error list into something a UI can render next to a field.

    The engines' Pydantic validators carry genuinely useful messages ("You cannot
    invest more than you have", "Portfolio weights must sum to 1.0") -- surfacing
    the raw nested error tree would bury them.
    """
    errors = [
        {
            "field": ".".join(str(p) for p in e["loc"] if p not in ("body", "query")),
            "message": e["msg"],
            "type": e["type"],
        }
        for e in exc.errors()
    ]
    return SafeJSONResponse(
        status_code=422,
        content={
            "error": "validation_error",
            "message": "Request validation failed.",
            "details": errors,
            "request_id": getattr(request.state, "request_id", None),
        },
    )


@app.exception_handler(Exception)
async def unhandled_handler(request: Request, exc: Exception) -> SafeJSONResponse:
    request_id = getattr(request.state, "request_id", None)
    logger.exception("Unhandled error (request_id=%s)", request_id)
    return SafeJSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content={
            "error": "internal_error",
            # Never leak a traceback or exception text to the client -- it can
            # carry connection strings and file paths. The request_id is the
            # handle for correlating with the server log.
            "message": "An internal error occurred.",
            "request_id": request_id,
        },
    )


# ── Health ────────────────────────────────────────────────────────────────────
@app.get("/health", tags=["health"])
async def health() -> dict[str, str]:
    """Liveness: is the process up? Must never touch a dependency."""
    return {"status": "ok", "app": settings.APP_NAME, "environment": settings.ENVIRONMENT}


@app.get("/health/ready", tags=["health"])
async def ready() -> dict[str, object]:
    """
    Readiness: can we actually serve? Checks the DB (hard) and Redis (soft).

    Redis being down does NOT make us unready -- the cache layer fails open by
    design, so the app still serves correct results, just slower. Reporting
    unready would pull a healthy pod out of rotation for no reason.
    """
    from sqlalchemy import text

    from backend.infrastructure.db import engine

    checks: dict[str, object] = {}

    try:
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
        checks["database"] = "ok"
        db_ok = True
    except Exception as e:
        checks["database"] = f"error: {type(e).__name__}"
        db_ok = False

    client = await redis_client.get_redis()
    checks["redis"] = "ok" if client else "unavailable (degraded, not fatal)"

    return {"status": "ready" if db_ok else "not_ready", "checks": checks}


app.include_router(api_router, prefix=settings.API_V1_PREFIX)

# Upstox redirects here after login -- this EXACT bare path
# ("http://localhost:8000/callback") is what's registered in the Upstox
# Developer App dashboard, not the versioned /api/v1/auth/upstox/callback
# every other route lives under. Upstox validates redirect_uri against that
# registration and rejects any mismatch outright ("Invalid Credentials"),
# so this alias has to exist at the literal path, independent of API
# versioning conventions.
#
# Reuses the REAL handler function directly (add_api_route, not a duplicate
# copy) -- the state-binding CSRF check, the code exchange, and the account
# link all happen in exactly one place (api/v1/upstox_auth.py::callback).
# Excluded from the OpenAPI schema so /api/v1/auth/upstox/callback remains
# the one documented, canonical path; this is purely the external redirect
# target Upstox itself needs.
from backend.api.v1.upstox_auth import callback as _upstox_oauth_callback  # noqa: E402

app.add_api_route(
    "/callback",
    _upstox_oauth_callback,
    methods=["GET"],
    include_in_schema=False,
    tags=["upstox"],
)
