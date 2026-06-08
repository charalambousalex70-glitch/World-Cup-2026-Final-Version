# START HERE — Your deployment

This package already has every fix from setup baked in:
- Python pinned to 3.12 (`backend/runtime.txt` + `PYTHON_VERSION` in `render.yaml`)
- Your live API URL set in `frontend/public/index.html`
- Hardened service worker + self-contained manifest icons (no missing-file errors)

## Your addresses
- **Backend (Render):** https://sweepstake-api-gd38.onrender.com
- **Frontend (Vercel):** https://world-cup-2026-eight-iota.vercel.app

## Upload steps
1. Upload this whole folder to your GitHub repo (replace existing files).
2. Render auto-redeploys the backend. Wait for "Live", then check
   https://sweepstake-api-gd38.onrender.com/health → should show
   `{"status":"ok","service":"SweepStake Live"}`
3. Vercel auto-redeploys the frontend.

## The ONE setting you must add by hand in Render
The Blueprint can't know your Vercel URL, so add this once in
Render → your service → **Environment** tab → Edit → Add:

```
Key:   CORS_ORIGINS
Value: https://world-cup-2026-eight-iota.vercel.app
```

(no trailing slash). Save. Render restarts automatically.

## Load the demo login (once)
In Render → your service → **Shell** tab, run:

```
python -m app.seed
```

Then sign in at your Vercel site with:  **you@example.com / demo1234**

## IMPORTANT: open the REAL site
Always test at **https://world-cup-2026-eight-iota.vercel.app** —
NOT a downloaded `index.html` from your computer. A local `file:///...`
copy cannot reach the backend and will always show "demo mode".

## Security note
The secrets shown on screen during setup (JWT_SECRET, FOOTBALL_API_KEY,
database password) were visible in screenshots. Once everything works,
rotate them: regenerate the football-data.org key, change JWT_SECRET to a
new random value, and rotate the database password in Render.
