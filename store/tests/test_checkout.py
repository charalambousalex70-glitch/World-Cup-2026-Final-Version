"""Checkout, webhook and order-lookup tests.

Stripe is replaced with a stub, so these run with no Stripe account, no
network, and no keys. What they verify is OUR logic: that prices come from the
database, that stock is respected, that a replayed webhook cannot fulfil twice,
and that one customer cannot read another's order.
"""
import json
from datetime import datetime, timezone

import pytest
from sqlalchemy import select

from app.core.config import settings
from app.core.database import AsyncSessionLocal
from app.core.ratelimit import limiter
from app.models import Order, Product, WebhookEvent
from app.services import payments
from app.services.pricing import CartError, generate_order_number, price_cart

pytestmark = pytest.mark.asyncio


@pytest.fixture(autouse=True)
def reset_limits():
    """Without this, earlier tests' requests would trip later tests' limits."""
    limiter.reset()
    yield
    limiter.reset()


@pytest.fixture
def stripe_on(monkeypatch):
    """Pretend Stripe is configured, and capture what we would have sent it."""
    monkeypatch.setattr(settings, "STRIPE_SECRET_KEY", "sk_test_fake", raising=False)
    monkeypatch.setattr(settings, "STRIPE_WEBHOOK_SECRET", "whsec_fake", raising=False)

    sent = {}

    def fake_session(order, cart):
        sent["order_number"] = order.order_number
        sent["total_cents"] = cart.total_cents
        sent["lines"] = [(p.slug, q) for p, q in cart.lines]
        return "cs_test_" + order.order_number, "https://checkout.stripe.com/pay/fake"

    monkeypatch.setattr(payments, "create_checkout_session", fake_session)
    return sent


def completed_event(order_number, event_id="evt_1", session_id=None):
    """The shape of a real checkout.session.completed event."""
    return {
        "id": event_id,
        "type": "checkout.session.completed",
        "data": {"object": {
            "id": session_id or f"cs_test_{order_number}",
            "payment_intent": "pi_test_123",
            "metadata": {"order_number": order_number},
            "client_reference_id": order_number,
            "shipping_details": {
                "name": "Alex Customer",
                "address": {"line1": "1 High Street", "city": "London",
                            "postal_code": "SW1A 1AA", "country": "GB"},
            },
        }},
    }


@pytest.fixture
def webhook_ok(monkeypatch):
    """Accept our stub events as if Stripe had signed them."""
    monkeypatch.setattr(settings, "STRIPE_WEBHOOK_SECRET", "whsec_fake", raising=False)
    monkeypatch.setattr(payments, "verify_webhook",
                        lambda payload, sig: json.loads(payload))


async def post_webhook(client, event):
    return await client.post(
        "/api/v1/webhooks/stripe",
        content=json.dumps(event),
        headers={"stripe-signature": "t=1,v1=fake"},
    )


# --------------------------------------------------------------------------
# The rule: prices come from the database, never from the browser
# --------------------------------------------------------------------------

async def test_price_comes_from_the_database_not_the_request(client, seeded, stripe_on):
    """A tampered price must have no effect whatsoever."""
    r = await client.post("/api/v1/checkout", json={
        "email": "a@example.com",
        # A malicious extra field. The schema ignores it; the server prices
        # the cart itself.
        "items": [{"slug": "home-tee", "qty": 1, "price_cents": 1}],
    })
    assert r.status_code == 200
    # £19.99 tee + £4.99 shipping = £24.98. Not £0.01.
    assert stripe_on["total_cents"] == 1999 + settings.SHIPPING_FLAT_CENTS


async def test_totals_add_up(client, seeded, stripe_on):
    r = await client.post("/api/v1/checkout", json={
        "email": "a@example.com",
        "items": [{"slug": "home-tee", "qty": 2}, {"slug": "team-mug", "qty": 1}],
    })
    assert r.status_code == 200
    expected = (1999 * 2) + 999 + settings.SHIPPING_FLAT_CENTS
    assert stripe_on["total_cents"] == expected


async def test_duplicate_lines_are_merged(client, seeded, stripe_on):
    await client.post("/api/v1/checkout", json={
        "email": "a@example.com",
        "items": [{"slug": "team-mug", "qty": 1}, {"slug": "team-mug", "qty": 2}],
    })
    assert stripe_on["lines"] == [("team-mug", 3)]


