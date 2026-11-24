// Install ke liye zaroori service worker. Data cache nahi karta (privacy ke liye).
self.addEventListener('install', () => self.skipWaiting());
self.addEventListener('activate', e => e.waitUntil(self.clients.claim()));
self.addEventListener('fetch', () => {});
