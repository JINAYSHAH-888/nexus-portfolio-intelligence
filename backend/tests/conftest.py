"""
Test fixtures.

Per CLAUDE.md §7 the suite runs against in-memory SQLite via an
`httpx.AsyncClient` wired to the REAL FastAPI app with `get_db` overridden --
no Docker, no Postgres, no Redis.

Environment is pinned BEFORE any backend module is imported, because
`core.config.settings` is a module-level singleton built at import time.
Three settings matter:

  MARKET_DATA_FORCE_SYNTHETIC=true
      The sandbox has no egress to Yahoo/AMFI/Alpha Vantage (CLAUDE.md §3).
      Forcing synthetic makes the fallback tiers deterministic instead of
      spending 30s per test timing out against unreachable hosts.
  REDIS_ENABLED=false
      Exercises the fail-open cache path, which is what CI and local dev
      actually run.
  DATABASE_URL=sqlite+aiosqlite:///:memory:
"""
from __future__ import annotations

import os

os.environ.setdefault("ENVIRONMENT", "test")
os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///:memory:")
os.environ.setdefault("SECRET_KEY", "test-secret-key-not-for-production-use-0123456789")
os.environ.setdefault("REDIS_ENABLED", "false")
os.environ.setdefault("RATE_LIMIT_ENABLED", "false")
os.environ.setdefault("MARKET_DATA_FORCE_SYNTHETIC", "true")
os.environ.setdefault("CELERY_TASK_ALWAYS_EAGER", "true")
os.environ.setdefault("LOG_LEVEL", "WARNING")

from collections.abc import AsyncGenerator  # noqa: E402

import pytest_asyncio  # noqa: E402
from httpx import ASGITransport, AsyncClient  # noqa: E402
from sqlalchemy.ext.asyncio import (  # noqa: E402
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import StaticPool  # noqa: E402

from backend.infrastructure.db import Base, get_db  # noqa: E402
from backend.main import app  # noqa: E402

TEST_DB_URL = "sqlite+aiosqlite:///:memory:"


@pytest_asyncio.fixture
async def engine():
    # StaticPool + a single shared connection: without it every new connection
    # to ":memory:" gets its own EMPTY database, so a row written in the request
    # would be invisible to the assertion that follows.
    eng = create_async_engine(
        TEST_DB_URL,
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    async with eng.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield eng
    await eng.dispose()


@pytest_asyncio.fixture
async def db_session(engine) -> AsyncGenerator[AsyncSession, None]:
    maker = async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)
    async with maker() as session:
        yield session


@pytest_asyncio.fixture
async def client(engine) -> AsyncGenerator[AsyncClient, None]:
    maker = async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)

    async def _override_get_db() -> AsyncGenerator[AsyncSession, None]:
        async with maker() as session:
            try:
                yield session
            finally:
                await session.close()

    app.dependency_overrides[get_db] = _override_get_db
    # raise_app_exceptions=False makes ASGITransport behave like a real HTTP
    # client: an unhandled exception must come back as a 500 RESPONSE (via our
    # handler), not be re-raised into the test. Without this we could never
    # assert on what a 500 body actually contains.
    transport = ASGITransport(app=app, raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac
    app.dependency_overrides.clear()


# ── Convenience payloads ──────────────────────────────────────────────────────
VALID_PROFILE = {
    "full_name": "Ananya Rao",
    "date_of_birth": "1986-04-12",
    "occupation": "Software Architect",
    "monthly_income": 250_000,
    "total_corpus": 4_000_000,
    "investment_amount": 1_500_000,
    "goal": "growth",
    "risk_appetite": "moderate",
    "target_retirement_age": 60,
    "monthly_expenses_in_retirement": 120_000,
}


@pytest_asyncio.fixture
async def registered(client: AsyncClient) -> dict:
    r = await client.post(
        "/api/v1/auth/register",
        json={
            "email": "ananya@example.com",
            "password": "Str0ngPassw0rd!",
            "full_name": "Ananya Rao",
        },
    )
    assert r.status_code == 201, r.text
    body = r.json()
    return {
        "user_id": body["user_id"],
        "access": body["access_token"],
        "refresh": body["refresh_token"],
        "headers": {"Authorization": f"Bearer {body['access_token']}"},
    }


@pytest_asyncio.fixture
async def profiled(client: AsyncClient, registered: dict) -> dict:
    r = await client.post(
        "/api/v1/profile", json=VALID_PROFILE, headers=registered["headers"]
    )
    assert r.status_code == 201, r.text
    return registered


@pytest_asyncio.fixture
async def selected(client: AsyncClient, profiled: dict) -> dict:
    """A realistic 3-scheme mixed equity+debt selection with explicit weights."""
    ids = ["nippon-large-cap-fund", "nippon-small-cap-fund", "nippon-corporate-bond-fund"]
    r = await client.post(
        "/api/v1/schemes/select",
        json={
            "selected_scheme_ids": ids,
            "weights": {ids[0]: 0.5, ids[1]: 0.2, ids[2]: 0.3},
        },
        headers=profiled["headers"],
    )
    assert r.status_code == 200, r.text
    return {**profiled, "scheme_ids": ids}
