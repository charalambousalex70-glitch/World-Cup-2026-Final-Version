"""Async SQLAlchemy engine, session factory and declarative base."""
from collections.abc import AsyncGenerator

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from app.core.config import settings

engine = create_async_engine(
    settings.async_database_url,
    echo=False,
    # NOTE: pool_pre_ping doesn't play well with the async asyncpg driver — its
    # connection "ping" runs outside the async context and raises MissingGreenlet.
    # Instead we recycle connections periodically, which keeps the pool healthy
    # (important on hosts that drop idle connections) without the broken ping.
    pool_recycle=280,        # recycle connections older than ~4.5 min
    pool_size=5,
    max_overflow=10,
    pool_timeout=30,
)

AsyncSessionLocal = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)


class Base(DeclarativeBase):
    """Declarative base for all ORM models."""


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """FastAPI dependency that yields a database session."""
    async with AsyncSessionLocal() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            # Roll back any partial work. Guarded so a double-rollback (e.g. a
            # handler already rolled back) can never raise a second error.
            try:
                await session.rollback()
            except Exception:
                pass
            raise
