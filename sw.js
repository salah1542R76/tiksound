const C="tk-v1";
self.addEventListener("install",e=>{e.waitUntil(fetch("/").then(r=>r.headers.get("X-Tiksound")?caches.open(C).then(c=>c.put("/",r)):0).catch(()=>{}));self.skipWaiting()});
self.addEventListener("activate",e=>e.waitUntil(self.clients.claim()));
self.addEventListener("fetch",e=>{
  const r=e.request,u=new URL(r.url);
  if(r.method!=="GET"||u.origin!==location.origin||u.pathname.startsWith("/api")||u.pathname.startsWith("/audio"))return;
  const key=r.mode==="navigate"?"/":r;
  e.respondWith(caches.match(key).then(hit=>{
    const net=fetch(r).then(res=>{if(res.ok&&res.headers.get("X-Tiksound")){const cp=res.clone();caches.open(C).then(c=>c.put(key,cp))}return res}).catch(()=>hit);
    return hit||net;
  }));
});
