// FundBridge service worker: cache the app shell for fast, installable loads; never cache API data.
// Offline API calls get a small JSON error the UI shows as a message instead of failing silently.
const SHELL = "fundbridge-shell-v1";
self.addEventListener("install", (e) => {
  e.waitUntil(caches.open(SHELL).then((c) => c.addAll(["/", "/manifest.webmanifest", "/icon.svg"])).then(() => self.skipWaiting()));
});
self.addEventListener("activate", (e) => {
  e.waitUntil(caches.keys().then((ks) => Promise.all(ks.filter((k) => k !== SHELL).map((k) => caches.delete(k)))).then(() => self.clients.claim()));
});
self.addEventListener("fetch", (e) => {
  const url = new URL(e.request.url);
  if (e.request.method !== "GET" || url.origin !== self.location.origin) return;
  if (url.pathname.startsWith("/api/")) {
    e.respondWith(fetch(e.request).catch(() => new Response(JSON.stringify({ detail: "You are offline. Try again when connected." }),
      { status: 503, headers: { "Content-Type": "application/json" } })));
    return;
  }
  // App shell + hashed assets: network first, fall back to cache (assets are immutable once built).
  e.respondWith(fetch(e.request).then((r) => {
    if (r.ok && (url.pathname === "/" || url.pathname.startsWith("/assets/"))) {
      const copy = r.clone();
      caches.open(SHELL).then((c) => c.put(e.request, copy));
    }
    return r;
  }).catch(() => caches.match(e.request).then((m) => m || caches.match("/"))));
});
