// Installability and notifications only: nothing is cached, so the app is never stale or readable offline.
self.addEventListener("install", () => self.skipWaiting());
self.addEventListener("activate", (event) => event.waitUntil(self.clients.claim()));
self.addEventListener("fetch", () => {});

const ICON = "/static/admin/icon-192.png";

self.addEventListener("push", (event) => {
  let message = { title: "Reachy", body: "" };
  try {
    message = event.data.json();
  } catch (error) {
    message.body = event.data ? event.data.text() : "";
  }
  // Android shows the actions as buttons; iPhone shows none, and a tap opens the url.
  event.waitUntil(self.registration.showNotification(message.title, {
    body: message.body,
    icon: ICON,
    actions: message.actions || [],
    data: { url: message.url || "" },
  }));
});

async function openApp(url) {
  const windows = await self.clients.matchAll({ type: "window" });
  if (!windows.length) return self.clients.openWindow(url || "/#status");
  const focused = await windows[0].focus();
  return url ? focused.navigate(url).catch(() => focused) : focused;
}

// "Switch" on a new-model notification: switch without opening the app, then say what happens next.
async function switchModel() {
  let body = "Couldn't switch. Open the app to try again.";
  try {
    const response = await fetch("/api/wake/switch", { method: "POST", headers: { "Content-Type": "application/json" }, body: "{}" });
    const answer = await response.json().catch(() => ({}));
    body = (response.ok ? answer.message : answer.error) || body;
  } catch (error) {
    // Unreachable (Tailscale off): the default text says to try in the app.
  }
  return self.registration.showNotification("Wake word", { body, icon: ICON, data: { url: "/#wake" } });
}

self.addEventListener("notificationclick", (event) => {
  event.notification.close();
  if (event.action === "later") return;
  if (event.action === "switch") event.waitUntil(switchModel());
  else event.waitUntil(openApp((event.notification.data || {}).url));
});
