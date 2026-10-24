// Cache only the dashboard shell. API requests and other services stay network-only.
const CACHE = "erebor-shell-__CACHE_VERSION__";
const SHELL = __SHELL_ASSETS__;

self.addEventListener("install", event => {
  event.waitUntil(caches.open(CACHE).then(cache => cache.addAll(SHELL))
    .then(() => self.skipWaiting()));
});

self.addEventListener("activate", event => {
  event.waitUntil(caches.keys().then(keys => Promise.all(keys
    .filter(key => key.startsWith("erebor-shell-") && key !== CACHE)
    .map(key => caches.delete(key)))).then(() => self.clients.claim()));
});

self.addEventListener("fetch", event => {
  const url = new URL(event.request.url);
  if (event.request.method !== "GET" || url.origin !== self.location.origin) return;
  const path = url.pathname === "/index.html" ? "/" : url.pathname;
  if (!SHELL.includes(path)) return;
  event.respondWith((async () => {
    const cache = await caches.open(CACHE);
    try {
      const response = await fetch(event.request, { cache: "no-cache" });
      if (response.ok && response.type === "basic" && !response.redirected) {
        await cache.put(path, response.clone());
      }
      return response;
    } catch (error) {
      const cached = await cache.match(path);
      if (cached) return cached;
      throw error;
    }
  })());
});
