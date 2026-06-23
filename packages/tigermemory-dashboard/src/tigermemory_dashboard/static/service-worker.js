const CACHE_NAME = 'tigermemory-memory-ops-v77';
const OFFLINE_URL = '/offline.html';
const DASHBOARD_PATHS = new Set([
  '/start',
  '/health',
  '/quality',
  '/canvas',
  '/self-evolution',
  '/agent-tools',
  '/settings'
]);
const APP_SHELL = [
  OFFLINE_URL,
  '/manifest.webmanifest',
  '/static/assets/tailwindcss.min.js',
  '/static/assets/lucide.min.js',
  '/static/i18n.json',
  '/static/i18n.js',
  '/static/dashboard-common.js',
  '/static/dashboard-pages.js',
  '/static/tiger/tigermemory_tiger_logo.svg',
  '/static/tiger/tigerlogo.png',
  '/static/tiger/tigermemory_tiger_logo_192.png',
  '/static/tiger/tigermemory_tiger_logo_512.png',
  '/static/tiger/tigermemory_tiger_stripes_bg.svg'
];

function shouldCache(request) {
  const url = new URL(request.url);
  if (url.origin !== self.location.origin) return false;
  if (request.mode === 'navigate') return false;
  if (url.pathname.startsWith('/api/')) return false;
  if (url.pathname.startsWith('/digest')) return false;
  if (url.pathname.startsWith('/daily')) return false;
  if (url.pathname === '/' || url.pathname === '/sw-reset') return false;
  return APP_SHELL.includes(url.pathname) || url.pathname.startsWith('/static/');
}

self.addEventListener('install', event => {
  event.waitUntil(
    caches.open(CACHE_NAME).then(cache => cache.addAll(APP_SHELL))
  );
  self.skipWaiting();
});

self.addEventListener('activate', event => {
  event.waitUntil(
    caches.keys().then(keys => Promise.all(
      keys.filter(key => key !== CACHE_NAME).map(key => caches.delete(key))
    ))
      .then(() => self.clients.claim())
      .then(() => self.clients.matchAll({ type: 'window', includeUncontrolled: true }))
      .then(clients => Promise.all(clients.map(client => {
        try {
          const url = new URL(client.url);
          const shouldRefresh = url.origin === self.location.origin &&
            (DASHBOARD_PATHS.has(url.pathname) || url.pathname.startsWith('/digest/'));
          if (shouldRefresh && 'navigate' in client) {
            return client.navigate(client.url).catch(() => null);
          }
        } catch {
        }
        return null;
      })))
  );
});

self.addEventListener('message', event => {
  if (event.data && event.data.type === 'SKIP_WAITING') {
    self.skipWaiting();
  }
});

self.addEventListener('fetch', event => {
  const request = event.request;
  if (request.method !== 'GET') return;
  event.respondWith(
    fetch(request)
      .then(response => {
        if (response.ok && shouldCache(request)) {
          const copy = response.clone();
          caches.open(CACHE_NAME).then(cache => cache.put(request, copy));
        }
        return response;
      })
      .catch(() => caches.match(request).then(cached => cached || caches.match(OFFLINE_URL)))
  );
});
