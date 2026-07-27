# Online Store

A product catalog with category filtering and a shopping cart.
FastAPI + Postgres. **Phase 1 of the plan in [`docs/PRD-STORE.md`](docs/PRD-STORE.md).**

Think of it as the food stand: a table, a menu board, and a basket. The cash
register (Stripe checkout) is Phase 2.

---

## Run it on your own machine

You need Python 3.11 or newer. **You do not need a database, a Railway account,
or a Stripe account to do this.**

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements-dev.txt

# Load the sample products
.venv/bin/python -m app.seed data/products.csv

# Start it
.venv/bin/python -m uvicorn app.main:app --reload
```

Open **http://localhost:8000**.

The first run creates `store.db`, a single file holding your catalog. That is
SQLite — a database that needs no installing. Railway uses Postgres instead;
the code is identical either way.

---

## Load your own products

1. Open **`data/products.csv`** in Excel or Google Sheets.
2. Replace the three sample rows with your own. One row per product.
3. Put your photos in **`web/images/`**, named exactly as in `image_filename`.
4. Run:

```bash
.venv/bin/python -m app.seed data/products.csv
```

Refresh the page. That's it.

| Column | What goes in it | Example |
|---|---|---|
| `name` | Product name customers see | `World Cup 2026 Home Tee` |
| `category` | The filter group. **Reuse the exact same spelling** | `T-Shirts` |
| `price_gbp` | Pounds with a decimal point | `19.99` |
| `description` | One to three sentences | `Soft cotton tee, unisex fit.` |
| `image_filename` | The photo's filename in `web/images/` | `home-tee.jpg` |
| `image_alt` | Describes the photo for blind users and Google | `Navy tee with 2026 crest` |
| `stock_qty` | How many you have. `999` for effectively unlimited | `50` |

**Re-running is safe.** Products are matched on their name, so running it again
updates what's there instead of creating duplicates. To wipe and start over, add
`--replace`.

**A product with no matching photo shows a lettered placeholder rather than a
broken image** — and the script prints a warning listing exactly which photos it
couldn't find.

---

## Run the tests

```bash
.venv/bin/pytest                      # everything (38 tests)
.venv/bin/pytest tests/test_catalog_api.py    # API only, ~1 second
.venv/bin/pytest tests/test_e2e_smoke.py      # real browser
```

The browser tests start and stop the server themselves — nothing to run by hand.

| File | Level | Covers |
|---|---|---|
| `tests/test_catalog_api.py` | 1–2 | Endpoints, filtering, pagination, hidden products, money parsing |
| `tests/test_e2e_smoke.py` | 1–2 | Page loads, no JS errors, filters, add-to-cart, totals, cart survives refresh |

To record **new** browser tests by clicking around, install the
[Playwright CRX Chrome extension](https://chromewebstore.google.com/detail/playwright-crx/jambeljnbnfbkcpnoiaedcabbgmnnlcd).
Click through the shop like a customer and it writes the Python for you.

---

## How it's put together

```
app/
  main.py            FastAPI app; serves both the API and the storefront
  core/config.py     Settings, read from environment variables
  core/database.py   Async database connection
  models/            The database tables (Category, Product)
  schemas/           What the API sends back — deliberately not the same
                     shape as the tables, so internal columns stay internal
  api/catalog.py     The endpoints
  seed.py            CSV -> database
web/index.html       The whole storefront. No build step, no npm
tests/               Levels 1 and 2
data/products.csv    Your product list
```

### Endpoints

| Method | Path | Does |
|---|---|---|
| `GET` | `/health` | Deploy health check |
| `GET` | `/api/v1/config` | Store name, currency symbol |
| `GET` | `/api/v1/categories` | The filter buttons |
| `GET` | `/api/v1/products?category=&page=&per_page=` | The catalog grid |
| `GET` | `/api/v1/products/{slug}` | One product |

---

## Three decisions worth knowing about

**Money is stored as whole pence in an integer column.** £19.99 is `1999`, never
`19.99`. A computer cannot hold `19.99` exactly — the same way you can't write ⅓
exactly as a decimal — and the error compounds across a basket until an order
totals `59.969999999999`. Whole numbers are always exact. We divide by 100 only
when printing. The CSV importer parses `"19.99"` by splitting the string rather
than multiplying a float, because `int(float("19.99") * 100)` gives `1998` —
every such product silently a penny cheap.

**`image_url` holds a complete URL, not a filename.** Today it points at this
app's own `/images` folder. In Phase 2 the `IMAGE_BASE_URL` setting changes to
your Cloudflare R2 bucket and the photos move — with no database migration.

**The cart lives in the browser** (`localStorage`), so Phase 1 needs no accounts
and no server-side cart. When checkout arrives, the browser will send only
product slugs and quantities; **the server will look up the real prices itself.**
It must never trust a price sent by a browser, or anyone could edit it to £0.01.

---

## Deploying to Railway

Not yet — that's the end of Phase 1, on request. When we do:

```bash
railway login
railway init
railway add --database postgres     # DATABASE_URL is injected automatically
railway variables --set STORE_NAME="Your Store"
railway up
railway domain                      # your public url
```

`railway.json` and the `Dockerfile` are already set up, including the
`/health` check Railway uses to confirm a deploy worked.

---

## What's next

Phase 2 is guest checkout with Stripe: orders, flat-rate shipping, a hosted
payment page, and webhook fulfilment. See [`docs/PRD-STORE.md`](docs/PRD-STORE.md).
