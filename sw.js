/* Overlay Tips app service worker.
   Network first for everything on overlay.tips, so members always get the latest tips and odds;
   the saved copy is only used when there's no signal. Odds data, the live API and Supabase
   (other domains) are never touched or cached. */
const CACHE = 'overlay-v1';
const CORE = ['/', '/index.html', '/site.css', '/nav.js?v=3', '/logo-nav.png', '/icon-192.png'];

self.addEventListener('install', e => {
  e.waitUntil(caches.open(CACHE).then(c => c.addAll(CORE)).catch(() => {}));
  self.skipWaiting();
});

self.addEventListener('activate', e => {
  e.waitUntil(caches.keys().then(ks => Promise.all(ks.filter(k => k !== CACHE).map(k => caches.delete(k))))
    .then(() => self.clients.claim()));
});

self.addEventListener('fetch', e => {
  const req = e.request;
  if (req.method !== 'GET') return;
  const url = new URL(req.url);
  if (url.origin !== self.location.origin) return;
  e.respondWith(
    fetch(req).then(res => {
      if (res.ok && res.type === 'basic') {
        const copy = res.clone();
        caches.open(CACHE).then(c => c.put(req, copy));
      }
      return res;
    }).catch(() => caches.match(req, { ignoreSearch: req.mode === 'navigate' })
      .then(r => r || (req.mode === 'navigate' ? caches.match('/') : undefined))
      .then(r => r || new Response('Offline', { status: 503, statusText: 'Offline' })))
  );
});
