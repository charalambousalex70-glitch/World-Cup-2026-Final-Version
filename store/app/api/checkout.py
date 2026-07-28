"""Checkout, order lookup, and the Stripe webhook."""
import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.database import get_db
from app.core.ratelimit import limiter
from app.models import Order, Product, WebhookEvent
from app.services import payments
from app.services.pricing import CartError, build_order, price_cart

log = logging.getLogger("checkout")
router = APIRouter()


class CartLine(BaseModel):
    slug: str = Field(min_length=1, max_length=120)
    qty: int = Field(ge=1, le=99)


class CheckoutIn(BaseModel):
    items: list[CartLine] = Field(min_length=1, max_length=50)
    email: EmailStr


class OrderItemOut(BaseModel):
    name: str
    qty: int
    price_cents: int
    line_total_cents: int


class OrderOut(BaseModel):
    order_number: str
    status: str
    email: str
    subtotal_cents: int
    shipping_cents: int
    total_cents: int
    currency: str
    items: list[OrderItemOut]
    ship_name: str | None = None
    ship_city: str | None = None
    ship_postal: str | None = None

    @classmethod
    def of(cls, order: Order) -> "OrderOut":
        return cls(
            order_number=order.order_number,
            status=order.status,
            # Partly hidden: enough for a customer to recognise their own
            # order, not enough to harvest addresses if a number is guessed.
            email=_mask_email(order.email),
            subtotal_cents=order.subtotal_cents,
            shipping_cents=order.shipping_cents,
            total_cents=order.total_cents,
            currency=order.currency,
            items=[
                OrderItemOut(
                    name=i.name_snapshot,
                    qty=i.qty,
                    price_cents=i.price_cents_snap,
                    line_total_cents=i.price_cents_snap * i.qty,
                )
                for i in order.items
            ],
            ship_name=order.ship_name,
            ship_city=order.ship_city,
            ship_postal=order.ship_postal,
        )


def _mask_email(email: str) -> str:
    name, _, domain = email.partition("@")
    if not domain:
        return "***"
    keep = name[:2] if len(name) > 2 else name[:1]
    return f"{keep}{'*' * max(3, len(name) - len(keep))}@{domain}"


@router.get("/checkout/status")
async def checkout_status() -> dict:
    """Lets the storefront show a working or a disabled checkout button."""
    return {
        "available": settings.stripe_configured,
        "shipping_cents": settings.SHIPPING_FLAT_CENTS,
        "shipping_countries": settings.shipping_country_list,
    }


