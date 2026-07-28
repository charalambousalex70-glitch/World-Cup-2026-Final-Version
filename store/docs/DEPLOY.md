# Putting the store online

Follow this top to bottom. It takes about 30 minutes the first time.

**Nothing here charges a real card until Step 7.** Everything before that runs
in Stripe's test mode, where the card number `4242 4242 4242 4242` works and no
money moves.

---

## Before you start

You need three free accounts:

| Account | What for | Sign up |
|---|---|---|
| **GitHub** | Holds the code | github.com |
| **Railway** | Runs the site and the database | railway.app |
| **Stripe** | Takes the payments | stripe.com |

And the Railway command-line tool:

```bash
npm install -g @railway/cli
```

*(If `npm` isn't installed, get Node.js from nodejs.org first.)*

---

## Step 1 — Log in to Railway

```bash
railway login
```

A browser window opens. Approve it, come back to the terminal.

---

## Step 2 — Create the project

From inside the store folder:

```bash
railway init
```

Give it a name when asked, e.g. `online-store`.

---

## Step 3 — Add the database

```bash
railway add --database postgres
```

Railway creates a Postgres database and sets `DATABASE_URL` for you
automatically. **You never have to copy that value anywhere** — the app reads
it, and `app/core/config.py` converts it to the format SQLAlchemy needs.

---

## Step 4 — Set your settings

```bash
railway variables \
  --set 'STORE_NAME=Your Store Name' \
  --set 'CURRENCY=GBP' \
  --set 'CURRENCY_SYMBOL=£' \
  --set 'SHIPPING_FLAT_CENTS=499' \
  --set 'SHIPPING_COUNTRIES=GB' \
  --set 'ENVIRONMENT=production'
```

`SHIPPING_FLAT_CENTS` is in **pence**: `499` is £4.99. Use `0` for free shipping.

`SHIPPING_COUNTRIES` is a comma-separated list of two-letter country codes —
`GB` for the UK alone, `GB,IE` to add Ireland. Stripe will refuse to ship
anywhere not on this list.

---

## Step 5 — Deploy

```bash
railway up
```

This uploads the code, builds it, and starts it. Takes a couple of minutes.

Then give it a public address:

```bash
railway domain
```

You'll get something like `online-store-production.up.railway.app`. **Copy it.**

Now tell the app its own address — Stripe needs this to send customers back
after paying:

```bash
railway variables --set 'PUBLIC_BASE_URL=https://YOUR-ADDRESS-HERE'
```

Visit the address in a browser. **The shop should load, with no products yet.**

---

## Step 6 — Load your products

```bash
railway run python -m app.seed data/products.csv
```

`railway run` runs the command on your machine but pointed at Railway's
database, so your real catalog gets loaded.

Refresh the site. Your products are there.

> **Photos:** at this stage the images are files inside the deploy. That works
> fine. Moving them to Cloudflare R2 (so they load faster and survive redeploys
> independently) is a later step — set `IMAGE_BASE_URL` to your R2 bucket url
> and re-run the seed command.

---

## Step 7 — Switch on payments

### 7a. Get your Stripe test key

In the Stripe dashboard, make sure the **Test mode** toggle is ON (top right).
Go to **Developers → API keys** and copy the **Secret key** — it starts
`sk_test_`.

```bash
railway variables --set 'STRIPE_SECRET_KEY=sk_test_...'
```

### 7b. Tell Stripe where to send confirmations

In Stripe: **Developers → Webhooks → Add endpoint**.

- **Endpoint URL:** `https://YOUR-ADDRESS-HERE/api/v1/webhooks/stripe`
- **Events to send:** select `checkout.session.completed`

Click **Add endpoint**, then reveal the **Signing secret** — it starts `whsec_`.

```bash
railway variables --set 'STRIPE_WEBHOOK_SECRET=whsec_...'
```

**Why this matters:** that webhook is the only thing that marks an order paid.
The "thank you" page never does — anyone can visit a url, so a page load is not
evidence that money moved. A signed message from Stripe is.

### 7c. Redeploy and test

```bash
railway up
```

Now buy something from your own shop. Use Stripe's test card:

| Field | Value |
|---|---|
| Card number | `4242 4242 4242 4242` |
| Expiry | Any future date, e.g. `12/30` |
| CVC | Any 3 digits |
| Postcode | Any valid one |

**Check all four of these:**

1. You land on a thank-you page with an order number
2. In Stripe → **Payments**, the payment shows as succeeded
3. At `https://YOUR-ADDRESS/orders`, the order number + your email finds it, marked **Paid**
4. The product's stock has gone down by what you bought

If all four pass, the whole chain works.

---

## Step 8 — Go live (only when you're ready)

**Do the security checklist in `docs/PRD-STORE.md` §6 first.** Once you switch
to live keys, real customers can spend real money, and mistakes cost actual
cash.

In Stripe, turn **Test mode OFF**. Repeat steps 7a and 7b to get the **live**
key (`sk_live_`) and a **new** webhook signing secret — test and live webhooks
are separate, and the test one will not work in live mode.

```bash
railway variables --set 'STRIPE_SECRET_KEY=sk_live_...'
railway variables --set 'STRIPE_WEBHOOK_SECRET=whsec_...'
railway up
```

The app logs a loud warning at startup when it's in live mode, so you can always
tell which you're in by running `railway logs`.

Then buy one real item yourself with your own card, confirm it works, and refund
it in the Stripe dashboard.

---

## Everyday commands

| What you want | Command |
|---|---|
| See what's happening / errors | `railway logs` |
| Change a setting | `railway variables --set 'KEY=value'` |
| See all settings | `railway variables` |
| Update products | edit the CSV, then `railway run python -m app.seed data/products.csv` |
| Deploy new code | `railway up` |

---

## When something's wrong

**The site won't load.** Run `railway logs`. Almost always a missing setting —
the app says which one.

**"Card payments are not switched on yet."** `STRIPE_SECRET_KEY` or
`STRIPE_WEBHOOK_SECRET` is missing. Check with `railway variables`.

**Payment works but the order stays "Awaiting payment".** The webhook isn't
arriving. In Stripe → Developers → Webhooks, click your endpoint and look at
recent deliveries — Stripe shows you the exact error. Usually the URL is wrong
(it must end `/api/v1/webhooks/stripe`) or `STRIPE_WEBHOOK_SECRET` is from the
wrong mode.

**Products don't appear.** The seed command ran against the wrong database.
Make sure you used `railway run` in front of it.

**Images are broken.** The filenames in `data/products.csv` must match the files
in `web/images/` exactly — including capital letters and the `.jpg` / `.png`
ending. The seed command prints a warning listing any it couldn't find.

---

## Testing payments on your own machine

You don't have to deploy to test checkout. Stripe's CLI forwards real webhook
events to your laptop:

```bash
stripe login
stripe listen --forward-to localhost:8000/api/v1/webhooks/stripe
```

It prints a `whsec_...` secret. Put that in your local `.env` along with your
`sk_test_...` key, restart the app, and checkout works end to end locally.
