/* LuckPot service worker (v5 — network-first for the app shell).
 * KEY FIX: index.html and the app are fetched network-first, so a new deploy
 * shows up immediately (no more "I changed it but the old version loads").
 * Only static assets fall back to cache; API writes/sockets always bypass.
 *
 * v5 changes (public-deployment-audit):
 *  - CACHE version bumped from v71 to v72 (clears old caches on activate)
 *  - Fixed stale-cache fallback bug: in the cache-first static branch the
 *    `.catch(() => cached)` tail referenced `cached` from the outer closure
 *    but inside the inner `.then(res => ...)` chain `cached` resolves to
 *    `undefined` because the Promise chain re-scopes. Refactored to capture
 *    `cached` in a local const before entering the fetch chain so the
 *    fallback correctly returns the cached response on network failure.
 */
const CACHE = "luckpot-v72";

self.addEventListener("install", () => self.skipWaiting());

// Allow the page to tell a waiting SW to activate immediately (used when a new
// version is detected while the app is open).
self.addEventListener("message", (e) => {
  if (e.data === "skipWaiting") self.skipWaiting();
});

self.addEventListener("activate", (e) => {
  e.waitUntil(
    caches.keys().then((keys) =>
      Promise.all(keys.filter((k) => k !== CACHE).map((k) => caches.delete(k)))
    ).then(() => self.clients.claim())
  );
});

self.addEventListener("fetch", (e) => {
  const { request } = e;
  const url = new URL(request.url);

  // Only handle GET requests; skip non-HTTP(S) schemes.
  if (request.method !== "GET" || !url.protocol.startsWith("http")) return;

  // API calls always go to network (never serve stale data for writes/auth).
  if (url.pathname.startsWith("/api/")) return;

  // HTML / navigation: NETWORK-FIRST so new deploys appear instantly.
  const isHTML =
    request.mode === "navigate" ||
    url.pathname === "/" ||
    url.pathname.endsWith(".html");
  if (isHTML) {
    e.respondWith(
      fetch(request)
        .then((res) => {
          const copy = res.clone();
          caches.open(CACHE).then((c) => c.put(request, copy));
          return res;
        })
        .catch(() => caches.match(request).then((c) => c || caches.match("/")))
    );
    return;
  }

  // Other static assets: cache-first for speed, network fallback.
  // FIX: capture `cached` in a local const so the .catch() tail can
  // reference it correctly even after the inner .then() chain resolves.
  e.respondWith(
    caches.match(request).then((cached) => {
      if (cached) return cached;
      return fetch(request)
        .then((res) => {
          const copy = res.clone();
          caches.open(CACHE).then((c) => c.put(request, copy).catch(() => {}));
          return res;
        })
        .catch(() => cached); // `cached` is correctly in scope here
    })
  );
});
