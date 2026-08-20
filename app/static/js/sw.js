/*
 * LocoRooSuite service worker (U24.13 - U24.15).
 * Offline shell only: precached shell assets, cache-first /static/,
 * network-first document navigations with offline fallback.
 * SSE (/events/), /api/ and /app/api/ are never intercepted.
 * Bump VERSION whenever shell assets change.
 */
const VERSION = 'v1.0.0';
const SHELL_CACHE = `lr-shell-${VERSION}`;

const SHELL_ASSETS = [
  '/static/css/tailwind.css',
  '/static/fonts/manrope-latin-400-normal.woff2',
  '/static/fonts/manrope-latin-500-normal.woff2',
  '/static/fonts/manrope-latin-600-normal.woff2',
  '/static/fonts/manrope-latin-700-normal.woff2',
  '/static/img/icons/icon-192.png',
  '/static/img/icons/icon-512.png',
  '/static/img/icons/icon-maskable-192.png',
  '/static/img/icons/icon-maskable-512.png',
  '/manifest.webmanifest',
  '/offline',
];

self.addEventListener('install', (event) => {
  event.waitUntil(
    (async () => {
      const cache = await caches.open(SHELL_CACHE);
      await Promise.allSettled(SHELL_ASSETS.map((url) => cache.add(url)));
      await self.skipWaiting();
    })()
  );
});

self.addEventListener('activate', (event) => {
  event.waitUntil(
    (async () => {
      const keys = await caches.keys();
      await Promise.all(
        keys
          .filter((k) => k.startsWith('lr-shell-') && k !== SHELL_CACHE)
          .map((k) => caches.delete(k))
      );
      await self.clients.claim();
    })()
  );
});

self.addEventListener('fetch', (event) => {
  const req = event.request;
  if (req.method !== 'GET') return;
  const url = new URL(req.url);
  if (url.origin !== self.location.origin) return;
  // Never intercept streaming or data endpoints.
  if (
    url.pathname.startsWith('/events/') ||
    url.pathname.startsWith('/api/') ||
    url.pathname.startsWith('/app/api/')
  ) {
    return;
  }
  if (url.pathname.startsWith('/static/')) {
    event.respondWith(cacheFirst(req));
    return;
  }
  if (req.mode === 'navigate') {
    event.respondWith(networkFirstDocument(req));
  }
});

async function cacheFirst(req) {
  const cache = await caches.open(SHELL_CACHE);
  const hit = await cache.match(req);
  if (hit) return hit;
  const resp = await fetch(req);
  if (resp && resp.ok) cache.put(req, resp.clone());
  return resp;
}

async function networkFirstDocument(req) {
  const cache = await caches.open(SHELL_CACHE);
  try {
    const resp = await fetch(req);
    return resp;
  } catch (_err) {
    const offline = await cache.match('/offline');
    if (offline) return offline;
    return new Response('Offline', {
      status: 503,
      headers: { 'Content-Type': 'text/plain; charset=utf-8' },
    });
  }
}

// --- Web Push (U24.20) ---

self.addEventListener('push', (event) => {
  let payload = {};
  try {
    payload = event.data ? event.data.json() : {};
  } catch (_err) {
    payload = {};
  }
  const title = payload.title || 'New email';
  const options = {
    body: payload.body || 'You have a new message in your inbox',
    tag: payload.tag || 'lr-new-mail',
    icon: '/static/img/icons/icon-192.png',
    badge: '/static/img/icons/icon-192.png',
    data: { url: payload.url || '/app/mail/' },
  };
  event.waitUntil(self.registration.showNotification(title, options));
});

self.addEventListener('notificationclick', (event) => {
  event.notification.close();
  const targetUrl = (event.notification.data && event.notification.data.url) || '/app/mail/';
  event.waitUntil(
    (async () => {
      const clientList = await self.clients.matchAll({ type: 'window', includeUncontrolled: true });
      for (const client of clientList) {
        if (client.url.includes(self.location.origin) && 'focus' in client) {
          await client.focus();
          if ('navigate' in client && client.url !== new URL(targetUrl, self.location.origin).href) {
            try {
              await client.navigate(targetUrl);
            } catch (_err) {
              /* focus alone is enough */
            }
          }
          return;
        }
      }
      await self.clients.openWindow(targetUrl);
    })()
  );
});
