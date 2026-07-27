# PRD — Online Store (Catalog → Cart → Checkout → Accounts)

**Author:** Claude · **Date:** 2026-07-27 · **Status:** Draft for approval
**Branch:** `claude/ecommerce-prd-roadmap-wihhsp`

---

## 0. Read this first (plain English)

This document is the **plan**, not the code. It says what we are building, what
we are going to reuse instead of writing from scratch, and the order we build it
in so you get something working **today**.

### The restaurant analogy we'll use all the way through

You are not opening a 200-seat restaurant on day one. Nobody does. Here is how
we grow:

| Stage | Restaurant | Our app | When |
|---|---|---|---|
| **Phase 0** | Buy the ingredients, label the shelves | Database tables for products + your real product data loaded in | Today, morning |
| **Phase 1** | **The food stand.** A table, a menu board, a basket | **Catalog page + category filter + add-to-cart.** Nothing is paid for yet | **Today — this is the quick win** |
| **Phase 2** | Put a **cash register** on the stand | Guest checkout with Stripe. Real money. No account needed | Day 2–3 |
| **Phase 3** | Hire **one employee** to handle receipts and orders | Order confirmation emails, "look up my order", simple admin screen | Week 1–2 |
| **Phase 4** | **Front of house** — regulars get a table and a tab | Optional customer accounts + order history | Week 2–3 |
| **Phase 5** | **Full front-of-house and back-of-house** | Stock control, search, discount codes, analytics | Month 2+ |

The rule: **each phase must be fully working and deployed before we start the
next one.** A food stand that works beats a half-built restaurant every time.

---

## 1. What we are building

A store that sells **20–100 physical products**.

| # | Feature | Plain English | Phase |
|---|---|---|---|
| F1 | Product catalog | A page showing all your products with photo, name, price | 1 |
| F2 | Category filtering | Buttons like "All / Shirts / Mugs" that narrow the list | 1 |
| F3 | Product detail | Click a product, see the big photo and full description | 1 |
| F4 | Shopping cart | A basket you can add to, change quantities in, and remove from | 1 |
| F5 | Guest checkout | Buy with **no account**. Just email + shipping address | 2 |
| F6 | Payment | Real card payment via Stripe's own hosted page | 2 |
| F7 | Order confirmation | Email receipt + an order number | 3 |
| F8 | Order lookup | "Where's my order?" using order number + email | 3 |
| F9 | Admin | You add/edit products and see orders without touching code | 3 |
| F10 | Optional accounts | Sign up **if you want**, to see past orders | 4 |
| F11 | Order history | Logged-in customers see everything they ever bought | 4 |

**Explicitly NOT in scope** (this is what keeps it a quick win): subscriptions,
product variants/sizes, multi-currency, reviews, wishlists, marketplace/multi-
vendor, tax calculation engines, international shipping rate tables, live chat.
We can add any of these later — they are Phase 5+.

---

## 2. ✅ Repo decision — RESOLVED

**This repository currently contains a different app.** It is
`SweepStake Live` — a World Cup 2026 football sweepstake platform (draws,
leaderboards, live scores over WebSockets). An online store is a completely
different product.

**Decision (2026-07-27): the store gets a BRAND NEW REPO.**

The sweepstake app stays exactly as it is, untouched and still deployable. The
store starts clean. In restaurant terms: you're not converting the football pub
into a shop — you're opening a new unit, and taking the good kitchen equipment
with you.

**What "taking the equipment" means concretely.** We copy these proven files out
of this repo into the new one on day one, so the new store is *not* starting
from nothing:

