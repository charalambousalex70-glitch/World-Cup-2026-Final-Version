"""FastAPI entrypoint. Serves both the JSON API and the storefront.

One service, not two. For a shop this size that is cheaper (one Railway
service), simpler (no CORS, no second deploy), and faster to ship. If the
storefront ever outgrows this, it can move to its own host and the API keeps
working unchanged — CORS_ORIGINS already exists for that day.
"""
import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from app.api import catalog, checkout
from app.core.config import settings
from app.core.database import Base, engine

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("store")

WEB_DIR = Path(__file__).resolve().parent.parent / "web"


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Create any missing tables on boot. This is fine while the schema is
    # young. Once real orders exist (Phase 2), switch to Alembic migrations so
    # schema changes are reviewable and reversible.
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    log.info("Store ready — %s database", "sqlite" if settings.is_sqlite else "postgres")

    if not settings.stripe_configured:
        log.warning("Stripe is not configured: browsing works, checkout is disabled.")
    elif settings.stripe_live_mode:
        # Loud on purpose. Real cards are being charged from this point on.
        log.warning("STRIPE IS IN LIVE MODE — real payments will be taken.")
    else:
        log.info("Stripe in TEST mode — use card 4242 4242 4242 4242.")

    yield
    await engine.dispose()


app = FastAPI(title=settings.PROJECT_NAME, lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    # Never "*" — that would let any website on the internet call this API
    # from a visitor's browser.
    allow_origins=settings.cors_list,
    allow_credentials=True,
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)

app.include_router(catalog.router, prefix=settings.API_V1, tags=["catalog"])
app.include_router(checkout.router, prefix=settings.API_V1, tags=["checkout"])


@app.get("/health")
async def health() -> dict:
    """Railway pings this to decide whether the deploy succeeded."""
    return {"status": "ok"}


@app.get("/api/v1/config")
async def public_config() -> dict:
    """Settings the storefront needs to render itself."""
    return {
        "store_name": settings.STORE_NAME,
        "currency": settings.CURRENCY,
        "currency_symbol": settings.CURRENCY_SYMBOL,
        "shipping_cents": settings.SHIPPING_FLAT_CENTS,
        "checkout_available": settings.stripe_configured,
    }


# --- Static files --------------------------------------------------------
# Product photos. In Phase 2 these move to Cloudflare R2 and this mount can go.
(WEB_DIR / "images").mkdir(parents=True, exist_ok=True)
app.mount("/images", StaticFiles(directory=WEB_DIR / "images"), name="images")


@app.get("/")
async def storefront() -> FileResponse:
    return FileResponse(WEB_DIR / "index.html")


@app.get("/product/{slug}")
async def product_page(slug: str) -> FileResponse:
    """Deep links to a product still serve the same single page; the
    JavaScript reads the path and opens the right product."""
    return FileResponse(WEB_DIR / "index.html")


@app.get("/thanks")
async def thanks_page() -> FileResponse:
    """Where Stripe returns the customer after a successful payment."""
    return FileResponse(WEB_DIR / "thanks.html")


@app.get("/orders")
async def orders_page() -> FileResponse:
    """'Where is my order?' — guests have no account, so they look it up."""
    return FileResponse(WEB_DIR / "orders.html")


@app.exception_handler(500)
async def internal_error(request, exc) -> JSONResponse:
    """Never leak a stack trace to a customer."""
    log.exception("Unhandled error on %s", request.url.path)
    return JSONResponse({"detail": "Something went wrong on our end."}, status_code=500)
