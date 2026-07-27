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