@router.post("/checkout")
async def create_checkout(
    body: CheckoutIn,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Turn a cart into a pending order and a Stripe payment page.

    Note what is NOT in the request body: prices. See services/pricing.py.
    """
    limiter.check(f"checkout:{request.client.host if request.client else 'unknown'}",
                  limit=10, window_seconds=60)

    if not settings.stripe_configured:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "Card payments are not switched on yet.",
        )

    try:
        cart = await price_cart(db, [line.model_dump() for line in body.items])
    except CartError as exc:
        # A real, user-fixable problem ("only 2 left"), so say so plainly.
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc))

    order = build_order(cart, body.email)
    db.add(order)
    await db.flush()

    try:
        session_id, url = payments.create_checkout_session(order, cart)
    except payments.PaymentsUnavailable as exc:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, str(exc))
    except Exception:
        # Never surface a payment provider's internals to a shopper.
        log.exception("Stripe rejected the session for %s", order.order_number)
        raise HTTPException(
            status.HTTP_502_BAD_GATEWAY,
            "We couldn't reach the payment provider. Please try again.",
        )

    order.stripe_session_id = session_id
    await db.commit()
    return {"url": url, "order_number": order.order_number}


@router.get("/orders/by-session/{session_id}", response_model=OrderOut)
async def order_by_session(
    session_id: str,
    db: AsyncSession = Depends(get_db),
) -> OrderOut:
    """Powers the thank-you page.

    Safe to expose without the email check that /orders/lookup requires: the
    session id is a long random string Stripe only ever gave to this one
    customer, so possessing it is itself the proof.
    """
    order = await db.scalar(select(Order).where(Order.stripe_session_id == session_id))
    if order is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Order not found.")
    return OrderOut.of(order)


@router.get("/orders/lookup", response_model=OrderOut)
async def lookup_order(
    order_number: str,
    email: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> OrderOut:
    """'Where is my order?' for guests, who have no account to log in to.

    BOTH the order number and the email must match. Requiring two facts is
    what stops someone reading other people's orders by trying numbers, and
    the rate limit is what stops them trying quickly.
    """
    limiter.check(f"lookup:{request.client.host if request.client else 'unknown'}",
                  limit=10, window_seconds=60)

    order = await db.scalar(
        select(Order).where(
            Order.order_number == order_number.strip().upper(),
            Order.email == email.strip().lower(),
        )
    )
    if order is None:
        # Deliberately identical whether the number or the email was wrong —
        # a different message for each would confirm which numbers exist.
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            "We couldn't find an order with that number and email address.",
        )
    return OrderOut.of(order)


@router.post("/webhooks/stripe", include_in_schema=False)
async def stripe_webhook(request: Request, db: AsyncSession = Depends(get_db)) -> dict:
    """Stripe tells us a payment succeeded. THE ONLY place an order is marked paid.

    The thank-you page must never do this: anyone can visit a url. Only a
    signed message from Stripe is evidence that money moved.
    """
    # Raw bytes. Not request.json(). See services/payments.verify_webhook.
    payload = await request.body()
    signature = request.headers.get("stripe-signature")

    try:
        event = payments.verify_webhook(payload, signature)
    except payments.PaymentsUnavailable:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "Not configured.")
    except Exception as exc:
        log.warning("Rejected a webhook: %s", exc)
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Invalid signature.")

    if event["type"] != "checkout.session.completed":
        return {"ignored": event["type"]}

    session = event["data"]["object"]
    order_number = (session.get("metadata") or {}).get("order_number") \
        or session.get("client_reference_id")
    if not order_number:
        log.error("Completed session %s carried no order number", session.get("id"))
        return {"status": "no order reference"}

    order = await db.scalar(select(Order).where(Order.order_number == order_number))
    if order is None:
        log.error("Webhook referenced unknown order %s", order_number)
        return {"status": "unknown order"}

    # --- idempotency -------------------------------------------------------
    # Stripe retries. This insert and the fulfilment below share one
    # transaction, so a replay either finds the row and stops, or the whole
    # thing rolls back together. There is no window where we have shipped but
    # not recorded it.
    db.add(WebhookEvent(stripe_event_id=event["id"], event_type=event["type"]))
    try:
        await db.flush()
    except Exception:
        await db.rollback()
        log.info("Ignored a repeat delivery of event %s", event["id"])
        return {"status": "already processed"}

    if order.status == "paid":
        await db.commit()
        return {"status": "already paid"}

    order.status = "paid"
    order.paid_at = datetime.now(timezone.utc)
    order.stripe_session_id = session.get("id")
    order.stripe_payment_intent = session.get("payment_intent")
    for field_name, value in payments.extract_shipping(session).items():
        if value:
            setattr(order, field_name, value)

    # Reduce stock. The `stock_qty >= qty` condition means two simultaneous
    # orders for the last item cannot both succeed — the second matches no
    # rows rather than driving stock negative.
    for item in order.items:
        if item.product_id:
            result = await db.execute(
                update(Product)
                .where(Product.id == item.product_id, Product.stock_qty >= item.qty)
                .values(stock_qty=Product.stock_qty - item.qty)
            )
            if result.rowcount == 0:
                # Paid but unfulfillable. Never silently swallow this: the
                # customer's money has moved and a human has to intervene.
                log.error(
                    "OVERSOLD: order %s paid for %d x %s but stock was short",
                    order.order_number, item.qty, item.name_snapshot,
                )

    await db.commit()
    log.info("Order %s paid", order.order_number)
    return {"status": "ok"}
