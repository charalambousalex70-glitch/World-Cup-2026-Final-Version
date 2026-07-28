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
    """Two mugs at £9.99 plus a scarf at £14.50 is £34.48, plus £4.99 shipping."""
    mug = shop.locator(".card", has_text="Team Mug").locator(".add")
    mug.click()
    mug.click()
    shop.locator(".card", has_text="Supporter Scarf").locator(".add").click()

    shop.locator("#openCart").click()
    expect(shop.locator("#drawer")).to_have_class("drawer open")
    expect(shop.locator("#subtotal")).to_have_text("£34.48")
    expect(shop.locator("#shipping")).to_have_text("£4.99")
    expect(shop.locator("#total")).to_have_text("£39.47")


def test_quantity_controls_and_removal(shop):
    shop.locator(".card", has_text="Team Mug").locator(".add").click()
    shop.locator("#openCart").click()

    expect(shop.locator("#subtotal")).to_have_text("£9.99")
    expect(shop.locator("#total")).to_have_text("£14.98")     # + £4.99 shipping

    shop.locator(".qty button", has_text="+").click()
    expect(shop.locator("#subtotal")).to_have_text("£19.98")
    expect(shop.locator("#total")).to_have_text("£24.97")

    shop.locator(".qty button", has_text="−").click()
    expect(shop.locator("#subtotal")).to_have_text("£9.99")

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
    expect(shop.locator("#subtotal")).to_have_text("£9.99")


def test_product_detail_opens(shop):
    shop.locator(".card", has_text="Team Mug").locator("h3 button").click()
    expect(shop.locator("#drawerBody")).to_contain_text("350ml ceramic mug")
    expect(shop.locator("#drawerBody .price")).to_have_text("£9.99")


def test_checkout_says_so_when_stripe_is_not_configured(shop):
    """This test server has no Stripe keys, so the button must say so plainly
    rather than looking clickable and then failing."""
    shop.locator(".card", has_text="Team Mug").locator(".add").click()
    shop.locator("#openCart").click()
    expect(shop.locator("#checkout")).to_be_disabled()
    expect(shop.locator("#payNote")).to_contain_text("aren't switched on")


def test_deep_link_to_a_product(shop, live_server):
    shop.goto(f"{live_server}/product/team-mug", wait_until="networkidle")
    expect(shop.locator("#drawerBody")).to_contain_text("Team Mug")


# --------------------------------------------------------------------------
# Checkout, order tracking and the pages Stripe returns customers to
# --------------------------------------------------------------------------

def test_cart_asks_for_an_email(shop):
    shop.locator(".card", has_text="Team Mug").locator(".add").click()
    shop.locator("#openCart").click()
    expect(shop.locator("#email")).to_be_visible()
    expect(shop.locator("label[for=email]")).to_contain_text("receipt")


def test_track_order_link_reaches_the_lookup_page(shop):
    shop.locator(".navlink", has_text="Track order").click()
    expect(shop.locator("h1")).to_contain_text("Find your order")
    expect(shop.locator("#num")).to_be_visible()
    expect(shop.locator("#email")).to_be_visible()


def test_order_lookup_needs_both_fields(page, live_server):
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.goto(f"{live_server}/orders", wait_until="networkidle")

    page.locator("#find").click()
    expect(page.locator("#err")).to_contain_text("both")

    page.locator("#num").fill("ORD-ABCD2345")
    page.locator("#email").fill("nobody@example.com")
    page.locator("#find").click()
    expect(page.locator("#err")).to_contain_text("couldn't find")

    assert not errors, f"JavaScript errors: {errors}"


def test_thanks_page_survives_no_session_id(page, live_server):
    """Someone visiting /thanks directly must get a sensible page, not a crash."""
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.goto(f"{live_server}/thanks", wait_until="networkidle")

    expect(page.locator("h1")).to_contain_text("Thank you")
    expect(page.locator(".btn")).to_contain_text("Back to the shop")
    assert not errors, f"JavaScript errors: {errors}"


def test_cancelled_payment_keeps_the_cart(page, live_server):
    """Returning from a cancelled Stripe payment must not lose the basket."""
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.goto(live_server, wait_until="networkidle")
    page.locator(".card", has_text="Team Mug").locator(".add").click()

    page.goto(f"{live_server}/?checkout=cancelled", wait_until="networkidle")
    expect(page.locator("#cartCount")).to_have_text("1")
    assert not errors, f"JavaScript errors: {errors}"
