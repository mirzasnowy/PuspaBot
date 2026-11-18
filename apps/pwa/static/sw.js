/* Puspa service worker (G1: SPA shell + offline cache).
 * Caches the SPA shell and the built assets, handles incoming
 * push by fetching the latest content from the server and showing
 * a notification. On iOS, push only works when the PWA is installed
 * on the home screen.
 */
const CACHE = "puspa-v2";
const SHELL = ["/chat", "/manifest.json"];
self.addEventListener("install", (event) => {
  event.waitUntil(
    caches
      .open(CACHE)
      .then((cache) => cache.addAll(SHELL))
      .then(() => self.skipWaiting()),
  );
});
self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches
      .keys()
      .then((keys) =>
        Promise.all(keys.filter((key) => key !== CACHE).map((key) => caches.delete(key))),
      )
      .then(() => self.clients.claim()),
  );
});
self.addEventListener("fetch", (event) => {
  const url = new URL(event.request.url);
  if (event.request.method !== "GET" || url.origin !== self.location.origin) return;
  // Versioned Vite assets are immutable: cache-first.
  if (url.pathname.startsWith("/assets/") || url.pathname.startsWith("/static/")) {
    event.respondWith(
      caches.open(CACHE).then((cache) =>
        cache.match(event.request).then(
          (hit) =>
            hit ||
            fetch(event.request).then((response) => {
              if (response.ok) cache.put(event.request, response.clone());
              return response;
            }),
        ),
      ),
    );
    return;
  }
  // Screens and API calls always go to the network (live data).
});
self.addEventListener("push", (event) => {
  const data = event.data ? event.data.text() : "Puspa has news for you.";
  event.waitUntil(self.registration.showNotification("Puspa", { body: data }));
});
self.addEventListener("notificationclick", (event) => {
  event.notification.close();
  event.waitUntil(self.clients.openWindow("/chat"));
});
