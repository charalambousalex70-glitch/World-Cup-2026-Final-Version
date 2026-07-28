# Online Store

A complete, deployable online shop. FastAPI + Postgres + Stripe.

**What works today:**

- Product catalog with category filtering and product detail pages
- Shopping cart that survives a refresh
- **Guest checkout** via Stripe — customers buy with no account, just an email
- Flat-rate shipping, collected as its own line
- Orders recorded, stock reduced automatically when payment confirms
- "Track my order" page for customers with no account
- Works on phones, light and dark themes

**Not built yet:** customer accounts and order history (see
[`docs/PRD-STORE.md`](docs/PRD-STORE.md)), email receipts, and an admin screen —
today you manage products through the CSV file.

👉 **To put it online, follow [`docs/DEPLOY.md`](docs/DEPLOY.md).**

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
| `tests/test_checkout.py` | 2–3 | Server-side pricing, stock limits, webhook fulfilment, replay protection, order lookup |
| `tests/test_e2e_smoke.py` | 1–2 | Page loads, no JS errors, filters, add-to-cart, totals, cart survives refresh, order tracking |

**The checkout tests need no Stripe account** — Stripe is stubbed, so what's
being tested is our own logic: that prices come from the database, that a
replayed webhook can't ship an order twice, and that one customer can't read
another's order.

To record **new** browser tests by clicking around, install the
[Playwright CRX Chrome extension](https://chromewebstore.google.com/detail/playwright-crx/jambeljnbnfbkcpnoiaedcabbgmnnlcd).
Click through the shop like a customer and it writes the Python for you.

---

## How it's put together

```
app/
  main.py              FastAPI app; serves both the API and the storefront
  core/config.py       Settings, read from environment variables
  core/database.py     Async database connection
  core/ratelimit.py    Stops people hammering checkout or guessing order numbers
  models/              The database tables
  schemas/             What the API sends back — deliberately not the same
                       shape as the tables, so internal columns stay internal
  api/catalog.py       Browsing endpoints
  api/checkout.py      Checkout, the Stripe webhook, order lookup
  services/pricing.py  Turns a cart into an order the server will charge for
  services/payments.py Everything that talks to Stripe, isolated in one file
  seed.py              CSV -> database
web/index.html         The shop. No build step, no npm
web/thanks.html        Where Stripe returns customers after paying
web/orders.html        "Where is my order?"
tests/                 63 tests
data/products.csv      Your product list
```

### Endpoints

| Method | Path | Does |
|---|---|---|
| `GET` | `/health` | Deploy health check |
| `GET` | `/api/v1/config` | Store name, currency, shipping, is checkout on |
| `GET` | `/api/v1/categories` | The filter buttons |
| `GET` | `/api/v1/products?category=&page=&per_page=` | The catalog grid |
| `GET` | `/api/v1/products/{slug}` | One product |
| `POST` | `/api/v1/checkout` | Cart → pending order → Stripe payment page |
| `POST` | `/api/v1/webhooks/stripe` | Stripe confirms payment. The **only** thing that marks an order paid |
| `GET` | `/api/v1/orders/lookup?order_number=&email=` | Guest order tracking |
| `GET` | `/api/v1/orders/by-session/{id}` | Powers the thank-you page |

---

## How a purchase actually works

```
Customer clicks "Go to payment"
  │
  ├─▶ Browser sends ONLY {slug, qty} and an email. No prices.
  │
  ├─▶ Server looks up the real prices, checks stock,
  │   creates an order marked PENDING
  │
  ├─▶ Customer is sent to Stripe's own page and types their card
  │   and address THERE. Card details never touch our server.
  │
  ├─▶ Stripe charges the card
  │
  ├─▶ ⚡ Stripe sends US a signed message: "this one is paid"
  │      → order marked PAID, stock reduced
  │
  └─▶ Customer lands back on our thank-you page
```

**The thank-you page never marks anything paid.** Anyone can visit a url, so a
page load proves nothing. Only the signed message from Stripe is evidence that
money moved.

---

## Four decisions worth knowing about

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

**The cart lives in the browser** (`localStorage`), so there's no account and no
server-side cart to manage. At checkout the browser sends only product slugs and
quantities — **the server looks up every price itself.** It must never trust a
price sent by a browser, or anyone could edit £19.99 to £0.01 in developer tools
and buy the shop for pennies. There's a test that specifically tries this.

**Stripe webhooks are made idempotent by a database constraint.** Stripe retries
delivery, so the same "payment succeeded" message *will* arrive more than once.
Each event id is recorded in a `webhook_events` table **in the same transaction
as the fulfilment**, so a repeat delivery finds the row and stops. If those were
two separate transactions, a crash in between would ship someone's order twice.

---

## Deploying

**See [`docs/DEPLOY.md`](docs/DEPLOY.md)** for the full walkthrough. The short
version:

```bash
railway login
railway init
railway add --database postgres     # DATABASE_URL is injected automatically
railway variables --set 'STORE_NAME=Your Store'
railway up
railway domain                      # your public url
railway run python -m app.seed data/products.csv
```

Then add your Stripe keys and webhook. `railway.json` and the `Dockerfile` are
already set up, including the `/health` check Railway uses to confirm a deploy
worked.

**The app refuses to half-work.** With no Stripe keys, browsing works fine and
the checkout button says plainly that payments aren't switched on — rather than
looking clickable and failing.

---

## What's next

Optional customer accounts and order history, email receipts, and an admin
screen for managing products without the CSV. See
[`docs/PRD-STORE.md`](docs/PRD-STORE.md).
