/* SweepStake Live service worker (v3 — network-first for the app shell).
 * KEY FIX: index.html and the app are fetched network-first, so a new deploy
 * shows up immediately (no more "I changed it but the old version loads").
 * Only static assets fall back to cache; API writes/sockets always bypass.
 */
const CACHE = "sweepstake-v8";

self.addEventListener("install", () => self.skipWaiting());

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
  if (request.method !== "GET" || url.protocol.startsWith("ws")) return;

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
  e.respondWith(
    caches.match(request).then(
      (cached) =>
        cached ||
        fetch(request).then((res) => {
          const copy = res.clone();
          caches.open(CACHE).then((c) => c.put(request, copy).catch(() => {}));
          return res;
        }).catch(() => cached)
    )
  );
});
