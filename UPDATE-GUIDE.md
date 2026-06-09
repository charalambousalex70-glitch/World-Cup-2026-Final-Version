# FINAL UPDATE — what to change in your GitHub repo

Replace these 8 files with the versions in this package. Everything else in
your repo stays as-is. After committing, Render and Vercel auto-redeploy.

## Files to update

| # | File in your repo | What changed |
|---|-------------------|--------------|
| 1 | `frontend/public/index.html` | Editable backend URL (🔧 panel), honest LIVE/DEMO badge, real error messages |
| 2 | `frontend/public/sw.js` | Network-first service worker — new deploys show up instantly (fixes the cache trap) |
| 3 | `frontend/public/manifest.webmanifest` | Icons embedded as data — no missing-file 404s |
| 4 | `backend/app/main.py` | Auto-seed on first boot; CORS auto-allows `*.vercel.app`; friendly root route |
| 5 | `backend/app/core/config.py` | Adds the `AUTO_SEED` setting |
| 6 | `backend/runtime.txt` | Pins Python 3.12 (prevents the pydantic-core build failure) |
| 7 | `render.yaml` | Pins `PYTHON_VERSION=3.12.7` for fresh Blueprint deploys |
| 8 | `START-HERE.md` | Updated instructions for the new flow |

## How to update (GitHub web)

For each file: open it in your repo → click the pencil (Edit) → select all →
paste the new contents → Commit. Or upload the whole folder from the zip and
let GitHub overwrite.

## After it redeploys — the ONE thing you do on the live site

1. Open your Vercel site (the real `https://...vercel.app`, not a downloaded file).
2. Click the **🔧** icon (top bar) or the **backend settings** link on sign-in.
3. Paste your Render backend URL (e.g. `https://sweepstake-api-0myw.onrender.com`).
4. Click **Save & Reconnect**.
5. Sign in: `you@example.com` / `demo1234`.

The badge should turn green and say **LIVE**. If it stays gold/**DEMO**, the 🔧
panel now shows a health check and the exact reason (unreachable / CORS / login),
plus the address to add to `CORS_ORIGINS` on your Render service.

## Notes
- The backend now auto-allows any `*.vercel.app` origin, so you likely won't
  need to touch `CORS_ORIGINS` at all anymore.
- The backend auto-seeds the demo data on first boot — no Shell step needed.
- Rotate your secrets (JWT_SECRET, football API key, DB password) since they
  appeared in screenshots during setup.