async def test_cannot_buy_a_sold_out_product(client, seeded, stripe_on):
    r = await client.post("/api/v1/checkout", json={
        "email": "a@example.com", "items": [{"slug": "away-tee", "qty": 1}],
    })
    assert r.status_code == 400
    assert "sold out" in r.json()["detail"].lower()


async def test_cannot_buy_more_than_stock(client, seeded, stripe_on):
    r = await client.post("/api/v1/checkout", json={
        "email": "a@example.com", "items": [{"slug": "home-tee", "qty": 11}],
    })
    assert r.status_code == 400
    assert "only have 10" in r.json()["detail"]


async def test_cannot_buy_a_hidden_product(client, seeded, stripe_on):
    r = await client.post("/api/v1/checkout", json={
        "email": "a@example.com", "items": [{"slug": "secret-tee", "qty": 1}],
    })
    assert r.status_code == 400


async def test_rejects_bad_input(client, seeded, stripe_on):
    bad = [
        {"email": "a@example.com", "items": []},
        {"email": "a@example.com", "items": [{"slug": "home-tee", "qty": 0}]},
        {"email": "a@example.com", "items": [{"slug": "home-tee", "qty": -5}]},
        {"email": "a@example.com", "items": [{"slug": "home-tee", "qty": 1000}]},
        {"email": "not-an-email", "items": [{"slug": "home-tee", "qty": 1}]},
        {"items": [{"slug": "home-tee", "qty": 1}]},
    ]
    for body in bad:
        r = await client.post("/api/v1/checkout", json=body)
        assert r.status_code == 422, f"should have rejected {body}"


async def test_checkout_disabled_without_stripe_keys(client, seeded):
    """No keys means an honest 503, not a half-finished order."""
    r = await client.post("/api/v1/checkout", json={
        "email": "a@example.com", "items": [{"slug": "home-tee", "qty": 1}],
    })
    assert r.status_code == 503


async def test_order_starts_pending_not_paid(client, seeded, stripe_on):
    """Creating a payment page is not the same as being paid."""
    r = await client.post("/api/v1/checkout", json={
        "email": "a@example.com", "items": [{"slug": "home-tee", "qty": 1}],
    })
    async with AsyncSessionLocal() as db:
        order = await db.scalar(
            select(Order).where(Order.order_number == r.json()["order_number"])
        )
        assert order.status == "pending"
        assert order.paid_at is None


# --------------------------------------------------------------------------
# Webhook — the only thing that may mark an order paid
# --------------------------------------------------------------------------

async def test_webhook_marks_order_paid_and_saves_address(
    client, seeded, stripe_on, webhook_ok
):
    created = await client.post("/api/v1/checkout", json={
        "email": "a@example.com", "items": [{"slug": "home-tee", "qty": 2}],
    })
    number = created.json()["order_number"]

    r = await post_webhook(client, completed_event(number))
    assert r.status_code == 200

    async with AsyncSessionLocal() as db:
        order = await db.scalar(select(Order).where(Order.order_number == number))
        assert order.status == "paid"
        assert order.paid_at is not None
        assert order.ship_city == "London"
        assert order.ship_postal == "SW1A 1AA"


async def test_webhook_reduces_stock(client, seeded, stripe_on, webhook_ok):
    created = await client.post("/api/v1/checkout", json={
        "email": "a@example.com", "items": [{"slug": "home-tee", "qty": 3}],
    })
    await post_webhook(client, completed_event(created.json()["order_number"]))

    async with AsyncSessionLocal() as db:
        tee = await db.scalar(select(Product).where(Product.slug == "home-tee"))
        assert tee.stock_qty == 7          # was 10


async def test_replayed_webhook_does_not_fulfil_twice(
    client, seeded, stripe_on, webhook_ok
):
    """Stripe WILL resend. Stock must drop once, not once per delivery."""
    created = await client.post("/api/v1/checkout", json={
        "email": "a@example.com", "items": [{"slug": "home-tee", "qty": 3}],
    })
    number = created.json()["order_number"]
    event = completed_event(number, event_id="evt_replay")

    first = await post_webhook(client, event)
    second = await post_webhook(client, event)
    third = await post_webhook(client, event)

    assert first.json()["status"] == "ok"
    assert second.json()["status"] == "already processed"
    assert third.json()["status"] == "already processed"

    async with AsyncSessionLocal() as db:
        tee = await db.scalar(select(Product).where(Product.slug == "home-tee"))
        assert tee.stock_qty == 7          # 10 - 3, exactly once
        events = (await db.execute(select(WebhookEvent))).scalars().all()
        assert len(events) == 1


