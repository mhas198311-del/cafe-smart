const CACHE_NAME = 'wbc-staff-pwa-v2';
const ASSETS = [
  '/staff/app/',
  '/static/manifest.json',
  '/static/logo_pwa_192.png',
  '/static/logo_pwa_512.png'
];

self.addEventListener('install', (e) => {
  e.waitUntil(
    caches.open(CACHE_NAME).then((cache) => {
      return cache.addAll(ASSETS).catch(err => console.log('SW caching error: ', err));
    }).then(() => self.skipWaiting())
  );
});

self.addEventListener('activate', (e) => {
  e.waitUntil(
    caches.keys().then((keys) => {
      return Promise.all(
        keys.map((key) => {
          if (key !== CACHE_NAME) {
            return caches.delete(key);
          }
        })
      );
    }).then(() => self.clients.claim())
  );
});

self.addEventListener('fetch', (e) => {
  if (e.request.method !== 'GET' || e.request.url.includes('/ws/') || e.request.url.includes('/api/')) {
    return;
  }
  e.respondWith(
    caches.match(e.request).then((cached) => {
      if (cached) return cached;
      return fetch(e.request).then((response) => {
        if (response.status === 200 && (e.request.url.includes('/static/') || e.request.url.includes('/fonts/'))) {
          const clone = response.clone();
          caches.open(CACHE_NAME).then(c => c.put(e.request, clone));
        }
        return response;
      });
    }).catch(() => caches.match('/staff/app/'))
  );
});

// Push notification event (triggered when mobile is locked / in background)
self.addEventListener('push', (e) => {
  let data = {
    title: '🚨 نداء طاولة — White Bird Cafe',
    body: 'طاولة بحاجة إلى الخدمة فوراً',
    url: '/staff/app/',
    tag: 'table-call'
  };

  if (e.data) {
    try {
      data = e.data.json();
    } catch (err) {
      data.body = e.data.text();
    }
  }

  const options = {
    body: data.body,
    icon: '/static/logo_pwa_192.png',
    badge: '/static/logo_pwa_192.png',
    vibrate: [400, 150, 400, 150, 600, 200, 800],
    tag: data.tag || 'table-alert',
    renotify: true,
    requireInteraction: true, // Remains on screen until staff dismisses/clicks
    data: {
      url: data.url || '/staff/app/'
    },
    actions: [
      { action: 'open_app', title: '📱 فتح التطبيق' }
    ]
  };

  e.waitUntil(
    self.registration.showNotification(data.title, options)
  );
});

// Click notification event
self.addEventListener('notificationclick', (e) => {
  e.notification.close();
  const targetUrl = (e.notification.data && e.notification.data.url) ? e.notification.data.url : '/staff/app/';

  e.waitUntil(
    clients.matchAll({ type: 'window', includeUncontrolled: true }).then((clientList) => {
      for (const client of clientList) {
        if (client.url.includes('/staff/app') && 'focus' in client) {
          return client.focus();
        }
      }
      if (clients.openWindow) {
        return clients.openWindow(targetUrl);
      }
    })
  );
});