```
NEW REPO                              COPIED FROM (this repo)
backend/app/core/database.py     ←──  backend/app/core/database.py   (as-is)
backend/app/core/config.py       ←──  backend/app/core/config.py     (trim football keys, add Stripe/R2)
backend/app/core/security.py     ←──  backend/app/core/security.py   (as-is)
backend/app/api/deps.py          ←──  backend/app/api/deps.py        (as-is)
backend/app/models/__init__.py   ←──  the `User` class only
backend/app/api/auth.py          ←──  backend/app/api/auth.py        (Phase 4)
backend/app/services/email.py    ←──  backend/app/services/email.py  (Phase 3)
backend/Dockerfile               ←──  backend/Dockerfile             (as-is)
docker-compose.yml               ←──  docker-compose.yml             (rename db)
```

Everything football-related — draws, fixtures, scoring, live polling,
WebSockets — is **left behind**. Cost of this choice: roughly 20 extra minutes
today. Benefit: no dead code, no confusing mixed-purpose repo, and this repo
keeps working.

**This PRD is otherwise unchanged by the decision** — the store design is
identical wherever the files live.

### The equipment we're taking

I read the existing code, and **the foundation is genuinely reusable**. This is
the single biggest reason we can get a win today — roughly a day of setup work
is already done and already deployed-tested:

| Existing file | What it does | Reuse for store? |
|---|---|---|
| `backend/app/core/database.py` | Async Postgres connection + pooling, already tuned for cloud cold-starts | ✅ **As-is** |
| `backend/app/core/config.py` | Reads settings from environment variables, converts Railway/Render's `postgres://` URL to the async format, refuses to boot with a missing secret | ✅ **As-is**, add Stripe/R2 keys |
| `backend/app/core/security.py` | Password hashing (bcrypt) + JWT login tokens. Already has the tricky bcrypt-72-byte bug fixed | ✅ **As-is** for Phase 4 |
| `backend/app/api/deps.py` | "Who is the logged-in user?" check | ✅ **As-is** for Phase 4 |
| `backend/app/models/__init__.py` → `User` | User table with email, password hash, reset codes | ✅ **Reuse the `User` model** |
| `backend/app/api/auth.py` | Register / login / password reset endpoints | ✅ **Reuse for Phase 4** |
| `backend/app/services/email.py` | Email sending with a safe dry-run mode when unconfigured | ✅ **Reuse for Phase 3** receipts |
| `backend/Dockerfile`, `docker-compose.yml` | Local dev + container build | ✅ **As-is** |
| `frontend/public/index.html` | Zero-build single-page app pattern — no compile step | ✅ **Reuse the pattern** |
| Everything football/sweepstake/WebSocket | Draws, fixtures, scoring, live polling | ❌ Not needed |

**Translation:** we already own the kitchen, the plumbing, and the electricity.
We're building a new menu, not a new building.

---

## 3. Research findings — what we reuse instead of building

I searched for existing repos, libraries, CLIs, APIs and MCP servers. Here is
everything worth using, and — just as importantly — what I checked and rejected.

### 3.1 The big architecture decision: don't adopt a commerce platform

I evaluated the three leading open-source commerce engines:

