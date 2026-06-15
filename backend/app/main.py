"""SweepStake Live — FastAPI application entrypoint."""
import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import select

from app.api import auth, sweepstakes
from app.core.config import settings
from app.core.database import AsyncSessionLocal, Base, engine
from app.services.poller import poll_loop
from app.websocket import routes as ws_routes

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("startup")


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Create tables if they don't exist.
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        # Lightweight idempotent migration for columns added after launch.
        # ADD COLUMN IF NOT EXISTS is safe to run on every boot.
        try:
            from sqlalchemy import text
            for ddl in (
                "ALTER TABLE fixtures ADD COLUMN IF NOT EXISTS venue VARCHAR(160)",
                "ALTER TABLE fixtures ADD COLUMN IF NOT EXISTS referee VARCHAR(120)",
                "ALTER TABLE fixtures ADD COLUMN IF NOT EXISTS detail TEXT",
                "ALTER TABLE comments ADD COLUMN IF NOT EXISTS reactions TEXT",
                "ALTER TABLE comments ALTER COLUMN user_id DROP NOT NULL",
                "ALTER TABLE sweepstakes ADD COLUMN IF NOT EXISTS draw_audit TEXT",
                "ALTER TABLE sweepstakes ADD COLUMN IF NOT EXISTS standings TEXT",
            ):
                await conn.execute(text(ddl))
        except Exception:
            pass  # never block startup on a migration nicety

    # Auto-seed demo data on first boot so the app is usable immediately
    # (no manual Shell step). Safe to run every boot: it no-ops if data exists.
    if settings.AUTO_SEED:
        try:
            from app.models import User
            from app.seed import seed
            async with AsyncSessionLocal() as db:
                # Check specifically for the demo user, not just any data, so a
                # half-populated DB still gets the demo login created.
                demo = (
                    await db.execute(
                        select(User.id).where(User.email == "you@example.com")
                    )
                ).scalar_one_or_none()
            if not demo:
                log.info("Demo user missing — running demo seed.")
                await seed()
            else:
                log.info("Demo user present — skipping seed.")
        except Exception:
            log.exception("Auto-seed skipped due to error (app still starts).")

    task = asyncio.create_task(poll_loop())
    yield
    task.cancel()


app = FastAPI(title=settings.PROJECT_NAME, lifespan=lifespan)

# Allow the configured origins exactly, plus any *.vercel.app preview/prod URL
# via regex — so renaming the Vercel project never breaks CORS again.
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_list,
    allow_origin_regex=r"https://.*\.vercel\.app",
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth.router, prefix=settings.API_V1)
app.include_router(sweepstakes.router, prefix=settings.API_V1)
app.include_router(ws_routes.router)  # WebSockets are not under /api/v1


# Global error handler: log the real traceback (so 500s are debuggable in the
# Render logs) AND return permissive CORS headers on the error response. Without
# this, an unhandled exception skips the CORS middleware and the browser reports
# a confusing "No Access-Control-Allow-Origin" error instead of the real 500.
import logging as _logging
from fastapi import Request as _Request
from fastapi.responses import JSONResponse as _JSONResponse

_err_log = _logging.getLogger("api.errors")


@app.exception_handler(Exception)
async def _unhandled(request: _Request, exc: Exception):
    _err_log.exception("Unhandled error on %s %s", request.method, request.url.path)
    origin = request.headers.get("origin", "*")
    return _JSONResponse(
        status_code=500,
        content={"detail": "Internal server error", "where": request.url.path},
        headers={
            "Access-Control-Allow-Origin": origin,
            "Access-Control-Allow-Credentials": "true",
        },
    )


@app.get("/", tags=["meta"])
@app.head("/", tags=["meta"])
async def root():
    return {
        "service": settings.PROJECT_NAME,
        "status": "running",
        "docs": "/docs",
        "health": "/health",
    }


@app.get("/health", tags=["meta"])
async def health():
    # 'build' confirms which backend version is live; 'poller' confirms the
    # background job runs; 'feed' shows the data-source health (e.g. 403 = the
    # plan isn't authorised for this competition).
    from app.services.poller import POLLER_STATS
    from app.services.football import FEED_HEALTH
    _k = settings.FOOTBALL_API_KEY or ""
    return {"status": "ok", "service": settings.PROJECT_NAME,
            "build": "v45-prediction-drilldown", "poller": POLLER_STATS, "feed": FEED_HEALTH,
            "api_key_fingerprint": (f"{_k[:4]}…{_k[-4:]} (len {len(_k)})" if _k else "MISSING")}
