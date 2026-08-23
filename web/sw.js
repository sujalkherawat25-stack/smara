self.addEventListener('install', event => {
  event.waitUntil(caches.open('smara-v3').then(cache => cache.addAll([
    '/app/',
    '/app/styles.css',
    '/app/workspace.css?v=native-workspace-v3',
    '/app/app.js?v=native-workspace-v3',
    '/app/manifest.webmanifest',
  ])));
  self.skipWaiting();
});
self.addEventListener('activate', event => {
  event.waitUntil(caches.keys().then(keys => Promise.all(
    keys.filter(key => key !== 'smara-v3').map(key => caches.delete(key)),
  )));
  self.clients.claim();
});
self.addEventListener('fetch', event => {
  if (event.request.method !== 'GET' || new URL(event.request.url).origin !== location.origin) return;
  event.respondWith(fetch(event.request).then(response => {
    const cached = response.clone();
    caches.open('smara-v3').then(cache => cache.put(event.request, cached));
    return response;
  }).catch(() => caches.match(event.request)));
});
self.addEventListener('push', event => { const data = event.data ? event.data.json() : {}; event.waitUntil(self.registration.showNotification(data.title || 'Smara', { body: data.body || 'An update needs your attention.', data: { url: data.url || '/app/' } })); });
self.addEventListener('notificationclick', event => { event.notification.close(); event.waitUntil(clients.openWindow(event.notification.data.url)); });
