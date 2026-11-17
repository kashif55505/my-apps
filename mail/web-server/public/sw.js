// Service worker: shows new-mail notifications even when the app tab is closed.
self.addEventListener('install', () => self.skipWaiting());
self.addEventListener('activate', (e) => e.waitUntil(self.clients.claim()));

self.addEventListener('push', (e) => {
  let d = {};
  try { d = e.data ? e.data.json() : {}; } catch { d = { title: 'New email', body: e.data && e.data.text() }; }
  e.waitUntil((async () => {
    const wins = await self.clients.matchAll({ type: 'window', includeUncontrolled: true });
    // The open, focused app already shows its own in-app alert
    if (wins.some((w) => w.focused && w.visibilityState === 'visible')) return;
    await self.registration.showNotification(d.title || 'New email', {
      body: `${d.body || ''}\n${d.account || ''}`.trim(),
      tag: `${d.account}-${d.uid}`,
      icon: '/icon-192.png',
      badge: '/icon-192.png',
      data: d,
    });
  })());
});

self.addEventListener('notificationclick', (e) => {
  e.notification.close();
  const d = e.notification.data || {};
  const url = `/?acc=${encodeURIComponent(d.account || '')}&uid=${d.uid || ''}`;
  e.waitUntil((async () => {
    const wins = await self.clients.matchAll({ type: 'window', includeUncontrolled: true });
    const w = wins.find((x) => new URL(x.url).origin === self.location.origin);
    if (w) { await w.focus(); w.postMessage({ type: 'open', account: d.account, uid: d.uid }); return; }
    await self.clients.openWindow(url);
  })());
});
