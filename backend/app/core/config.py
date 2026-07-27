"""Application configuration loaded from environment variables."""
from functools import lru_cache
from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # Core
    PROJECT_NAME: str = "SweepStake Live"
    API_V1: str = "/api/v1"
    ENVIRONMENT: str = "development"

    # Database — Render provides DATABASE_URL. We normalise it to async driver.
    DATABASE_URL: str = "postgresql+asyncpg://postgres:postgres@localhost:5432/sweepstake"

    # Auth
    # FIX: default is now empty string instead of a publicly-known placeholder.
    # A @model_validator below raises at startup if JWT_SECRET is unset in
    # any non-development environment, preventing silent production insecurity.
    JWT_SECRET: str = ""
    JWT_ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 60 * 24 * 7  # 7 days

    # CORS — comma-separated list of allowed origins (your Vercel domain)
    CORS_ORIGINS: str = "http://localhost:5173,http://localhost:3000"

    # Football data API (https://www.football-data.org or API-FOOTBALL)
    FOOTBALL_API_URL: str = "https://api.football-data.org/v4"
    FOOTBALL_API_KEY: str = ""
    FOOTBALL_POLL_SECONDS: int = 30  # how often to poll for live results

    # Load demo data (the "Office World Cup" with fake players) on first boot.
    # Default OFF so real users start with a clean, empty app. Set AUTO_SEED=true
    # in the environment only if you want the demo data for testing.
    AUTO_SEED: bool = False

    # --- Email sending (OPTIONAL) ------------------------------------------
    # No provider is configured by default. To enable real sending of the admin
    # "League Update" emails, set these in the environment (e.g. on Render).
    # Until all required fields are present, the feature runs in DRY-RUN mode:
    # it generates and logs every email and reports what WOULD be sent, but
    # dispatches nothing. This is deliberate — we never guess a provider/key.
    SMTP_HOST: str = ""
    SMTP_PORT: int = 587
    SMTP_USER: str = ""
    SMTP_PASSWORD: str = ""
    SMTP_FROM_EMAIL: str = ""      # e.g. "LuckPot <updates@yourdomain.com>"
    SMTP_USE_TLS: bool = True

    @model_validator(mode="after")
    def _validate_jwt_secret(self) -> "Settings":
        """Prevent startup with an empty JWT_SECRET in non-development envs.
        In production, render.yaml uses generateValue: true which always sets
        a strong secret. This guard catches misconfigured manual deploys."""
        if self.ENVIRONMENT != "development" and not self.JWT_SECRET:
            raise ValueError(
                "JWT_SECRET must be set in non-development environments. "
                "Set it as an environment variable on Render (or use "
                "generateValue: true in render.yaml)."
            )
        return self

    @property
    def effective_jwt_secret(self) -> str:
        """Returns the JWT secret, falling back to a deterministic dev-only
        value so local docker-compose / tests work without extra config."""
        return self.JWT_SECRET or "dev-only-insecure-secret-do-not-use-in-production"

    @property
    def email_configured(self) -> bool:
        """True only when enough is set to actually send mail."""
        return bool(self.SMTP_HOST and self.SMTP_USER
                    and self.SMTP_PASSWORD and self.SMTP_FROM_EMAIL)

    @property
    def cors_list(self) -> list[str]:
        return [o.strip() for o in self.CORS_ORIGINS.split(",") if o.strip()]

    @property
    def async_database_url(self) -> str:
        """Render gives postgres://... — convert to async SQLAlchemy URL."""
        url = self.DATABASE_URL
        if url.startswith("postgres://"):
            url = url.replace("postgres://", "postgresql+asyncpg://", 1)
        elif url.startswith("postgresql://"):
            url = url.replace("postgresql://", "postgresql+asyncpg://", 1)
        return url


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
