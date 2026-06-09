# API Reference

Base URL: `{BACKEND_URL}/api/v1`
Auth: `Authorization: Bearer <token>` on all routes except register/login.
Interactive docs (Swagger): `{BACKEND_URL}/docs`

## Auth

### POST /auth/register
```json
{ "email": "a@b.com", "username": "Alex", "password": "secret123" }
```
→ `201` `{ "access_token": "...", "token_type": "bearer", "user": { ... } }`

### POST /auth/login
```json
{ "email": "a@b.com", "password": "secret123" }
```
→ `200` same `Token` shape. `401` on bad credentials.

### GET /auth/me
→ current `User`.

## Sweepstakes

### POST /sweepstakes
Create. Creator auto-joins as a paid participant. Prize percentages must sum to 100.
```json
{
  "name": "Office World Cup",
  "tournament_name": "World Cup 2026",
  "competition_code": "WC",
  "entry_fee": 20,
  "currency": "EUR",
  "max_participants": 10,
  "prize_tiers": [
    { "rank": 1, "percentage": 60 },
    { "rank": 2, "percentage": 25 },
    { "rank": 3, "percentage": 15 }
  ]
}
```
→ `201` full `Sweepstake` (with `invite_code`, `prize_pool`, participants, teams).

### GET /sweepstakes
→ all sweepstakes the user administers or has joined.

### GET /sweepstakes/{id}
→ one sweepstake, fully expanded.

### DELETE /sweepstakes/{id}
Admin only. → `204`.

### POST /sweepstakes/join
```json
{ "invite_code": "WC26DEMO" }
```
→ `200` the joined sweepstake. `404` invalid code, `409` full or draw finalized.
Broadcasts `participant_joined`.

## Draw

### POST /sweepstakes/{id}/draw
Admin only. Runs a fair CSPRNG allocation (re-runnable until approved).
→ `200` list of `{ participant_id, participant_name, team_id, team_name, flag_emoji }`.
Broadcasts `draw_completed`. `409` if already approved.

### POST /sweepstakes/{id}/draw/approve
Admin only. Locks the draw permanently, sets status `active`.
→ `200` sweepstake. Broadcasts `draw_approved`.

## Live data

### GET /sweepstakes/{id}/leaderboard
→ ranked rows:
```json
[{ "rank":1, "participant_name":"You", "team_name":"Brazil",
   "flag_emoji":"🇧🇷", "stage":"Winner", "points":120,
   "eliminated":false, "potential_payout":120.0 }]
```

### GET /sweepstakes/{id}/fixtures
→ list of fixtures.

### POST /sweepstakes/{id}/sync
Admin only. Forces an immediate football-API sync. Broadcasts
`leaderboard_updated` if anything changed.

### GET /sweepstakes/{id}/notifications
→ this user's notifications for the sweepstake, newest first.

## Payments

### PATCH /sweepstakes/{id}/participants/{pid}/payment
Admin only.
```json
{ "has_paid": true }
```
→ `200` updated sweepstake.

## WebSocket

`WS {BACKEND_URL}/ws/sweepstakes/{id}`

On connect you receive `{ "event": "connected", "data": { "room": "<id>" } }`.
Send any text (e.g. `"ping"`) as keepalive. Events pushed by the server:

| Event | Payload | Meaning |
|-------|---------|---------|
| `participant_joined` | `{ username, count }` | someone joined |
| `draw_completed` | `{ allocations: [...] }` | draw run (pre-approval) |
| `draw_approved` | `{}` | draw locked |
| `leaderboard_updated` | `{ leaderboard: [...] }` | scores changed |
| `fixtures_updated` | `{ count }` | N fixtures changed |

## Errors

Standard FastAPI `{ "detail": "..." }` with appropriate status codes
(`401` auth, `403` not admin, `404` missing, `409` conflict, `422` validation).
