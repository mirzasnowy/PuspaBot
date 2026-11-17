/* Service worker Puspa (Fase 2).
 * Cache shell aplikasi + tangani push masuk: ambil isi terbaru dari
 * server lalu tampilkan notifikasi. Di iOS, push hanya jalan kalau
 * PWA dipasang ke layar utama.
 */
const CACHE = 'puspa-v1';
self.addEventListener('install', (event) => {
  event.waitUntil(caches.open(CACHE).then((c) => c.addAll(['/chat', '/office'])));
});
self.addEventListener('fetch', (event) => {
  event.respondWith(
    caches.match(event.request).then((hit) => hit || fetch(event.request))
  );
});
self.addEventListener('push', (event) => {
  const data = event.data ? event.data.text() : 'Puspa punya kabar baru.';
  event.waitUntil(
    self.registration.showNotification('Puspa', { body: data })
  );
});
self.addEventListener('notificationclick', (event) => {
  event.notification.close();
  event.waitUntil(clients.openWindow('/chat'));
});
