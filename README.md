# SweepStake Live ⚽

Real-time football sweepstake platform. Create a sweepstake, invite friends,
run an animated team draw, then watch the leaderboard recalculate live as real
results come in.

```
┌─────────────────────────┐         ┌──────────────────────────────┐
│  Frontend (Vercel)      │  HTTPS  │  Backend (Render)            │
│  - PWA: installable     │ ──────► │  FastAPI + Uvicorn           │
│  - index.html + sw.js   │         │  - JWT auth                  │
│  - live via WebSocket   │ ◄═════► │  - WebSocket rooms           │
└─────────────────────────┘   WSS   │  - background football poller│
                                     └──────────────┬───────────────┘
                                                    │  asyncpg
                                          ┌─────────▼─────────┐
                                          │ PostgreSQL (Render)│
                                          └───────────────────┘
                                                    ▲  HTTPS poll
                                          ┌─────────┴─────────┐
                                          │ football-data.org │
                                          └───────────────────┘
```

## Stack

| Layer     | Technology |
|-----------|-----------|
| Frontend  | Vanilla SPA (PWA), zero-build, deployable static to Vercel |
| Backend   | FastAPI, SQLAlchemy 2 (async), Uvicorn |
| Realtime  | Native WebSockets (room per sweepstake) |
| Database  | PostgreSQL (asyncpg) |
| Auth      | JWT (HS256, Bearer tokens) |
| Live data | football-data.org v4 (offline/demo mode when no key) |

> The frontend ships as a single self-contained `index.html` so it deploys
> instantly with no build step. The React/TypeScript/Tailwind structure
> described in the brief maps 1:1 onto the components in `index.html`
> (`vHome`, `vBoard`, `vDraw`, `vFixtures`, `vAdmin`) — see `docs/FRONTEND.md`
> for how to port it into a Vite + React project if you prefer that toolchain.

## Repository layout

```
sweepstake/
├── backend/
│   ├── app/
│   │   ├── main.py            # FastAPI app, CORS, lifespan, poller startup
│   │   ├── seed.py            # demo data loader
│   │   ├── core/              # config, database, security (JWT, hashing)
│   │   ├── models/            # SQLAlchemy ORM = the schema
│   │   ├── schemas/           # Pydantic request/response models
│   │   ├── api/               # auth + sweepstakes routes, deps
│   │   ├── services/          # scoring, draw, football API, poller
│   │   └── websocket/         # connection manager + ws route
│   ├── requirements.txt
│   ├── Dockerfile
│   └── .env.example
├── frontend/
│   ├── public/
│   │   ├── index.html         # the app (UI + live integration)
│   │   ├── manifest.webmanifest
│   │   ├── sw.js              # service worker (offline shell + API cache)
│   │   └── icons/
│   ├── src/api.js             # standalone API client (for a React port)
│   └── vercel.json
├── render.yaml                # one-click backend + DB blueprint
├── docker-compose.yml         # local dev (Postgres + API)
└── docs/
    ├── API.md                 # endpoint reference
    └── DEPLOY.md              # step-by-step deployment
```

## Quick start (local)

```bash
# 1. Backend + Postgres via Docker
docker compose up --build
docker compose exec api python -m app.seed     # load the demo sweepstake

# 2. Frontend — any static server pointed at frontend/public
cd frontend/public && python3 -m http.server 5173
```

Open http://localhost:5173. To connect the frontend to your local API, edit the
one line near the bottom of `index.html`:

```js
window.__API_URL__ = "http://localhost:8000";
```

Log in with **you@example.com / demo1234**. Leave `__API_URL__` empty to run the
self-contained demo with no backend at all.

## Database schema (summary)

| Table | Key columns | Purpose |
|-------|-------------|---------|
| `users` | email, username, hashed_password, avatar_color | accounts |
| `sweepstakes` | name, tournament_name, entry_fee, currency, invite_code, status, draw_approved, admin_id | a competition |
| `participants` | sweepstake_id, user_id, has_paid | membership + payment |
| `teams` | sweepstake_id, name, flag_emoji, stage, eliminated | tournament teams |
| `allocations` | participant_id (unique), team_id (unique per sweep) | the permanent draw |
| `fixtures` | home/away team, scores, status, stage | matches |
| `prize_tiers` | rank, percentage | payout split |
| `notifications` | user_id, icon, title, body, read | activity feed |

Uniqueness constraints on `allocations` enforce **one team per participant** and
**no duplicate teams** at the database level — the draw can't corrupt itself.

## Scoring

Points = furthest stage the allocated team has reached:

| Stage | Group | R16 | QF | SF | Final | Champion |
|-------|-------|-----|----|----|-------|----------|
| Pts   | 10    | 25  | 45 | 70 | 90    | 120      |

The leaderboard recomputes on every fixture change and broadcasts to all
connected clients.

## How "live" works

1. Background poller (`services/poller.py`) hits football-data.org every
   `FOOTBALL_POLL_SECONDS` for each **active** sweepstake.
2. Changed fixtures update `fixtures` + derive each team's `stage`/`eliminated`.
3. Leaderboard is recomputed and pushed via WebSocket
   (`leaderboard_updated`), plus per-user notifications are written.
4. The frontend's WebSocket handler applies the new leaderboard and re-renders —
   no refresh, no polling on the client.

Without a `FOOTBALL_API_KEY` the poller idles and the app uses the seeded
sample fixtures, so everything is demonstrable offline.

See `docs/DEPLOY.md` for production deployment and `docs/API.md` for the full
endpoint reference.
