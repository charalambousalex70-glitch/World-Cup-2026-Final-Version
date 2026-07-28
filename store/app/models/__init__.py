"""SQLAlchemy ORM models — the database schema.

Phase 1 covers the catalog only: Category and Product.
Phase 2 adds Order, OrderItem and WebhookEvent (see docs/PRD-STORE.md §4.6).
Phase 4 adds User, copied from the SweepStake Live codebase.

MONEY IS STORED AS WHOLE PENCE IN AN INTEGER COLUMN.
£19.99 is stored as 1999. Never as 19.99. A float cannot represent 19.99
exactly, and the error compounds across a basket until an order totals
something like 59.969999999999. All arithmetic happens on whole numbers; we
divide by 100 only when printing. See docs/PRD-STORE.md §4.2.
"""
import uuid
from datetime import datetime, timezone

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, Integer, String, Text, Uuid
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base


def _uuid() -> uuid.UUID:
    return uuid.uuid4()


def _now() -> datetime:
    return datetime.now(timezone.utc)


class Category(Base):
    """A group of products — the filter buttons across the top of the catalog."""

    __tablename__ = "categories"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=_uuid)
    # slug is the URL-safe name: "T-Shirts" -> "t-shirts"
    slug: Mapped[str] = mapped_column(String(80), unique=True, index=True, nullable=False)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    sort_order: Mapped[int] = mapped_column(Integer, default=0)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)

    products: Mapped[list["Product"]] = relationship(back_populates="category")


class Product(Base):
    """One thing you sell."""

    __tablename__ = "products"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=_uuid)
    slug: Mapped[str] = mapped_column(String(120), unique=True, index=True, nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)

    # Whole pence. See the module docstring above.
    price_cents: Mapped[int] = mapped_column(Integer, nullable=False)
    currency: Mapped[str] = mapped_column(String(3), default="GBP")

    category_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("categories.id"))

    # A COMPLETE url, not a bare filename. In Phase 1 it points at this app's
    # own /images folder; in Phase 2 it points at Cloudflare R2. Storing the
    # whole url means swapping storage is an UPDATE, not a migration.
    image_url: Mapped[str | None] = mapped_column(String(500))
    # What a screen reader announces, and what Google reads. Not optional in
    # spirit even though the column allows null.
    image_alt: Mapped[str | None] = mapped_column(String(200))

    stock_qty: Mapped[int] = mapped_column(Integer, default=0)
    # Lets you pull a product from the shop without deleting it — deleting
    # would orphan the order history that references it.
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    sort_order: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)

    category: Mapped["Category | None"] = relationship(back_populates="products")

    __table_args__ = (
        # The catalog's main query is "active products in this category",
        # so that is the index we build.
        Index("ix_products_category_active", "category_id", "is_active"),
    )


class Order(Base):
    """One purchase.

    `user_id` is deliberately nullable: a guest checkout has no account, and
    that is the normal case. Phase 4 fills it in for logged-in customers and
    can retroactively link past guest orders by matching the email address.
    """

    __tablename__ = "orders"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=_uuid)
    # Short and human-sayable over the phone, unlike a UUID.
    order_number: Mapped[str] = mapped_column(String(20), unique=True, index=True, nullable=False)
    email: Mapped[str] = mapped_column(String(255), index=True, nullable=False)

    # pending -> paid -> shipped, or cancelled. Set only by the Stripe webhook,
    # never by the browser landing on the thank-you page.
    status: Mapped[str] = mapped_column(String(20), default="pending", index=True)

    subtotal_cents: Mapped[int] = mapped_column(Integer, nullable=False)
    shipping_cents: Mapped[int] = mapped_column(Integer, default=0)
    total_cents: Mapped[int] = mapped_column(Integer, nullable=False)
    currency: Mapped[str] = mapped_column(String(3), default="GBP")

    stripe_session_id: Mapped[str | None] = mapped_column(String(255), unique=True, index=True)
    stripe_payment_intent: Mapped[str | None] = mapped_column(String(255))

    # Captured by Stripe's own checkout page and copied here by the webhook.
    ship_name: Mapped[str | None] = mapped_column(String(200))
    ship_line1: Mapped[str | None] = mapped_column(String(200))
    ship_line2: Mapped[str | None] = mapped_column(String(200))
    ship_city: Mapped[str | None] = mapped_column(String(120))
    ship_postal: Mapped[str | None] = mapped_column(String(20))
    ship_country: Mapped[str | None] = mapped_column(String(2))

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    paid_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    items: Mapped[list["OrderItem"]] = relationship(
        back_populates="order", cascade="all, delete-orphan", lazy="selectin"
    )


class OrderItem(Base):
    """One line on an order.

    The name and price are COPIED here rather than read through the product
    relationship. If you raise a price next month, last month's receipt must
    still show what the customer actually paid. Orders are history, and history
    does not change.
    """

    __tablename__ = "order_items"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=_uuid)
    order_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("orders.id", ondelete="CASCADE"), index=True
    )
    # Kept even if the product is later deleted, hence nullable + no cascade.
    product_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("products.id"))

    name_snapshot: Mapped[str] = mapped_column(String(200), nullable=False)
    price_cents_snap: Mapped[int] = mapped_column(Integer, nullable=False)
    qty: Mapped[int] = mapped_column(Integer, nullable=False)

    order: Mapped["Order"] = relationship(back_populates="items")


class WebhookEvent(Base):
    """Every Stripe event we have already acted on.

    Stripe retries webhooks, so the same event WILL arrive more than once. The
    primary key is what stops us fulfilling an order twice: we insert this row
    in the SAME transaction as the fulfilment, so either both happen or neither
    does. Split them across two transactions and a crash in between means a
    customer gets their order shipped twice.
    """

    __tablename__ = "webhook_events"

    stripe_event_id: Mapped[str] = mapped_column(String(255), primary_key=True)
    event_type: Mapped[str] = mapped_column(String(80))
    processed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
