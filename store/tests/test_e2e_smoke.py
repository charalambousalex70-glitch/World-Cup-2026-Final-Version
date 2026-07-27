"""Browser tests — Levels 1 & 2 through a real Chromium.

These are the tests that answer "does the shop actually work for a human?":
does the page load, are there JavaScript errors, do the filters filter, does
add-to-cart really add to the cart, does the cart survive a refresh.

Run: .venv/bin/pytest tests/test_e2e_smoke.py

The server is started and stopped for you — nothing to run by hand.

Tip: you can also record NEW tests like these by clicking through the site with
the Playwright CRX Chrome extension, which writes the Python for you.
"""
import os
import socket
import subprocess
import sys
import tempfile
import time
import uuid
from pathlib import Path

import pytest
from playwright.sync_api import expect

ROOT = Path(__file__).resolve().parent.parent


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="session")
def live_server():
    """Start the app on a spare port with its own throwaway catalog."""
    port = _free_port()
    db_path = Path(tempfile.gettempdir()) / f"store-e2e-{uuid.uuid4().hex}.db"

    env = {
        **os.environ,
        "DATABASE_URL": f"sqlite+aiosqlite:///{db_path}",
        "STORE_NAME": "Test Store",
        "IMAGE_BASE_URL": "/images",
    }
    python = str(ROOT / ".venv" / "bin" / "python")
    if not Path(python).exists():
        python = sys.executable

    # Load the sample catalog into the throwaway database.
    subprocess.run(
        [python, "-m", "app.seed", "data/products.csv"],
        cwd=ROOT, env=env, check=True, capture_output=True,
    )

    proc = subprocess.Popen(
        [python, "-m", "uvicorn", "app.main:app", "--port", str(port), "--log-level", "warning"],
        cwd=ROOT, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
    )

    base = f"http://127.0.0.1:{port}"
    for _ in range(100):                       # wait up to ~10s for boot
        if proc.poll() is not None:
            raise RuntimeError(f"Server died:\n{proc.stdout.read().decode()}")
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.2):
                break
        except OSError:
            time.sleep(0.1)
    else:
        proc.kill()
        raise RuntimeError("Server did not start in time")

    yield base

    proc.terminate()
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        proc.kill()
    db_path.unlink(missing_ok=True)


@pytest.fixture
def shop(page, live_server):
    """Open the shop and fail the test on any JavaScript error (Level 1)."""
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.goto(live_server, wait_until="networkidle")
    yield page
    assert not errors, f"JavaScript errors on the page: {errors}"


# --------------------------------------------------------------------------
# Level 1 — smoke
# --------------------------------------------------------------------------

def test_page_loads_with_key_elements(shop):
    expect(shop.locator("#grid")).to_be_visible()
    expect(shop.locator("#openCart")).to_be_visible()
    expect(shop.locator("#filters")).to_be_visible()


def test_products_render(shop):
    expect(shop.locator(".card")).to_have_count(3)
    expect(shop.locator(".card").first).to_be_visible()


def test_prices_show_as_pounds_not_pence(shop):
    """1999 in the database must read as £19.99 on screen."""
    expect(shop.locator(".card", has_text="World Cup 2026 Home Tee")
              .locator(".price")).to_have_text("£19.99")


def test_filter_buttons_include_all_plus_categories(shop):
    chips = shop.locator("#filters .chip")
    expect(chips).to_have_count(4)          # All + 3 categories
    expect(chips.first).to_have_text("All")


# --------------------------------------------------------------------------
# Level 2 — functional
# --------------------------------------------------------------------------

def test_category_filter_narrows_the_grid(shop):
    shop.locator("#filters .chip", has_text="T-Shirts").click()
    expect(shop.locator(".card")).to_have_count(1)
    expect(shop.locator(".card").first).to_contain_text("World Cup 2026 Home Tee")

    shop.locator("#filters .chip", has_text="All").click()
    expect(shop.locator(".card")).to_have_count(3)


def test_add_to_cart_updates_the_badge(shop):
    expect(shop.locator("#cartCount")).to_be_hidden()

    shop.locator(".card", has_text="Team Mug").locator(".add").click()
    expect(shop.locator("#cartCount")).to_be_visible()
    expect(shop.locator("#cartCount")).to_have_text("1")

    shop.locator(".card", has_text="Supporter Scarf").locator(".add").click()
    expect(shop.locator("#cartCount")).to_have_text("2")


def test_cart_totals_are_correct(shop):
    """Two mugs at £9.99 plus one scarf at £14.50 must total £34.48."""
    mug = shop.locator(".card", has_text="Team Mug").locator(".add")
    mug.click()
    mug.click()
    shop.locator(".card", has_text="Supporter Scarf").locator(".add").click()

    shop.locator("#openCart").click()
    expect(shop.locator("#drawer")).to_have_class("drawer open")
    expect(shop.locator("#total")).to_have_text("£34.48")


def test_quantity_controls_and_removal(shop):
    shop.locator(".card", has_text="Team Mug").locator(".add").click()
    shop.locator("#openCart").click()

    expect(shop.locator("#total")).to_have_text("£9.99")

    shop.locator(".qty button", has_text="+").click()
    expect(shop.locator("#total")).to_have_text("£19.98")

    shop.locator(".qty button", has_text="−").click()
    expect(shop.locator("#total")).to_have_text("£9.99")

    shop.locator(".rm").click()
    expect(shop.locator("#drawerBody")).to_contain_text("Your cart is empty")
    expect(shop.locator("#cartCount")).to_be_hidden()


def test_cart_survives_a_page_refresh(shop):
    """The whole point of keeping the cart in the browser."""
    shop.locator(".card", has_text="Team Mug").locator(".add").click()
    expect(shop.locator("#cartCount")).to_have_text("1")

    shop.reload(wait_until="networkidle")

    expect(shop.locator("#cartCount")).to_have_text("1")
    shop.locator("#openCart").click()
    expect(shop.locator("#total")).to_have_text("£9.99")


def test_product_detail_opens(shop):
    shop.locator(".card", has_text="Team Mug").locator("h3 button").click()
    expect(shop.locator("#drawerBody")).to_contain_text("350ml ceramic mug")
    expect(shop.locator("#drawerBody .price")).to_have_text("£9.99")


def test_checkout_is_visibly_not_ready_yet(shop):
    """Phase 1 has no payment. The button must say so rather than fail silently."""
    shop.locator(".card", has_text="Team Mug").locator(".add").click()
    shop.locator("#openCart").click()
    expect(shop.locator("#checkout")).to_be_disabled()
    expect(shop.locator(".soon")).to_contain_text("Phase 2")


def test_deep_link_to_a_product(shop, live_server):
    shop.goto(f"{live_server}/product/team-mug", wait_until="networkidle")
    expect(shop.locator("#drawerBody")).to_contain_text("Team Mug")
