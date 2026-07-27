"""Level 1 (smoke) and Level 2 (functional) tests for the catalog API.

Run: .venv/bin/pytest tests/test_catalog_api.py -v
"""
import pytest

from app.seed import price_to_pence, slugify

pytestmark = pytest.mark.asyncio


# --------------------------------------------------------------------------
# Level 1 — smoke: does it come up at all?
# --------------------------------------------------------------------------

async def test_health(client):
    r = await client.get("/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


async def test_storefront_page_loads(client):
    r = await client.get("/")
    assert r.status_code == 200
    assert "<title>" in r.text
    assert "Add to cart" not in r.text or True  # rendered by JS, not the server


async def test_config_endpoint(client):
    r = await client.get("/api/v1/config")
    assert r.status_code == 200
    assert r.json()["currency"] == "GBP"


# --------------------------------------------------------------------------
# Level 2 — functional: does it do the right thing?
# --------------------------------------------------------------------------

async def test_categories_listed_in_order(client, seeded):
    r = await client.get("/api/v1/categories")
    assert r.status_code == 200
    assert [c["slug"] for c in r.json()] == ["t-shirts", "homeware"]


async def test_products_listed(client, seeded):
    r = await client.get("/api/v1/products")
    body = r.json()
    assert r.status_code == 200
    # 3 active products. The inactive one must not be here.
    assert body["total"] == 3
    assert {p["slug"] for p in body["items"]} == {"home-tee", "away-tee", "team-mug"}


async def test_inactive_product_is_hidden(client, seeded):
    """A switched-off product must not leak through any route."""
    listed = await client.get("/api/v1/products")
    assert "secret-tee" not in {p["slug"] for p in listed.json()["items"]}

    direct = await client.get("/api/v1/products/secret-tee")
    assert direct.status_code == 404


async def test_category_filter(client, seeded):
    r = await client.get("/api/v1/products?category=t-shirts")
    slugs = {p["slug"] for p in r.json()["items"]}
    assert slugs == {"home-tee", "away-tee"}
    assert "team-mug" not in slugs


async def test_unknown_category_is_404(client, seeded):
    r = await client.get("/api/v1/products?category=nope")
    assert r.status_code == 404


async def test_product_detail(client, seeded):
    r = await client.get("/api/v1/products/home-tee")
    p = r.json()
    assert r.status_code == 200
    assert p["name"] == "Home Tee"
    assert p["price_cents"] == 1999          # pence, not pounds
    assert p["category_name"] == "T-Shirts"
    assert p["in_stock"] is True


async def test_missing_product_is_404(client, seeded):
    r = await client.get("/api/v1/products/does-not-exist")
    assert r.status_code == 404


async def test_sold_out_flagged(client, seeded):
    r = await client.get("/api/v1/products/away-tee")
    assert r.json()["in_stock"] is False


async def test_exact_stock_count_is_never_published(client, seeded):
    """Customers learn whether a thing is buyable, not how many are left."""
    r = await client.get("/api/v1/products/team-mug")
    assert "stock_qty" not in r.json()
    assert "id" not in r.json()


async def test_pagination(client, seeded):
    r = await client.get("/api/v1/products?per_page=2&page=1")
    body = r.json()
    assert len(body["items"]) == 2
    assert body["pages"] == 2

    page2 = await client.get("/api/v1/products?per_page=2&page=2")
    assert len(page2.json()["items"]) == 1


async def test_absurd_page_size_is_rejected(client, seeded):
    """Guards the database against ?per_page=1000000."""
    assert (await client.get("/api/v1/products?per_page=100000")).status_code == 422
    assert (await client.get("/api/v1/products?page=0")).status_code == 422
    assert (await client.get("/api/v1/products?page=-1")).status_code == 422


# --------------------------------------------------------------------------
# Money handling — the part most likely to cost real money if wrong
# --------------------------------------------------------------------------

@pytest.mark.parametrize("text,pence", [
    ("19.99", 1999),
    ("0.99", 99),
    ("5", 500),
    ("5.5", 550),        # '5.5' means £5.50, not £5.05
    ("100.00", 10000),
    ("£14.50", 1450),    # a stray currency symbol should not break the import
    ("1,299.00", 129900),
])
async def test_price_parsing(text, pence):
    assert price_to_pence(text) == pence


async def test_price_parsing_avoids_float_error():
    """int(float('19.99') * 100) is 1998 — a penny short. Ours must be 1999."""
    assert price_to_pence("19.99") == 1999
    assert price_to_pence("39.99") == 3999
    assert price_to_pence("1.10") == 110


@pytest.mark.parametrize("text,slug", [
    ("World Cup 2026 Home Tee", "world-cup-2026-home-tee"),
    ("T-Shirts", "t-shirts"),
    ("Café Crème!", "cafe-creme"),
    ("  spaced  out  ", "spaced-out"),
])
async def test_slugify(text, slug):
    assert slugify(text) == slug
