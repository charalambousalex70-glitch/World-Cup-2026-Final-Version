"""Catalog endpoints — categories and products. Read-only in Phase 1.

Nothing here changes data, and nothing here needs a login. That is the whole
point of Phase 1: a shop window anyone can walk past.
"""
import math

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, select
from sqlalchemy.orm import selectinload
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.models import Category, Product
from app.schemas import CategoryOut, ProductListOut, ProductOut

router = APIRouter()

# An upper bound on page size. Without this, anyone could request
# ?per_page=1000000 and make the database do a lot of pointless work.
MAX_PER_PAGE = 60


@router.get("/categories", response_model=list[CategoryOut])
async def list_categories(db: AsyncSession = Depends(get_db)) -> list[CategoryOut]:
    """Every category that has at least been switched on, in display order."""
    result = await db.execute(
        select(Category)
        .where(Category.is_active.is_(True))
        .order_by(Category.sort_order, Category.name)
    )
    return [CategoryOut.model_validate(c) for c in result.scalars().all()]


@router.get("/products", response_model=ProductListOut)
async def list_products(
    category: str | None = Query(None, description="Category slug, e.g. 't-shirts'"),
    page: int = Query(1, ge=1),
    per_page: int = Query(24, ge=1, le=MAX_PER_PAGE),
    db: AsyncSession = Depends(get_db),
) -> ProductListOut:
    """The catalog grid. Optionally narrowed to one category.

    `page`/`per_page` are validated by FastAPI itself (ge/le above), so a
    negative or absurd page number is rejected before it reaches the database.
    """
    filters = [Product.is_active.is_(True)]

    if category:
        cat = await db.scalar(select(Category).where(Category.slug == category))
        if cat is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, f"No category '{category}'")
        filters.append(Product.category_id == cat.id)

    # Count first, so we can tell the frontend how many pages exist.
    total = await db.scalar(select(func.count()).select_from(Product).where(*filters)) or 0

    result = await db.execute(
        select(Product)
        .where(*filters)
        # selectinload fetches every product's category in ONE extra query
        # rather than one query per product (the "N+1" problem). With 100
        # products that is 2 queries instead of 101.
        .options(selectinload(Product.category))
        .order_by(Product.sort_order, Product.name)
        .offset((page - 1) * per_page)
        .limit(per_page)
    )

    return ProductListOut(
        items=[ProductOut.from_product(p) for p in result.scalars().all()],
        total=total,
        page=page,
        per_page=per_page,
        pages=max(1, math.ceil(total / per_page)),
    )


@router.get("/products/{slug}", response_model=ProductOut)
async def get_product(slug: str, db: AsyncSession = Depends(get_db)) -> ProductOut:
    """One product's detail page."""
    product = await db.scalar(
        select(Product)
        .where(Product.slug == slug, Product.is_active.is_(True))
        .options(selectinload(Product.category))
    )
    if product is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Product not found")
    return ProductOut.from_product(product)
