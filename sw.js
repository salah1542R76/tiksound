const C = "tk-v3";
self.addEventListener("install", () => self.skipWaiting());
self.addEventListener("activate", e => e.waitUntil(
  caches.keys().then(ks => Promise.all(ks.filter(k => k !== C).map(k => caches.delete(k)))).then(() => self.clients.claim())
));
self.addEventListener("fetch", e => {
  const r = e.request, u = new URL(r.url);
  if (r.method !== "GET" || u.origin !== location.origin || u.pathname.startsWith("/api") || u.pathname.startsWith("/audio")) return;
  const keep = res => { if (res.ok && res.headers.get("X-Tiksound")) { const cp = res.clone(); caches.open(C).then(c => c.put(r.mode === "navigate" ? "/" : r, cp)); } return res; };
  if (r.mode === "navigate") {
    // Réseau d'abord : la dernière version du site s'affiche toujours, le cache sert hors ligne
    e.respondWith(fetch(r).then(keep).catch(() => caches.match("/").then(h => h || Response.error())));
    return;
  }
  e.respondWith(caches.match(r).then(hit => { const net = fetch(r).then(keep).catch(() => hit); return hit || net; }));
});
