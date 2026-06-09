# Copy to .env for local dev. On Render, set these as environment variables.

ENVIRONMENT=development

# Postgres. Locally: spin up via docker or use a Render DB URL.
DATABASE_URL=postgresql+asyncpg://postgres:postgres@localhost:5432/sweepstake

# Generate a strong secret:  python -c "import secrets;print(secrets.token_urlsafe(48))"
JWT_SECRET=change-me-in-production

# Comma-separated allowed frontend origins (your Vercel URL in prod).
CORS_ORIGINS=http://localhost:5173,http://localhost:3000

# Football data provider (https://www.football-data.org — free tier available).
# Leave the key blank to run in OFFLINE mode using sample/seed data.
FOOTBALL_API_URL=https://api.football-data.org/v4
FOOTBALL_API_KEY=
FOOTBALL_POLL_SECONDS=60
