"""
Async SQLAlchemy engine + session factory.

The app is async end-to-end (asyncpg in prod, aiosqlite in tests). Models use
dialect-portable column types so the exact same ORM runs against Postgres and
against the in-memory SQLite the test suite uses -- no Docker needed to run
tests, no separate "test models".
"""
from __future__ import annotations

import logging
from collections.abc import AsyncGenerator
from typing import Any

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase

from backend.core.config import settings

logger = logging.getLogger(__name__)


class Base(DeclarativeBase):
    """Declarative base for every ORM model."""


def _engine_kwargs(url: str) -> dict[str, Any]:
    # SQLite (tests) rejects pool_size/max_overflow; Postgres wants them.
    if url.startswith("sqlite"):
        return {}
    return {
        "pool_size": settings.DB_POOL_SIZE,
        "max_overflow": settings.DB_MAX_OVERFLOW,
        "pool_pre_ping": True,  # survives a Postgres restart / idle timeout
        "pool_recycle": 1800,
    }


engine: AsyncEngine = create_async_engine(
    settings.DATABASE_URL,
    echo=settings.DB_ECHO,
    future=True,
    **_engine_kwargs(settings.DATABASE_URL),
)

SessionLocal: async_sessionmaker[AsyncSession] = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,  # keep attributes readable after commit (response serialization)
    autoflush=False,
)


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """
    FastAPI dependency yielding a session per request.

    Commit is explicit in the service layer. We roll back on any exception so a
    failed request can never leave a half-applied transaction on the connection
    when it returns to the pool.
    """
    async with SessionLocal() as session:
        try:
            yield session
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()


async def dispose_engine() -> None:
    await engine.dispose()
