"""Application settings, read from environment variables.

Adapted from the SweepStake Live codebase (backend/app/core/config.py) — the
URL-normalising and validation logic is proven in production. Football/websocket
settings removed; storefront settings added.
"""
from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # --- Core ---------------------------------------------------------------
    PROJECT_NAME: str = "Online Store"
    STORE_NAME: str = "Our Store"
    API_V1: str = "/api/v1"
    ENVIRONMENT: str = "development"

    # --- Database -----------------------------------------------------------
    # Local default is SQLite so the app runs with zero setup. Railway injects a
    # real DATABASE_URL for Postgres, which we normalise to the async driver
    # below. Both work because SQLAlchemy speaks to each through the same API.
    DATABASE_URL: str = "sqlite+aiosqlite:///./store.db"

    # --- Money --------------------------------------------------------------
    # Prices are stored as whole pence (integers). See docs/PRD-STORE.md §4.2.
    CURRENCY: str = "GBP"
    CURRENCY_SYMBOL: str = "£"

    # --- Product images -----------------------------------------------------
    # Phase 1: images are files served by this app from /images.
    # Phase 2: point this at the Cloudflare R2 public bucket URL. Because the
    # products table stores a COMPLETE url, that switch needs no schema change.
    IMAGE_BASE_URL: str = "/images"

    # --- Shipping (Phase 2) -------------------------------------------------
    # Flat fee per order, in pence. 499 = £4.99. Kept in the environment so the
    # price can change without a code change; set to 0 for free shipping.
    SHIPPING_FLAT_CENTS: int = 499

    # --- CORS ---------------------------------------------------------------
    # Phase 1 serves the frontend from this same app, so no cross-origin calls
    # happen at all. This exists for later, if the frontend moves to its own host.
    CORS_ORIGINS: str = "http://localhost:8000,http://127.0.0.1:8000"

    @property
    def cors_list(self) -> list[str]:
        return [o.strip() for o in self.CORS_ORIGINS.split(",") if o.strip()]

    @property
    def is_sqlite(self) -> bool:
        return self.async_database_url.startswith("sqlite")

    @property
    def async_database_url(self) -> str:
        """Railway/Render hand us `postgres://...`; SQLAlchemy's async engine
        needs `postgresql+asyncpg://...`. Normalise whatever we are given."""
        url = self.DATABASE_URL
        if url.startswith("postgres://"):
            url = url.replace("postgres://", "postgresql+asyncpg://", 1)
        elif url.startswith("postgresql://"):
            url = url.replace("postgresql://", "postgresql+asyncpg://", 1)
        elif url.startswith("sqlite://") and "+aiosqlite" not in url:
            url = url.replace("sqlite://", "sqlite+aiosqlite://", 1)
        return url


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
