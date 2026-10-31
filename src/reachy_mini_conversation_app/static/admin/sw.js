// Installability and notifications only: nothing is cached, so the app is never stale or readable offline.
self.addEventListener("install", () => self.skipWaiting());
self.addEventListener("activate", (event) => event.waitUntil(self.clients.claim()));
self.addEventListener("fetch", () => {});

self.addEventListener("push", (event) => {
  let message = { title: "Reachy", body: "" };
  try {
    message = event.data.json();
  } catch (error) {
    message.body = event.data ? event.data.text() : "";
  }
  event.waitUntil(self.registration.showNotification(message.title, { body: message.body, icon: "/static/admin/icon-192.png" }));
});

self.addEventListener("notificationclick", (event) => {
  event.notification.close();
  event.waitUntil(
    self.clients.matchAll({ type: "window" }).then((windows) => (windows.length ? windows[0].focus() : self.clients.openWindow("/#status")))
  );
});
