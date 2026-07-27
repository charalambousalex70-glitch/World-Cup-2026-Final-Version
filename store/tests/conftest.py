"""Shared test fixtures.

Every test run gets its own throwaway SQLite database, so tests can never
corrupt your real catalog and never interfere with each other.
"""
import os
import tempfile
import uuid
from pathlib import Path

# Point the app at a temporary database BEFORE anything imports the settings
# object, because settings are read once at import time and then cached.
_TMP_DB = Path(tempfile.gettempdir()) / f"store-test-{uuid.uuid4().hex}.db"
os.environ["DATABASE_URL"] = f"sqlite+aiosqlite:///{_TMP_DB}"
os.environ["IMAGE_BASE_URL"] = "/images"
os.environ["STORE_NAME"] = "Test Store"

import pytest  # noqa: E402
import pytest_asyncio  # noqa: E402
from httpx import ASGITransport, AsyncClient  # noqa: E402

from app.core.database import AsyncSessionLocal, Base, engine  # noqa: E402
from app.main import app  # noqa: E402
from app.models import Category, Product  # noqa: E402


@pytest.fixture(scope="session")
def browser_type_launch_args(browser_type_launch_args):
    """Use a pre-installed Chromium if the exact build Playwright wants is absent.

    Playwright pins one Chromium build per release. CI images (and this dev
    container) often ship a nearby build instead, which makes Playwright stop
    and demand `playwright install`. Pointing it at the Chromium that IS on
    disk avoids a 150MB download on every machine. Locally, where `playwright
    install` has been run, this does nothing.
    """
    preinstalled = Path("/opt/pw-browsers/chromium")
    if preinstalled.exists():
        return {**browser_type_launch_args, "executable_path": str(preinstalled)}
    return browser_type_launch_args


@pytest_asyncio.fixture(scope="function")
async def db_ready():
    """Fresh, empty tables for each test."""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
    yield
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)


@pytest_asyncio.fixture
async def seeded(db_ready):
    """A small, known catalog: 2 categories, 3 products, one of them sold out."""
    async with AsyncSessionLocal() as db:
        shirts = Category(slug="t-shirts", name="T-Shirts", sort_order=0)
        mugs = Category(slug="homeware", name="Homeware", sort_order=1)
        db.add_all([shirts, mugs])
        await db.flush()

        db.add_all([
            Product(slug="home-tee", name="Home Tee", price_cents=1999,
                    currency="GBP", category_id=shirts.id, stock_qty=10,
                    image_url="/images/home-tee.jpg", image_alt="A navy tee",
                    description="Soft cotton tee.", sort_order=1),
            Product(slug="away-tee", name="Away Tee", price_cents=2450,
                    currency="GBP", category_id=shirts.id, stock_qty=0,
                    image_alt="A white tee", sort_order=2),
            Product(slug="team-mug", name="Team Mug", price_cents=999,
                    currency="GBP", category_id=mugs.id, stock_qty=99,
                    image_alt="A white mug", sort_order=3),
            # Switched off: must never appear in the shop.
            Product(slug="secret-tee", name="Secret Tee", price_cents=100,
                    currency="GBP", category_id=shirts.id, stock_qty=5,
                    is_active=False, sort_order=4),
        ])
        await db.commit()
    yield


@pytest_asyncio.fixture
async def client():
    """Talks to the app in-process — no network, no port, no flakiness."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


def pytest_sessionfinish(session, exitstatus):
    _TMP_DB.unlink(missing_ok=True)
