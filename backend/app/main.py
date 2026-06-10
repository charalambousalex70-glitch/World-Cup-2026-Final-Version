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
    # 'build' lets you confirm which backend version is actually live on Render.
    return {"status": "ok", "service": settings.PROJECT_NAME, "build": "v8-maxplayers"}