| Platform | Language | Verdict for us |
|---|---|---|
| [Medusa](https://medusajs.com) | Node/TypeScript | Powerful and modular, ~31k GitHub stars. **Rejected:** different language from your existing Python backend, and standing it up is days of config before you see a product on screen. |
| [Saleor](https://saleor.io) | Python/Django + GraphQL | Python at least, but GraphQL-first, multi-warehouse, enterprise-shaped. **Rejected:** huge surface area for 20–100 products. |
| [Vendure](https://vendure.io) | TypeScript/NestJS | Clean and strongly typed, but **open-core (GPLv3)** with paid enterprise tier. **Rejected:** language mismatch + licensing complexity. |

**Decision: build on the FastAPI foundation already in this repo.**

Why, in plain English: those three platforms are *pre-built restaurants*. They
come with a walk-in freezer, a sommelier station, and a 40-page manual. You want
a food stand today. Adopting one of them means spending your first week learning
their way of doing things instead of selling anything. A product catalog and a
cart are genuinely simple — maybe 400 lines of code — and we already have the
hard parts (database, auth, deployment) working.

We can revisit this at Phase 5 if you ever need multi-warehouse or multi-brand.

### 3.2 Code we will actually read and borrow from

| Repo | License | What we take |
|---|---|---|
| [aliseyedi01/FastAPI-Ecommerce-API](https://github.com/aliseyedi01/FastAPI-Ecommerce-API) | **MIT** ✅ | Reference for product/category/cart endpoint shapes and route naming. Read for ideas, don't wholesale-copy. |
| [zamaniamin/fastapi-shop](https://github.com/zamaniamin/fastapi-shop) | Open source | Reference for product schema design with SQLAlchemy + Pydantic. |
| [benavlabs/FastAPI-boilerplate](https://github.com/benavlabs/FastAPI-boilerplate) | Open source | Reference for async SQLAlchemy 2.0 patterns and project layout. |
| **This repo's `backend/app/core/`** | Ours | The real workhorse — see §2 table above. |

**Rejected:** [app-generator/ecommerce-fastapi-stripe](https://github.com/app-generator/ecommerce-fastapi-stripe).
It looked perfect from the search results, but I checked the LICENSE file: it
says *"Product is available for subscribers ONLY."* **That is not open source —
using it would be a licensing problem.** It also stores products in JSON files
with no database, which we don't want. This is exactly why we check licenses
before copying code.

### 3.3 Python libraries (the ready-made ingredients)

| Library | What it does for us | Phase |
|---|---|---|
| `fastapi`, `sqlalchemy`, `asyncpg`, `pydantic` | Already in `requirements.txt` ✅ | 1 |
| `alembic` | Already in `requirements.txt` ✅ — database migrations (safe schema changes) | 1 |
| **`stripe`** (official Python SDK) | Creating checkout sessions, verifying webhooks | 2 |
| **`boto3`** | Talking to Cloudflare R2 — R2 is S3-compatible, so the standard AWS library works | 2 |
| `jinja2` | HTML templating if we want server-rendered pages for SEO | 3 |
| `pytest`, `pytest-asyncio`, `httpx` | Backend tests. `httpx.AsyncClient` + `ASGITransport` tests the API in-process with no network | 1 |
| `pytest-playwright` | Browser tests driven from Python | 1 |
| `testcontainers[postgres]` | Spins up a real throwaway Postgres for tests | 3 |

### 3.4 APIs and hosted services

| Service | Why | Cost |
|---|---|---|
| **Stripe Checkout** (hosted) | Stripe hosts the payment page. Card numbers never touch our server — that removes almost all our PCI compliance burden. Guest-friendly by design | ~2.9% + 30¢ per sale |
| **Railway** | Hosts the API + Postgres together, one dashboard | ~$5/mo hobby |
| **Cloudflare R2** | Product photos. **Zero egress fees** — you never get a surprise bandwidth bill | $0.015/GB/mo, $0 egress |

### 3.5 CLIs

| CLI | Commands we'll use |
|---|---|
| **Railway CLI** | `railway login` → `railway init` → `railway add --database postgres` → `railway variables` → `railway up` → `railway domain` → `railway logs` |
| **Stripe CLI** | `stripe listen --forward-to localhost:8000/api/v1/webhooks/stripe` — this is the magic that lets us test real payment webhooks on your laptop |
| **git** | Version control (already set up) |

### 3.6 MCP servers (letting me operate tools directly)

MCP = a way for me to use a service directly instead of telling you to click
around a dashboard.

| MCP | What it unlocks | Phase |
|---|---|---|
| **Playwright MCP** | I drive a real browser to test your store and see what you'd see | 1 |
| **Stripe MCP** (`https://mcp.stripe.com`, official) | I can create products/prices and inspect payments in your Stripe account | 2 |
| **Railway MCP** | I can deploy, set env vars, read logs, manage domains | 2 |
| **Cloudflare MCP** | I can manage R2 buckets directly | 2 |
| **GitHub MCP** | Already active ✅ — I commit and push for you | 0 |

**Note on SSH:** you asked about SSH. We don't need it. Railway deploys over
HTTPS from git, and Railway's own CLI gives us log access. Adding SSH would be
extra keys to manage for zero benefit. If we ever need a shell on the server,
`railway run` and `railway logs` cover it.

### 3.7 The four frameworks you named

I checked all four. Here's the honest read:

| Framework | Status | Verdict |
|---|---|---|
| [obra/superpowers](https://github.com/obra/superpowers) | ✅ Active | **Use it.** Enforces brainstorm → plan → test-driven implementation → review. Its "bite-sized tasks" discipline is exactly right for phases. Install: `/plugin install superpowers@claude-plugins-official` |
| [gsd-build/get-shit-done](https://github.com/gsd-build/get-shit-done) | ⚠️ **ARCHIVED** | **Don't use this URL.** The project moved to [open-gsd/gsd-core](https://github.com/open-gsd/gsd-core). Install the new one: `npx @opengsd/gsd-core@latest`, then `/gsd-new-project`. Its Discuss → Plan → Execute → Verify → Ship loop maps 1:1 onto our phases. |
| [dsifry/metaswarm](https://github.com/dsifry/metaswarm) | ✅ Active | **Hold until Phase 3.** 18 agents and a 9-phase workflow with adversarial review. Genuinely powerful, but it is a full brigade — overkill for a food stand, excellent once we have a real kitchen. |
| [aiagentskit/claude-agents-library](https://github.com/aiagentskit/claude-agents-library) | ✅ Active, MIT | **Use it selectively.** 34 agent personas. Copy just 4 into `.claude/agents/`: `backend-architect`, `frontend-developer`, `api-tester`, plus a security reviewer. |

**My recommendation, stated plainly:** for Phase 1 today, use **superpowers**
plus **2–3 agents** from the library. Adding all four frameworks at once means
spending today configuring tooling instead of shipping a catalog page. We layer
in **gsd-core** at Phase 2 and **metaswarm** at Phase 3, when the extra
structure starts paying for itself.

That is the same restaurant logic: you don't hire a head chef, a sous chef, and
a maître d' for a food stand.

---

## 4. The technical design

### 4.1 Architecture

```
        ┌────────────────────────────────┐
        │  Browser (your customer)        │
        │  catalog · filters · cart       │
        └──────────────┬─────────────────┘
                       │ HTTPS (JSON)
        ┌──────────────▼─────────────────┐        ┌──────────────────┐
        │  FastAPI on Railway             │───────▶│ Stripe Checkout  │
        │  /api/v1/products               │◀───────│ (hosted page)    │
        │  /api/v1/checkout               │webhook └──────────────────┘
        │  /api/v1/orders                 │
        └──────────────┬─────────────────┘
                       │ asyncpg
        ┌──────────────▼─────────────────┐        ┌──────────────────┐
        │  Postgres on Railway            │        │ Cloudflare R2    │
        │  products · orders · users      │        │ product photos   │
        └────────────────────────────────┘        └──────────────────┘
```

### 4.2 Catalog schema — **this is what you're waiting for**

You said your product names, prices and photos are ready. **This is the schema.**
Section 4.3 tells you the exact format to hand them over in.

```sql
CREATE TABLE categories (
    id          UUID PRIMARY KEY,
    slug        VARCHAR(80)  UNIQUE NOT NULL,  -- "t-shirts" (used in the URL)
    name        VARCHAR(120) NOT NULL,         -- "T-Shirts" (shown on screen)
    sort_order  INTEGER      DEFAULT 0,        -- controls button order
    is_active   BOOLEAN      DEFAULT TRUE
);

CREATE TABLE products (
    id            UUID PRIMARY KEY,
    slug          VARCHAR(120) UNIQUE NOT NULL, -- "world-cup-2026-tee"
    name          VARCHAR(200) NOT NULL,
    description   TEXT,
    price_cents   INTEGER      NOT NULL,        -- ⚠️ SEE THE NOTE BELOW (pence)
    currency      VARCHAR(3)   DEFAULT 'GBP',
    category_id   UUID REFERENCES categories(id),
    image_url     VARCHAR(500),                 -- full URL; swappable storage
    image_alt     VARCHAR(200),                 -- accessibility + SEO
    stock_qty     INTEGER      DEFAULT 0,
    is_active     BOOLEAN      DEFAULT TRUE,    -- hide without deleting
    sort_order    INTEGER      DEFAULT 0,
    created_at    TIMESTAMPTZ  DEFAULT now()
);

CREATE INDEX ix_products_category ON products(category_id) WHERE is_active;
CREATE INDEX ix_products_slug     ON products(slug);
```

#### ⚠️ Why `price_cents INTEGER` and not `price 19.99`

**This is the most important line in the document.** Computers cannot store
`19.99` exactly — the same way you can't write ⅓ exactly as a decimal. Store
money as a decimal and you eventually get an order totalling `59.969999999999`.

So we store **whole pence**: £19.99 becomes `1999`. All maths is on whole
numbers, which is always exact. We divide by 100 only at the moment we print it
on screen. This is what every payment system does — Stripe's API takes the
smallest currency unit too.

*(The column is named `price_cents` rather than `price_pence` because "cents" is
the industry-standard name for "smallest unit of the currency" — it stays
correct if you ever add EUR or USD in Phase 5.)*

I'm flagging this because the existing sweepstake code uses
`entry_fee: Float` (`backend/app/models/__init__.py`). That's a latent bug in
that app. We will not repeat it in the store.

#### Why `image_url` is a full URL

You want photos on Cloudflare R2, and we will do that. But setting up an R2
bucket and API tokens is 30 minutes of account admin that would block today's
quick win.

So: **Phase 1 serves photos as plain files from the app; Phase 2 moves them to
R2.** Because the column holds a complete URL, that migration is one
`UPDATE` statement and **zero schema changes**. Design it once, swap the
storage later, nothing breaks.

### 4.3 📋 ACTION FOR YOU — how to hand over your products

I've created **[`docs/product-import-template.csv`](./product-import-template.csv)**.
Open it in Excel or Google Sheets, fill in one row per product, and give it back.

| Column | What to put | Example |
|---|---|---|
| `name` | Product name as customers see it | `World Cup 2026 Home Tee` |
| `category` | Which group. Just type it — reuse the same spelling | `T-Shirts` |
| `price_gbp` | Normal price in pounds with a decimal point. **I convert to pence** | `19.99` |
| `description` | 1–3 sentences | `Soft cotton tee, unisex fit.` |
| `image_filename` | The photo's filename | `home-tee.jpg` |
| `image_alt` | Describe the photo for blind users & Google | `Navy tee with 2026 crest` |
| `stock_qty` | How many you have. Put `999` if unlimited | `50` |

**Photos:** put them all in one folder, named to match `image_filename` exactly.
JPG or PNG, ideally under 500KB each and roughly square. Don't rename them
after filling in the sheet.

Leave `slug` and `sort_order` alone — I generate those.

### 4.4 Cart design

**Phase 1 — cart lives in the browser** (`localStorage`). No database, no login,
no server work. Refresh the page and it's still there. This is why Phase 1 lands
today.

**Phase 2 — the server never trusts the browser.** This is a security rule, not
a preference. When checking out, the browser sends only *product IDs and
quantities*. The server looks up the real prices from the database itself.

Otherwise anyone can open browser dev-tools, change the price to `0.01`, and buy
your entire stock for a euro. **Prices are always re-read server-side.**

### 4.5 Checkout flow (Phase 2)

```
Customer clicks "Checkout"
   │
   ├─▶ Browser POSTs [{product_id, qty}] to /api/v1/checkout
   │
   ├─▶ Server: look up REAL prices, check stock, create a PENDING order
   │
   ├─▶ Server calls Stripe: stripe.checkout.Session.create(
   │       mode="payment",
   │       line_items=[...real prices...],
   │       shipping_address_collection={"allowed_countries": [...]},
   │       customer_creation="always",     ← guest gets a Stripe customer record
   │       success_url=".../thanks?session_id={CHECKOUT_SESSION_ID}",
   │       cancel_url=".../cart")
   │
   ├─▶ Browser redirects to Stripe's page. Customer types card + address THERE
   │
   ├─▶ Stripe charges the card
   │
   ├─▶ ⚡ Stripe calls OUR webhook: checkout.session.completed
   │       → verify signature → mark order PAID → decrement stock → email receipt
   │
   └─▶ Customer lands back on our "Thank you" page
```

**Guest checkout is Stripe's default** — the customer just types an email. No
password, no account. Exactly what you asked for.

#### Three webhook rules we will not break

These come straight from the Stripe docs and hard-won community experience:

1. **Verify the signature using the RAW request body.** If you let FastAPI parse
   the JSON first, the bytes change and verification fails. Read `await request.body()`
   and pass those exact bytes to `stripe.Webhook.construct_event`.
2. **Never mark an order paid from the success page.** Anyone can visit that URL.
   The webhook is the only source of truth about money.
3. **Be idempotent.** Stripe retries webhooks — you *will* get the same event
   twice. We keep a `webhook_events` table with a **UNIQUE constraint on
   `stripe_event_id`**, and we write that row **in the same database transaction**
   as the fulfilment. If they're in separate transactions, a crash between them
   means you ship the order twice.

### 4.6 Later-phase tables (for reference — built in Phase 2/3)

```sql
CREATE TABLE orders (
    id                UUID PRIMARY KEY,
    order_number      VARCHAR(20) UNIQUE NOT NULL,   -- "WC-10432", human-friendly
    email             VARCHAR(255) NOT NULL,          -- guest checkout needs only this
    user_id           UUID NULL REFERENCES users(id), -- NULL = guest. Phase 4 fills it
    status            VARCHAR(20) DEFAULT 'pending',  -- pending|paid|shipped|cancelled|refunded
    subtotal_cents    INTEGER NOT NULL,
    shipping_cents    INTEGER DEFAULT 0,
    total_cents       INTEGER NOT NULL,
    currency          VARCHAR(3) DEFAULT 'GBP',
    stripe_session_id VARCHAR(255) UNIQUE,
    ship_name         VARCHAR(200),
    ship_line1        VARCHAR(200),
    ship_line2        VARCHAR(200),
    ship_city         VARCHAR(120),
    ship_postal       VARCHAR(20),
    ship_country      VARCHAR(2),
    created_at        TIMESTAMPTZ DEFAULT now(),
    paid_at           TIMESTAMPTZ
);

CREATE TABLE order_items (
    id               UUID PRIMARY KEY,
    order_id         UUID REFERENCES orders(id) ON DELETE CASCADE,
    product_id       UUID REFERENCES products(id),
    name_snapshot    VARCHAR(200) NOT NULL,  -- name AT TIME OF SALE
    price_cents_snap INTEGER NOT NULL,       -- price AT TIME OF SALE
    qty              INTEGER NOT NULL
);

CREATE TABLE webhook_events (
    stripe_event_id VARCHAR(255) PRIMARY KEY,   -- the idempotency guard
    processed_at    TIMESTAMPTZ DEFAULT now()
);
```

**Why "snapshot" columns:** if you raise a T-shirt from €19.99 to €24.99 next
month, last month's receipt must still say €19.99. We copy the name and price
into the order line at purchase time. Orders are history — history doesn't change.

---

## 5. The roadmap

### Phase 0 — Prep the kitchen *(~1 hour, today)*
- [x] Repo decision — **brand new repo** (§2)
- [ ] Create the new repo and copy the reusable core files listed in §2
- [ ] Install superpowers + 3 agents from claude-agents-library
- [ ] Create `categories` + `products` tables via Alembic migration
- [ ] Load your CSV + photos
- **Done when:** `SELECT count(*) FROM products;` returns your real product count

### Phase 1 — 🍔 The food stand *(~3–4 hours, TODAY — the quick win)*
- [ ] `GET /api/v1/categories`
- [ ] `GET /api/v1/products?category=slug` (with pagination)
- [ ] `GET /api/v1/products/{slug}`
- [ ] Catalog page: responsive grid, photo, name, price
- [ ] Category filter buttons
- [ ] Product detail page
- [ ] Cart in `localStorage`: add, change qty, remove, running total
- [ ] Cart badge showing item count
- [ ] Deploy to Railway
- **Done when: you open a public URL on your phone, filter to a category, add
  two items, and the cart total is correct.** ← *That is today's win.*
- **Testing:** Level 0 (Playwright CRX by hand) + Level 1 (smoke) + Level 2 (functional)

### Phase 2 — 💰 The cash register *(1–2 days)*
- [ ] Move photos to Cloudflare R2, update `image_url`
- [ ] `orders`, `order_items`, `webhook_events` tables
- [ ] `POST /api/v1/checkout` — **server-side price lookup**
- [ ] Flat shipping fee via `SHIPPING_FLAT_CENTS` env var, as its own line item
- [ ] Stripe Checkout Session with shipping address collection
- [ ] `POST /api/v1/webhooks/stripe` — signature verify + idempotent fulfilment
- [ ] Thank-you page + cancel page
- [ ] Stripe **test mode** end-to-end, then live keys
- **Done when:** a real card charges, an order row says `paid`, stock drops
- **Testing:** Levels 0–3 + **Level 5 security gate** (mandatory before live keys)

### Phase 3 — 👨‍🍳 One employee *(3–5 days)*
- [ ] Confirmation emails (reuse `services/email.py`)
- [ ] Order lookup by number + email
- [ ] Admin: product CRUD, mark shipped, view orders
- [ ] Image upload to R2 via presigned URL
- [ ] Adopt **gsd-core** and **metaswarm** here
- **Testing:** Levels 0–4, full QA gate

### Phase 4 — 🛎️ Front of house *(3–5 days)*
- [ ] Reuse existing `User` model + `auth.py` (already built ✅)
- [ ] Magic-link login (no passwords to forget)
- [ ] Order history — link past guest orders by matching email
- [ ] "Save my address for next time"
- **Key rule:** accounts stay **100% optional**. Guest checkout must never break

### Phase 5 — 🏆 Full restaurant *(ongoing)*
Stock alerts · product search · discount codes · related products · abandoned
cart emails · sales dashboard · SEO/sitemap · product variants (sizes)

---

## 6. Testing plan (your hierarchy, mapped)

| Level | What | Tool | Gate |
|---|---|---|---|
| **0** | Manual UI/UX click-through | **[Playwright CRX](https://chromewebstore.google.com/detail/playwright-crx/jambeljnbnfbkcpnoiaedcabbgmnnlcd)** Chrome extension — records your clicks and turns them into Python test scripts | Every phase |
| **1** | Smoke: page loads? JS errors? key elements visible? | Playwright | Every deploy |
| **2** | Functional, mocked API | Playwright + `pytest` | Every PR |
| **3** | Integration, real APIs (Stripe test mode) | Playwright + `pytest` + Stripe CLI | Phase 2+ |
| **4** | QA gate: code quality, error handling, DB indexes, performance | Checklist + `metaswarm` reviewers | Before each phase ships |
| **5** | Security gate: input validation, auth, data protection | Checklist + `/security-review` | **Mandatory before live Stripe keys** |

**Playwright CRX is a great call for you specifically** — you're not a developer,
so you click through the store like a customer, the extension records it, and we
get a real automated test for free. Manual testing that writes its own tests.

### Level 5 security checklist (Phase 2 blocker)
- [ ] Prices always re-read server-side — never from the browser
- [ ] Stripe webhook signature verified against the **raw** body
- [ ] Idempotency row + fulfilment in **one transaction**
- [ ] Secrets in Railway env vars only — **never committed to git**
- [ ] `STRIPE_SECRET_KEY` starts `sk_test_` until the gate passes
- [ ] SQL injection: SQLAlchemy parameterised queries only, no f-string SQL
- [ ] CORS restricted to your real domain, not `*`
- [ ] Rate limit on checkout + auth endpoints
- [ ] No card data ever touches our server (Stripe hosts the form)
- [ ] Quantities validated as positive integers ≤ stock

---

## 7. Decisions

### ✅ Settled (2026-07-27)

| # | Question | Decision |
|---|---|---|
| 1 | Repo — pivot, add-on, or new? | **Brand new repo.** Copy the reusable core across (§2) |
| 2 | Currency | **GBP (£).** `price_cents` holds pence. Multi-currency is Phase 5 |
| 3 | Shipping | **Flat fee per order.** One fixed price regardless of basket size |

**How flat-fee shipping works in the build.** A single setting,
`SHIPPING_FLAT_CENTS`, lives in the environment variables — not hardcoded — so
you can change the price later without a code change. At checkout the server
adds it as its own Stripe line item, so the customer sees
"Shipping £4.99" as a separate line rather than a mysteriously inflated total.
Changing it to free shipping later is one variable set to `0`.

### ⏳ Still open (not blocking Phase 1)

| # | Question | Needed by |
|---|---|---|
| 4 | **What is the flat shipping price?** e.g. £4.99 | Phase 2 |
| 5 | **Which countries do you ship to?** UK only, or UK + Ireland, etc. | Phase 2 — sets Stripe's `allowed_countries` |
| 6 | **Store name + rough colour scheme?** | Phase 1 — nice to have, I'll use a neutral default otherwise |

**You do NOT need any accounts today.** Railway, Stripe and Cloudflare accounts
are only needed at Phase 2. Phase 1 runs locally and on Railway's free tier.

---

## 8. Sources

- [obra/superpowers](https://github.com/obra/superpowers) · [open-gsd/gsd-core](https://github.com/open-gsd/gsd-core) (replaces the archived [gsd-build/get-shit-done](https://github.com/gsd-build/get-shit-done)) · [dsifry/metaswarm](https://github.com/dsifry/metaswarm) · [aiagentskit/claude-agents-library](https://github.com/aiagentskit/claude-agents-library)
- [Stripe Checkout Sessions API](https://docs.stripe.com/api/checkout/sessions/create?lang=python) · [Collecting addresses](https://docs.stripe.com/payments/collect-addresses) · [Stripe MCP](https://docs.stripe.com/mcp)
- [Railway CLI docs](https://docs.railway.com/cli) · [Deploy FastAPI on Railway](https://docs.railway.com/guides/fastapi)
- [Cloudflare R2 upload objects](https://developers.cloudflare.com/r2/objects/upload-objects/)
- [Playwright CRX](https://github.com/ruifigueira/playwright-crx) · [Chrome Web Store listing](https://chromewebstore.google.com/detail/playwright-crx/jambeljnbnfbkcpnoiaedcabbgmnnlcd)
- [FastAPI async tests](https://fastapi.tiangolo.com/advanced/async-tests/) · [Testcontainers + FastAPI + asyncpg](https://lealre.github.io/fastapi-testcontainer-asyncpg/)
- Commerce platform comparison: [Vendure](https://vendure.io/blog/best-headless-commerce-platforms) · [Saleor vs Medusa](https://www.netguru.com/blog/saleor-vs-medusa)
- Rejected on licence: [app-generator/ecommerce-fastapi-stripe](https://github.com/app-generator/ecommerce-fastapi-stripe) — subscribers only
- Borrowed patterns (MIT): [aliseyedi01/FastAPI-Ecommerce-API](https://github.com/aliseyedi01/FastAPI-Ecommerce-API)
