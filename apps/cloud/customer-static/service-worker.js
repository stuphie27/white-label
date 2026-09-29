const CACHE = 'pirouette-shell-v6016';
const SHELL = ['/static/css/style.css', '/static/images/favicon.png', '/manifest.webmanifest'];

self.addEventListener('install', event => {
  self.skipWaiting();
  event.waitUntil(caches.open(CACHE).then(cache => cache.addAll(SHELL)));
});

self.addEventListener('activate', event => {
  event.waitUntil(
    caches.keys()
      .then(keys => Promise.all(keys.filter(key => key !== CACHE).map(key => caches.delete(key))))
      .then(() => self.clients.claim())
  );
});

self.addEventListener('fetch', event => {
  if (event.request.method !== 'GET') return;
  const url = new URL(event.request.url);
  if (url.origin !== location.origin) return;

  // Photographs and live updates must always come from the current database and
  // generated files. Never serve an old event photograph from browser storage.
  if (url.pathname.startsWith('/photos/') || url.pathname.includes('/updates')) {
    event.respondWith(fetch(event.request, {cache: 'no-store'}));
    return;
  }

  if (url.pathname.startsWith('/static/') || url.pathname === '/manifest.webmanifest') {
    event.respondWith(
      fetch(event.request).then(response => {
        if (response.ok) {
          const copy = response.clone();
          caches.open(CACHE).then(cache => cache.put(event.request, copy));
        }
        return response;
      }).catch(() => caches.match(event.request))
    );
  }
});
