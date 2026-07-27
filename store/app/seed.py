"""Load products from a CSV file into the database.

Run it:      python -m app.seed data/products.csv
Start over:  python -m app.seed data/products.csv --replace

This is how your product list gets into the shop. Fill in the spreadsheet, run
this, refresh the page. Run it as many times as you like — matching on the slug
means re-running UPDATES existing products rather than creating duplicates.
"""
import argparse
import asyncio
import csv
import re
import sys
import unicodedata
from pathlib import Path

from sqlalchemy import delete, select

from app.core.config import settings
from app.core.database import AsyncSessionLocal, Base, engine
from app.models import Category, Product

REQUIRED_COLUMNS = {"name", "category", "price_gbp", "image_filename", "stock_qty"}


def slugify(value: str) -> str:
    """'World Cup 2026 Home Tee!' -> 'world-cup-2026-home-tee'

    Slugs are what appear in the address bar, so they must be lowercase, use
    hyphens instead of spaces, and contain no punctuation or accents.
    """
    value = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode()
    value = re.sub(r"[^\w\s-]", "", value).strip().lower()
    return re.sub(r"[-\s]+", "-", value) or "item"


def price_to_pence(raw: str) -> int:
    """'19.99' -> 1999.

    We deliberately do NOT do int(float(raw) * 100): float arithmetic would
    turn 19.99 into 19.989999999999998, and int() would then floor it to 1998 —
    every such product silently a penny cheap. Splitting the string on the
    decimal point is exact.
    """
    raw = raw.strip().replace(",", "").lstrip("£$€")
    if not raw:
        raise ValueError("price is empty")
    if "." not in raw:
        return int(raw) * 100
    pounds, _, pence = raw.partition(".")
    pence = (pence + "00")[:2]          # '5' -> '50', '999' -> '99'
    return int(pounds or 0) * 100 + int(pence)


async def seed(csv_path: Path, replace: bool = False) -> None:
    if not csv_path.exists():
        sys.exit(f"❌ No such file: {csv_path}")

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    with csv_path.open(newline="", encoding="utf-8-sig") as fh:
        rows = list(csv.DictReader(fh))

    if not rows:
        sys.exit("❌ The CSV has no rows.")

    missing = REQUIRED_COLUMNS - set(rows[0].keys())
    if missing:
        sys.exit(f"❌ The CSV is missing these columns: {', '.join(sorted(missing))}")

    created = updated = 0

    async with AsyncSessionLocal() as db:
        if replace:
            await db.execute(delete(Product))
            await db.execute(delete(Category))
            await db.commit()
            print("🗑️  Cleared existing catalog (--replace)")

        # Categories are created on demand, in the order they first appear in
        # the spreadsheet — so the filter buttons follow your sheet's order.
        cat_cache: dict[str, Category] = {}

        for line_no, row in enumerate(rows, start=2):  # start=2: row 1 is headers
            name = (row.get("name") or "").strip()
            if not name:
                print(f"⚠️  Row {line_no}: no name, skipped")
                continue

            try:
                price_cents = price_to_pence(row["price_gbp"])
            except (ValueError, KeyError) as exc:
                print(f"⚠️  Row {line_no} ({name}): bad price — {exc}. Skipped")
                continue

            if price_cents <= 0:
                print(f"⚠️  Row {line_no} ({name}): price must be above zero. Skipped")
                continue

            # --- category ---
            cat_name = (row.get("category") or "").strip() or "Other"
            cat_slug = slugify(cat_name)
            if cat_slug not in cat_cache:
                cat = await db.scalar(select(Category).where(Category.slug == cat_slug))
                if cat is None:
                    cat = Category(slug=cat_slug, name=cat_name, sort_order=len(cat_cache))
                    db.add(cat)
                    await db.flush()
                cat_cache[cat_slug] = cat
            category = cat_cache[cat_slug]

            # --- image url ---
            # Stored as a COMPLETE url. Today IMAGE_BASE_URL is '/images';
            # in Phase 2 it becomes the Cloudflare R2 bucket url and this same
            # script writes R2 urls instead. No schema change either way.
            filename = (row.get("image_filename") or "").strip()
            image_url = f"{settings.IMAGE_BASE_URL.rstrip('/')}/{filename}" if filename else None

            try:
                stock = int((row.get("stock_qty") or "0").strip() or 0)
            except ValueError:
                stock = 0

            slug = slugify(name)
            existing = await db.scalar(select(Product).where(Product.slug == slug))

            if existing:
                existing.name = name
                existing.description = (row.get("description") or "").strip() or None
                existing.price_cents = price_cents
                existing.currency = settings.CURRENCY
                existing.category_id = category.id
                existing.image_url = image_url
                existing.image_alt = (row.get("image_alt") or "").strip() or name
                existing.stock_qty = stock
                existing.sort_order = line_no
                updated += 1
            else:
                db.add(Product(
                    slug=slug,
                    name=name,
                    description=(row.get("description") or "").strip() or None,
                    price_cents=price_cents,
                    currency=settings.CURRENCY,
                    category_id=category.id,
                    image_url=image_url,
                    image_alt=(row.get("image_alt") or "").strip() or name,
                    stock_qty=stock,
                    sort_order=line_no,
                ))
                created += 1

        await db.commit()

    print(f"\n✅ Done. {created} added, {updated} updated, "
          f"{len(cat_cache)} categories.")

    # Warn about photos that were promised but are not on disk. Better to find
    # out here than to discover broken images on the live site.
    images_dir = Path(__file__).resolve().parent.parent / "web" / "images"
    if settings.IMAGE_BASE_URL.startswith("/"):
        wanted = {(r.get("image_filename") or "").strip() for r in rows}
        wanted.discard("")
        absent = sorted(f for f in wanted if not (images_dir / f).exists())
        if absent:
            print(f"\n⚠️  {len(absent)} photo(s) named in the CSV are not in "
                  f"web/images/ — those products will show a placeholder:")
            for f in absent[:10]:
                print(f"     • {f}")
            if len(absent) > 10:
                print(f"     … and {len(absent) - 10} more")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Load products from a CSV file.")
    parser.add_argument("csv", type=Path, nargs="?", default=Path("data/products.csv"))
    parser.add_argument("--replace", action="store_true",
                        help="Delete the existing catalog first. Destructive.")
    args = parser.parse_args()
    asyncio.run(seed(args.csv, args.replace))
