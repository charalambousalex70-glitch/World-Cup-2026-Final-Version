"""Everything that talks to Stripe.

Isolated in one file so the rest of the app never imports `stripe` directly.
That keeps the checkout logic testable without a Stripe account, and means
swapping payment provider later touches this file alone.
"""
import logging

import stripe

from app.core.config import settings
from app.models import Order
from app.services.pricing import PricedCart

log = logging.getLogger("payments")


class PaymentsUnavailable(RuntimeError):
    """Stripe is not configured. Checkout must refuse, not half-work."""


def _client() -> None:
    if not settings.stripe_configured:
        raise PaymentsUnavailable(
            "Card payments are not switched on yet. Please try again later."
        )
    stripe.api_key = settings.STRIPE_SECRET_KEY


def create_checkout_session(order: Order, cart: PricedCart) -> tuple[str, str]:
    """Hand the basket to Stripe. Returns (session_id, url).

    The session id is stored on the order so the thank-you page can find it
    again from the `?session_id=` Stripe appends to the return url.

    Stripe hosts the page that collects the card and the shipping address, so
    card numbers never touch our server. That is what keeps our PCI obligations
    to the bare minimum.
    """
    _client()

    line_items = [
        {
            "price_data": {
                "currency": settings.CURRENCY.lower(),
                "product_data": {"name": product.name},
                # Stripe also works in the smallest currency unit, so our
                # stored pence go across unchanged. No conversion, no rounding.
                "unit_amount": product.price_cents,
            },
            "quantity": qty,
        }
        for product, qty in cart.lines
    ]

    if cart.shipping_cents > 0:
        # Its own line, so the customer sees what they are paying for rather
        # than a total that mysteriously exceeds the basket.
        line_items.append({
            "price_data": {
                "currency": settings.CURRENCY.lower(),
                "product_data": {"name": "Shipping"},
                "unit_amount": cart.shipping_cents,
            },
            "quantity": 1,
        })

    base = settings.PUBLIC_BASE_URL.rstrip("/")
    session = stripe.checkout.Session.create(
        mode="payment",
        line_items=line_items,
        # Guest checkout: Stripe asks for an email, no account, no password.
        customer_creation="always",
        customer_email=order.email or None,
        shipping_address_collection={"allowed_countries": settings.shipping_country_list},
        # Our own order number travels with the payment, so the webhook can
        # find the order again even if anything else goes missing.
        client_reference_id=order.order_number,
        metadata={"order_number": order.order_number},
        success_url=f"{base}/thanks?session_id={{CHECKOUT_SESSION_ID}}",
        cancel_url=f"{base}/?checkout=cancelled",
    )
    log.info("Created Stripe session for order %s", order.order_number)
    return session.id, session.url


def verify_webhook(payload: bytes, signature: str | None) -> stripe.Event:
    """Confirm an incoming webhook genuinely came from Stripe.

    `payload` MUST be the raw request body. Parsing it into JSON first and
    re-serialising changes the bytes, and the signature will never match.
    """
    if not settings.STRIPE_WEBHOOK_SECRET:
        raise PaymentsUnavailable("No webhook secret configured.")
    if not signature:
        raise ValueError("Missing Stripe signature header.")
    # Raises on a bad signature or a timestamp outside Stripe's tolerance,
    # which is what blocks replayed requests.
    return stripe.Webhook.construct_event(
        payload, signature, settings.STRIPE_WEBHOOK_SECRET
    )


def extract_shipping(session: dict) -> dict:
    """Pull the delivery address out of a completed checkout session."""
    details = (session.get("shipping_details")
               or session.get("customer_details")
               or {})
    address = details.get("address") or {}
    return {
        "ship_name": details.get("name"),
        "ship_line1": address.get("line1"),
        "ship_line2": address.get("line2"),
        "ship_city": address.get("city"),
        "ship_postal": address.get("postal_code"),
        "ship_country": address.get("country"),
    }
