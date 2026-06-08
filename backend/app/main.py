"""SweepStake Live — FastAPI application entrypoint."""
import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api import auth, sweepstakes
from app.core.config import settings
from app.core.database import Base, engine
from app.services.poller import poll_loop
from app.websocket import routes as ws_routes

logging.basicConfig(level=logging.INFO)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Create tables if they don't exist (Alembic is preferred for prod migrations).
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    # Start background football poller.
    task = asyncio.create_task(poll_loop())
    yield
    task.cancel()


app = FastAPI(title=settings.PROJECT_NAME, lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth.router, prefix=settings.API_V1)
app.include_router(sweepstakes.router, prefix=settings.API_V1)
app.include_router(ws_routes.router)  # WebSockets are not under /api/v1


@app.get("/health", tags=["meta"])
async def health():
    return {"status": "ok", "service": settings.PROJECT_NAME}
