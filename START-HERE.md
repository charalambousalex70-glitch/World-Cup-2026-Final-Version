# START HERE — SweepStake Live (v3, hardened)

This version fixes the fragile parts that caused setup pain. Key changes:

1. **Backend URL is now editable on the live site** — no more editing code &
   redeploying when your Render URL changes. Open the app, tap the **🔧** icon
   (top bar, or the link on the sign-in screen), paste your backend URL, Save.
2. **The DEMO/LIVE badge is now honest** — it shows **DEMO** (gold) when not
   connected and **LIVE** (green) when connected. Tap DEMO to open settings.
3. **Real error messages** — if it can't connect, the 🔧 panel tells you exactly
   why (unreachable vs. CORS vs. login rejected) and shows the address to put in
   CORS_ORIGINS.
4. **No more cache trap** — the service worker is network-first for the page, so
   new deploys show up immediately.
5. **Auto-seed** — the backend loads the demo data itself on first boot. No more
   running `python -m app.seed` in the Shell by hand.
6. **CORS auto-allows *.vercel.app** — renaming your Vercel project won't break it.

## Deploy (fresh)

### Backend — Render
1. New → **Blueprint**, point at this repo. It builds the API + Postgres.
2. It will ask for `CORS_ORIGINS` — you can leave it blank now (the server also
   auto-allows any `*.vercel.app` URL). Optionally paste your exact Vercel URL.
3. Wait for **Live**. Visit `<your-api>/health` → `{"status":"ok",...}`.
   The demo data seeds itself automatically.

### Frontend — Vercel
1. Add New → Project → this repo.
2. **Root Directory: `frontend`**. Framework: Other. Deploy.
3. Open the site, click **🔧**, paste your Render API URL, **Save & Reconnect**.
4. Sign in: **you@example.com / demo1234** → should show **LIVE**.

That's it. The 🔧 step replaces all the code-editing/redeploy/cache-clearing
that used to be required.

## Quick fixes
- **Stuck on DEMO?** Tap 🔧. The health line tells you if the backend is
  reachable. If it says CORS, the panel shows the exact address to add to
  `CORS_ORIGINS` on the Render service.
- **Always test the real URL** (`https://....vercel.app`), never a downloaded
  `index.html` from your computer.

## Security
Rotate the secrets that were visible during setup: regenerate the
football-data.org key, set a fresh random `JWT_SECRET`, rotate the DB password.
