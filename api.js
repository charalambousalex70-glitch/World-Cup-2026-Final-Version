# Frontend Notes

## Why a zero-build SPA?

The deliverable needed to be **deployable today** and **installable as a PWA**.
A single `public/index.html` achieves both with no toolchain: Vercel serves it
statically, the service worker makes it installable/offline, and it talks to the
real API over `fetch` + `WebSocket`.

It runs in two modes automatically:
- **Live**: when `window.__API_URL__` is set and the backend authenticates, it
  uses real leaderboard/draw/fixtures data and subscribes to WebSocket updates.
- **Demo**: otherwise it falls back to the seeded in-memory data so the UI is
  always demonstrable.

## Component map → React port

If you want the canonical Vite + React + TypeScript + Tailwind project from the
brief, the existing functions port directly:

| index.html function | React component |
|---------------------|-----------------|
| `renderAuth`        | `<AuthScreen />` |
| `vHome`             | `<Dashboard />` |
| `vBoard`            | `<Leaderboard />` |
| `vDraw` + `doDraw`  | `<LiveDraw />` (the spinning wheel + confetti) |
| `vFixtures`         | `<Fixtures />` |
| `vAdmin`            | `<AdminPanel />` |
| `openCreate`        | `<CreateSweepstakeSheet />` |
| `openInvite`        | `<InviteSheet />` |
| `tabBar`            | `<TabBar />` |

The data layer is already extracted in `src/api.js` (REST client +
`connectLive()` WebSocket helper) — drop it into a React app and call it from
hooks (`useEffect` for `connectLive`, `useState` for leaderboard rows).

### Suggested React structure
```
frontend/
├── index.html
├── vite.config.ts          # vite-plugin-pwa for manifest + sw
├── tailwind.config.ts      # tokens: navy/gold palette from index.html :root
└── src/
    ├── api.ts              # (this api.js, typed)
    ├── App.tsx
    ├── hooks/useLive.ts    # wraps connectLive
    └── components/...       # the table above
```

Use `vite-plugin-pwa` to generate `manifest.webmanifest` + a Workbox service
worker instead of the hand-written `sw.js`. Carry over the CSS variables in
`:root` as Tailwind theme tokens (e.g. `colors.navy`, `colors.gold`).

## Design tokens (from `:root`)

```
--navy   #0a0e1a   --gold  #ffc83d
--card   #141d33   --grn   #2fe28a
--line   #22304f   --red   #ff5b6e
--txt    #eaf0ff   --blu   #4d8dff
```
Fonts: **Anton** (display), **Sora** (body), **JetBrains Mono** (numbers).
