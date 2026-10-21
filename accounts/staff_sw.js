// Staff app service worker, served at /staff/sw.js so its scope is /staff/.
// Pages always come from the network (calls and availability must be live);
// only icons and the manifest are cached, and push notifications are shown
// when a table calls while the app is in the background or the phone is locked.
const CACHE_NAME = 'wb-staff-v3';
const ASSETS = ['/static/manifest.json', '/static/logo_pwa_192.png', '/static/logo_pwa_512.png'];

self.addEventListener('install', (e) => {
  e.waitUntil(caches.open(CACHE_NAME).then((c) => c.addAll(ASSETS)).catch(() => {}).then(() => self.skipWaiting()));
});

self.addEventListener('activate', (e) => {
  e.waitUntil(
    caches.keys()
      .then((keys) => Promise.all(keys.filter((k) => k !== CACHE_NAME).map((k) => caches.delete(k))))
      .then(() => self.clients.claim())
  );
});

self.addEventListener('fetch', (e) => {
  const req = e.request;
  if (req.method !== 'GET') return;
  if (req.mode === 'navigate') {
    e.respondWith(fetch(req).catch(() => new Response(
      '<!doctype html><meta charset="utf-8"><meta name="viewport" content="width=device-width">' +
      '<body style="background:#0d0a07;color:#eee;font-family:sans-serif;text-align:center;padding:40px 16px" dir="rtl">' +
      '<h2>لا يوجد اتصال بالإنترنت</h2><p>تأكد من الواي فاي وحاول مرة ثانية.</p>' +
      '<button onclick="location.reload()" style="padding:10px 20px">إعادة المحاولة</button></body>',
      { headers: { 'Content-Type': 'text/html; charset=utf-8' } }
    )));
    return;
  }
  if (ASSETS.some((a) => req.url.endsWith(a))) {
    e.respondWith(caches.match(req).then((hit) => hit || fetch(req)));
  }
});

self.addEventListener('push', (e) => {
  let data = { title: '🚨 نداء طاولة', body: 'طاولة بحاجة إلى الخدمة', url: '/staff/app/', tag: 'table-call' };
  if (e.data) {
    try { data = Object.assign(data, e.data.json()); } catch (err) { data.body = e.data.text(); }
  }
  e.waitUntil(self.registration.showNotification(data.title, {
    body: data.body,
    icon: '/static/logo_pwa_192.png',
    badge: '/static/logo_pwa_192.png',
    vibrate: [400, 150, 400, 150, 600],
    tag: data.tag || 'table-call',
    renotify: true,
    requireInteraction: true,
    data: { url: data.url || '/staff/app/' },
  }));
});

self.addEventListener('notificationclick', (e) => {
  e.notification.close();
  const url = (e.notification.data && e.notification.data.url) || '/staff/app/';
  e.waitUntil(clients.matchAll({ type: 'window', includeUncontrolled: true }).then((list) => {
    for (const c of list) {
      if (c.url.includes('/staff/app') && 'focus' in c) return c.focus();
    }
    return clients.openWindow(url);
  }));
});
