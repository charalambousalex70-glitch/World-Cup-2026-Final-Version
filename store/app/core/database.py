"""Async SQLAlchemy engine, session factory and declarative base.

Adapted from SweepStake Live (backend/app/core/database.py). The Postgres
pool tuning there was worked out against real cloud cold-starts, so it is kept
verbatim — but it only applies to Postgres. SQLite (local dev) rejects those
pool arguments, hence the branch below.
"""
from collections.abc import AsyncGenerator

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from app.core.config import settings

if settings.is_sqlite:
    # Local dev. SQLite has no connection pool to tune.
    engine = create_async_engine(settings.async_database_url, echo=False)
else:
    engine = create_async_engine(
        settings.async_database_url,
        echo=False,
        # NOTE: pool_pre_ping is deliberately NOT used — its connection "ping"
        # runs outside the async context and raises MissingGreenlet with
        # asyncpg. Recycling connections keeps the pool healthy instead, which
        # matters on hosts that drop idle connections.
        pool_recycle=280,
        pool_size=5,
        max_overflow=10,
        pool_timeout=30,
        connect_args={"command_timeout": 60},  # asyncpg: max seconds per query
    )

AsyncSessionLocal = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)


class Base(DeclarativeBase):
    """Declarative base for all ORM models."""


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """FastAPI dependency that hands each request its own database session."""
    async with AsyncSessionLocal() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            # Roll back partial work. Guarded so a double-rollback (a handler
            # that already rolled back) can never raise a second error and mask
            # the original one.
            try:
                await session.rollback()
            except Exception:
                pass
            raise