async def test_webhook_rejects_a_bad_signature(client, seeded, monkeypatch):
    monkeypatch.setattr(settings, "STRIPE_WEBHOOK_SECRET", "whsec_fake", raising=False)

    def boom(payload, sig):
        raise ValueError("bad signature")

    monkeypatch.setattr(payments, "verify_webhook", boom)

    r = await post_webhook(client, completed_event("ORD-NOPE"))
    assert r.status_code == 400


async def test_webhook_ignores_unrelated_events(client, seeded, webhook_ok):
    r = await post_webhook(client, {"id": "evt_x", "type": "invoice.paid",
                                    "data": {"object": {}}})
    assert r.status_code == 200
    assert "ignored" in r.json()


async def test_unknown_order_is_handled_quietly(client, seeded, webhook_ok):
    r = await post_webhook(client, completed_event("ORD-DOESNOTEXIST"))
    assert r.status_code == 200
    assert r.json()["status"] == "unknown order"


# --------------------------------------------------------------------------
# Order lookup — guests have no account, so this is how they check
# --------------------------------------------------------------------------

async def test_lookup_needs_the_right_number_and_email(
    client, seeded, stripe_on, webhook_ok
):
    created = await client.post("/api/v1/checkout", json={
        "email": "Buyer@Example.com", "items": [{"slug": "team-mug", "qty": 1}],
    })
    number = created.json()["order_number"]
    await post_webhook(client, completed_event(number))

    ok = await client.get(f"/api/v1/orders/lookup?order_number={number}"
                          "&email=buyer@example.com")
    assert ok.status_code == 200
    assert ok.json()["status"] == "paid"

    # Right order, wrong person.
    wrong = await client.get(f"/api/v1/orders/lookup?order_number={number}"
                             "&email=someone@else.com")
    assert wrong.status_code == 404

    # Right person, wrong order.
    missing = await client.get("/api/v1/orders/lookup?order_number=ORD-XXXXXXXX"
                               "&email=buyer@example.com")
    assert missing.status_code == 404
    # Identical wording, so probing cannot reveal which numbers exist.
    assert wrong.json()["detail"] == missing.json()["detail"]


async def test_lookup_masks_the_email(client, seeded, stripe_on):
    created = await client.post("/api/v1/checkout", json={
        "email": "buyer@example.com", "items": [{"slug": "team-mug", "qty": 1}],
    })
    number = created.json()["order_number"]
    r = await client.get(f"/api/v1/orders/lookup?order_number={number}"
                         "&email=buyer@example.com")
    assert r.json()["email"] == "bu***@example.com"


async def test_lookup_is_rate_limited(client, seeded):
    """Guessing order numbers must get slow fast."""
    codes = []
    for _ in range(14):
        r = await client.get("/api/v1/orders/lookup?order_number=ORD-GUESSING"
                             "&email=a@example.com")
        codes.append(r.status_code)
    assert 429 in codes


async def test_order_prices_are_snapshots(client, seeded, stripe_on, webhook_ok):
    """Raising a price must not rewrite an existing receipt."""
    created = await client.post("/api/v1/checkout", json={
        "email": "a@example.com", "items": [{"slug": "team-mug", "qty": 2}],
    })
    number = created.json()["order_number"]
    await post_webhook(client, completed_event(number))

    async with AsyncSessionLocal() as db:
        mug = await db.scalar(select(Product).where(Product.slug == "team-mug"))
        mug.price_cents = 1999          # price goes up
        await db.commit()

    r = await client.get(f"/api/v1/orders/lookup?order_number={number}"
                         "&email=a@example.com")
    # Still charged at the old price.
    assert r.json()["items"][0]["price_cents"] == 999
    assert r.json()["total_cents"] == 999 * 2 + settings.SHIPPING_FLAT_CENTS


# --------------------------------------------------------------------------
# Order numbers
# --------------------------------------------------------------------------

async def test_order_numbers_are_unguessable_and_unambiguous():
    numbers = {generate_order_number() for _ in range(500)}
    assert len(numbers) == 500                     # no collisions in practice
    for n in numbers:
        assert n.startswith("ORD-")
        # No 0/O or 1/I, so it survives being read aloud.
        assert not set("01IO") & set(n[4:])
