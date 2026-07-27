"""Pydantic models — the shape of what the API sends back.

These are deliberately separate from the ORM models. The ORM model is what the
database holds; the schema is what the outside world sees. Keeping them apart
means we can add an internal column (a cost price, say) without accidentally
publishing it to every visitor.
"""
from pydantic import BaseModel, ConfigDict


class CategoryOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    slug: str
    name: str


class ProductOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    slug: str
    name: str
    description: str | None = None
    price_cents: int
    currency: str
    image_url: str | None = None
    image_alt: str | None = None
    in_stock: bool
    category_slug: str | None = None
    category_name: str | None = None

    @classmethod
    def from_product(cls, p) -> "ProductOut":
        """Build the public view of a product.

        Note what is NOT exposed: the database id and the exact stock count.
        The storefront only needs to know whether a thing is buyable, and
        publishing precise inventory numbers tells competitors more than it
        tells customers.
        """
        return cls(
            slug=p.slug,
            name=p.name,
            description=p.description,
            price_cents=p.price_cents,
            currency=p.currency,
            image_url=p.image_url,
            image_alt=p.image_alt,
            in_stock=p.stock_qty > 0,
            category_slug=p.category.slug if p.category else None,
            category_name=p.category.name if p.category else None,
        )


class ProductListOut(BaseModel):
    """A page of products, plus enough information to render 'Next'."""

    items: list[ProductOut]
    total: int
    page: int
    per_page: int
    pages: int
