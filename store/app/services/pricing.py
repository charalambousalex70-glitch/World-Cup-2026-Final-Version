"""Turning a browser's cart into an order the server is willing to charge for.

THE RULE THIS FILE EXISTS TO ENFORCE: the browser sends only what a customer
wants and how many. It never sends a price. Every price here is read from our
own database. If we trusted the browser, anyone could open developer tools,
change 1999 to 1, and buy the shop for a penny.
"""
import random
import string
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models import Order, OrderItem, Product

# One basket cannot exceed this. A sanity bound, not a business rule — it stops
# a script asking for 2 billion of something and overflowing a total.
MAX_QTY_PER_LINE = 99
MAX_LINES = 50


class CartError(ValueError):
    """The cart cannot be turned into an order, and it's the caller's fault."""


@dataclass
class PricedCart:
    lines: list[tuple[Product, int]] = field(default_factory=list)
    subtotal_cents: int = 0
    shipping_cents: int = 0

    @property
    def total_cents(self) -> int:
        return self.subtotal_cents + self.shipping_cents


def generate_order_number() -> str:
    """Short, human-sayable, and not guessable in sequence.

    Sequential numbers (order 1001, 1002...) leak how many sales you have made
    and let anyone guess a neighbour's order number. Random letters avoid both.
    Ambiguous characters (0/O, 1/I) are excluded so it survives being read
    aloud over the phone.
    """
    alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
    return "ORD-" + "".join(random.choices(alphabet, k=8))


async def price_cart(db: AsyncSession, items: list[dict]) -> PricedCart:
    """Look up every product for real, and total it up.

    `items` is the untrusted browser payload: [{"slug": "...", "qty": 2}, ...]
    """
    if not items:
        raise CartError("Your cart is empty.")
    if len(items) > MAX_LINES:
        raise CartError("Too many different items in one order.")

    # Collapse duplicate slugs so ["tee", "tee"] becomes one line of two.
    wanted: dict[str, int] = {}
    for raw in items:
        slug = str(raw.get("slug", "")).strip()
        if not slug:
            raise CartError("An item in your cart is missing its product.")
        try:
            qty = int(raw.get("qty", 0))
        except (TypeError, ValueError):
            raise CartError("An item has an invalid quantity.")
        if qty < 1:
            raise CartError("Quantities must be at least 1.")
        if qty > MAX_QTY_PER_LINE:
            raise CartError(f"You can order at most {MAX_QTY_PER_LINE} of one item.")
        wanted[slug] = wanted.get(slug, 0) + qty

    for slug, qty in wanted.items():
        if qty > MAX_QTY_PER_LINE:
            raise CartError(f"You can order at most {MAX_QTY_PER_LINE} of one item.")

    # One query for every product, not one query per product.
    result = await db.execute(
        select(Product).where(Product.slug.in_(wanted.keys()), Product.is_active.is_(True))
    )
    found = {p.slug: p for p in result.scalars().all()}

    cart = PricedCart()
    for slug, qty in wanted.items():
        product = found.get(slug)
        if product is None:
            raise CartError("An item in your cart is no longer available.")
        if product.stock_qty < qty:
            raise CartError(
                f"Sorry — we only have {product.stock_qty} of '{product.name}' left."
                if product.stock_qty else f"'{product.name}' has just sold out."
            )
        # THE price. From our database, not from the request body.
        cart.subtotal_cents += product.price_cents * qty
        cart.lines.append((product, qty))

    cart.shipping_cents = settings.SHIPPING_FLAT_CENTS if cart.lines else 0
    return cart


def build_order(cart: PricedCart, email: str) -> Order:
    """Create the pending order record. Not paid until Stripe says so."""
    order = Order(
        order_number=generate_order_number(),
        email=email.strip().lower(),
        status="pending",
        subtotal_cents=cart.subtotal_cents,
        shipping_cents=cart.shipping_cents,
        total_cents=cart.total_cents,
        currency=settings.CURRENCY,
    )
    order.items = [
        OrderItem(
            product_id=product.id,
            name_snapshot=product.name,
            price_cents_snap=product.price_cents,
            qty=qty,
        )
        for product, qty in cart.lines
    ]
    return order
