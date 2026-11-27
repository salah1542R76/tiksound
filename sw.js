const C = "tk-v11";
// Coquille de l'appli gardée dès l'installation : elle s'ouvre hors ligne après une seule visite. lang-en.js suit le N de tk-vN (même ?v=N que index.html).
const SHELL = ["/", "/manifest.webmanifest", "/lang-en.js?v=" + C.slice(4), "/favicon.svg", "/favicon.ico", "/icon-192.png"];
self.addEventListener("install", e => { self.skipWaiting(); e.waitUntil(caches.open(C).then(c => Promise.all(SHELL.map(u =>
  fetch(u, { cache: "no-cache" }).then(res => res.ok && res.headers.get("X-Tiksound") && c.put(u, res)).catch(() => {}))))); });
self.addEventListener("activate", e => e.waitUntil(
  caches.keys().then(ks => Promise.all(ks.filter(k => k !== C).map(k => caches.delete(k)))).then(() => self.clients.claim())
));
self.addEventListener("fetch", e => {
  const r = e.request, u = new URL(r.url);
  if (r.method !== "GET" || u.origin !== location.origin || u.pathname.startsWith("/api") || u.pathname.startsWith("/audio") || u.pathname.startsWith("/ads") || u.pathname.startsWith("/confidentialite") || u.pathname.startsWith("/conditions") || u.pathname.startsWith("/signalement") || u.pathname.startsWith("/promo")) return;   // /promo : vidéo lue par morceaux (206), jamais gardée
  const keep = res => { if (res.ok && res.headers.get("X-Tiksound")) { const cp = res.clone(); caches.open(C).then(c => c.put(r.mode === "navigate" ? "/" : r, cp)); } return res; };
  if (r.mode === "navigate") {
    // Réseau d'abord, 3 s au plus : au-delà (connexion bloquée), la copie gardée s'affiche et la version reçue ensuite sert au lancement suivant
    const net = fetch(r).then(keep);
    e.waitUntil(net.catch(() => {}));
    e.respondWith(Promise.race([net, new Promise(ok => setTimeout(ok, 3000))])
      .then(res => res || caches.match("/").then(h => h || net))
      .catch(() => caches.match("/").then(h => h || Response.error())));
    return;
  }
  e.respondWith(caches.match(r).then(hit => { const net = fetch(r).then(keep).catch(() => hit); return hit || net; }));
});
