"use strict";

const $ = (selector) => document.querySelector(selector);
const state = { people: [], versions: {}, faces: [], groups: [], editing: null, status: null };

async function api(path, { method = "GET", body } = {}) {
  const options = { method, headers: {} };
  if (body !== undefined) {
    options.headers["Content-Type"] = "application/json";
    options.body = JSON.stringify(body);
  }
  const response = await fetch(path, options);
  let payload = {};
  try {
    payload = await response.json();
  } catch (error) {
    payload = {};
  }
  if (!response.ok) {
    throw new Error(payload.error || `Something went wrong (${response.status}).`);
  }
  return payload;
}

function toast(message) {
  const box = $("#toast");
  box.textContent = message;
  box.hidden = false;
  clearTimeout(toast.timer);
  toast.timer = setTimeout(() => (box.hidden = true), 3500);
}

async function run(button, action) {
  if (button) button.disabled = true;
  try {
    return await action();
  } catch (error) {
    toast(error.message);
    return undefined;
  } finally {
    if (button) button.disabled = false;
  }
}

function element(tag, props = {}, children = []) {
  const node = document.createElement(tag);
  Object.assign(node, props);
  for (const child of [].concat(children)) {
    node.append(child instanceof Node ? child : document.createTextNode(child));
  }
  return node;
}

function ago(value) {
  const then = typeof value === "number" ? value * 1000 : Date.parse(value);
  if (!then) return "";
  const minutes = Math.round((Date.now() - then) / 60000);
  if (minutes < 1) return "just now";
  if (minutes < 60) return `${minutes} min ago`;
  const hours = Math.round(minutes / 60);
  if (hours < 24) return `${hours} h ago`;
  return new Date(then).toLocaleDateString();
}

// Tabs ------------------------------------------------------------------------------------------

const views = { people: loadPeople, camera: openCamera, status: loadStatus, activity: loadActivity, settings: loadSettings };

function showTab() {
  const tab = views[location.hash.slice(1)] ? location.hash.slice(1) : "people";
  for (const view of document.querySelectorAll(".view")) view.hidden = view.id !== `view-${tab}`;
  for (const link of document.querySelectorAll(".tabs a")) {
    link.toggleAttribute("aria-current", link.dataset.tab === tab);
    if (link.dataset.tab === tab) link.setAttribute("aria-current", "page");
  }
  if (tab !== "camera") closeCamera();
  if ($("#person-dialog").open) $("#person-dialog").close();
  views[tab]();
}

// Privacy ---------------------------------------------------------------------------------------

function showPrivacy(on) {
  const button = $("#privacy");
  button.setAttribute("aria-pressed", String(on));
  button.textContent = on ? "Privacy on" : "Privacy off";
}

$("#privacy").addEventListener("click", (event) => {
  const on = $("#privacy").getAttribute("aria-pressed") !== "true";
  const question = on
    ? "Turn privacy on? Reachy's mic and camera go off and he goes to sleep."
    : "Turn privacy off? Reachy's mic and camera come back on.";
  if (!confirm(question)) return;
  run(event.currentTarget, async () => {
    const status = await api("/api/privacy", { method: "POST", body: { on } });
    showPrivacy(status.privacy.on);
    toast(on ? "Privacy on: mic and camera off." : "Privacy off.");
    if (location.hash === "#camera") openCamera();
  });
});

// People ----------------------------------------------------------------------------------------

function faceFor(person) {
  return state.faces.find((face) => face.id === person.face);
}

function photoUrl(face, photo) {
  return `/api/faces/photo?person_id=${encodeURIComponent(face.id)}&photo=${encodeURIComponent(photo)}`;
}

function groupName(chat) {
  const group = state.groups.find((g) => g.id === chat);
  return group ? group.name : "a group";
}

function renderPeople() {
  const list = $("#people");
  list.replaceChildren();
  for (const [index, person] of state.people.entries()) {
    const face = faceFor(person);
    const avatar = face && face.photos.length
      ? element("img", { src: photoUrl(face, face.photos[0]), alt: "" })
      : element("span", { className: "initial" }, person.name.slice(0, 1).toUpperCase());
    const tags = [];
    if (person.chat) tags.push(`Messages to ${groupName(person.chat)}`);
    if (person.phone) tags.push("Calls");
    if (face) tags.push("Face");
    const card = element("button", { className: "card", type: "button" }, [
      avatar,
      element("span", { className: "who" }, [
        element("div", { className: "name" }, person.name),
        element("div", { className: "tags" }, tags.join(" · ") || "Nothing set up yet"),
      ]),
    ]);
    card.addEventListener("click", () => openPerson(index));
    list.append(element("li", {}, card));
  }
  const shared = $("#shared-names");
  shared.hidden = !state.shared.length;
  shared.textContent = state.shared.length
    ? "Two people answer to the same name, so Reachy won't know who's meant: " +
      state.shared.map((s) => `"${s.said}" (${s.people.join(", ")})`).join("; ")
    : "";
}

function takePeople(payload) {
  state.people = payload.people;
  state.versions = payload.versions;
  state.shared = payload.shared;
  state.faces = payload.faces || [];
  state.facesAvailable = payload.faces !== null;
  renderPeople();
}

async function loadPeople() {
  await run(null, async () => takePeople(await api("/api/people")));
  if (!state.groups.length) {
    api("/api/groups").then((payload) => {
      state.groups = payload.groups;
      renderPeople();
    }).catch(() => {});
  }
}

function openPerson(index) {
  state.editing = index;
  const person = index === null ? { name: "", say: [], chat: "", phone: "", face: "" } : state.people[index];
  const form = $("#person-form");
  $("#person-heading").textContent = index === null ? "New person" : person.name;
  form.name.value = person.name;
  form.say.value = person.say.join(", ");
  form.phone.value = person.phone;
  const select = form.chat;
  select.replaceChildren(element("option", { value: "" }, "None"));
  for (const group of state.groups) select.append(element("option", { value: group.id }, group.name));
  if (person.chat && !state.groups.some((g) => g.id === person.chat)) {
    select.append(element("option", { value: person.chat }, "Current group"));
  }
  select.value = person.chat;
  $("#person-delete").hidden = index === null;
  $("#person-error").hidden = true;
  renderFace(person);
  $("#person-dialog").showModal();
}

function renderFace(person) {
  const box = $("#face-photos");
  box.replaceChildren();
  $("#face-box").hidden = !state.facesAvailable;
  const face = faceFor(person);
  if (!face) {
    box.append(element("p", { className: "hint" }, person.name ? "No face yet." : "Save the name first."));
    return;
  }
  for (const photo of face.photos) {
    const remove = element("button", { type: "button", ariaLabel: "Delete photo" }, "×");
    remove.addEventListener("click", () =>
      run(remove, async () => {
        if (!confirm("Delete this photo?")) return;
        await api("/api/faces/forget-photo", { method: "POST", body: { person_id: face.id, photo } });
        await refreshFaces(person);
      })
    );
    box.append(element("figure", {}, [element("img", { src: photoUrl(face, photo), alt: "" }), remove]));
  }
}

async function refreshFaces(person) {
  takePeople(await api("/api/people"));
  const current = state.people.find((p) => p.name === person.name) || person;
  if (!current.face) {
    const enrolled = state.faces.find((f) => f.name.toLowerCase() === person.name.toLowerCase());
    if (enrolled) current.face = enrolled.id;
  }
  renderFace(current);
}

function editedPerson() {
  const form = $("#person-form");
  const previous = state.editing === null ? {} : state.people[state.editing];
  return {
    name: form.name.value.trim(),
    say: form.say.value.split(",").map((s) => s.trim()).filter(Boolean),
    chat: form.chat.value,
    phone: form.phone.value.trim(),
    face: previous.face || "",
  };
}

async function savePeople(people) {
  takePeople(await api("/api/people", { method: "PUT", body: { people, versions: state.versions } }));
}

$("#person-form").addEventListener("submit", (event) => {
  event.preventDefault();
  const person = editedPerson();
  const people = [...state.people];
  if (state.editing === null) people.push(person);
  else people[state.editing] = person;
  run($("#person-save"), async () => {
    try {
      await savePeople(people);
      $("#person-dialog").close();
      toast(`${person.name} saved.`);
    } catch (error) {
      $("#person-error").textContent = error.message;
      $("#person-error").hidden = false;
    }
  });
});

$("#person-cancel").addEventListener("click", () => $("#person-dialog").close());
$("#add-person").addEventListener("click", () => openPerson(null));

$("#person-delete").addEventListener("click", (event) => {
  const person = state.people[state.editing];
  if (!confirm(`Remove ${person.name}? Reachy won't message or call them any more.`)) return;
  run(event.currentTarget, async () => {
    await savePeople(state.people.filter((_, index) => index !== state.editing));
    const face = faceFor(person);
    if (face && confirm(`Also forget ${person.name}'s face?`)) {
      await api("/api/faces/forget", { method: "POST", body: { person_id: face.id } });
      takePeople(await api("/api/people"));
    }
    $("#person-dialog").close();
  });
});

function shrink(file) {
  return new Promise((resolve, reject) => {
    const image = new Image();
    image.onload = () => {
      const scale = Math.min(1, 1280 / Math.max(image.width, image.height));
      const canvas = document.createElement("canvas");
      canvas.width = Math.round(image.width * scale);
      canvas.height = Math.round(image.height * scale);
      canvas.getContext("2d").drawImage(image, 0, 0, canvas.width, canvas.height);
      URL.revokeObjectURL(image.src);
      resolve(canvas.toDataURL("image/jpeg", 0.88).split(",")[1]);
    };
    image.onerror = () => reject(new Error("That photo could not be read."));
    image.src = URL.createObjectURL(file);
  });
}

function faceName() {
  const name = $("#person-form").name.value.trim();
  if (!name) throw new Error("Type the name first.");
  return name;
}

$("#face-upload").addEventListener("change", (event) => {
  const files = [...event.target.files].slice(0, 6);
  event.target.value = "";
  run(null, async () => {
    const name = faceName();
    const photos = await Promise.all(files.map(shrink));
    const result = await api("/api/faces/upload", { method: "POST", body: { name, photos } });
    if (result.skipped && result.skipped.length) toast(result.skipped.join("; "));
    await linkFace(name, result.person);
  });
});

$("#face-enroll").addEventListener("click", (event) =>
  run(event.currentTarget, async () => {
    const name = faceName();
    toast("Look at Reachy…");
    const result = await api("/api/faces/enroll", { method: "POST", body: { name } });
    await linkFace(name, result.person);
  })
);

async function linkFace(name, enrolled) {
  if (!enrolled) return;
  const index = state.people.findIndex((p) => p.name === name);
  if (index >= 0 && state.people[index].face !== enrolled.id) {
    const people = state.people.map((p, i) => (i === index ? { ...p, face: enrolled.id } : p));
    await savePeople(people);
  } else {
    takePeople(await api("/api/people"));
  }
  renderFace({ ...editedPerson(), face: enrolled.id });
  toast("Face saved.");
}

// Camera ----------------------------------------------------------------------------------------

function openCamera() {
  const image = $("#camera");
  const note = $("#camera-note");
  note.hidden = true;
  image.onerror = async () => {
    image.removeAttribute("src");
    let message = "The camera isn't available.";
    try {
      const status = await api("/api/status");
      if (!status.app.running) message = "Reachy isn't running.";
      else if (status.privacy.on) message = "Privacy is on: the camera is off.";
    } catch (error) {
      message = error.message;
    }
    note.textContent = message;
    note.hidden = false;
  };
  image.src = `/api/camera?t=${Date.now()}`;
}

function closeCamera() {
  const image = $("#camera");
  image.onerror = null;
  image.removeAttribute("src");
}

for (const button of document.querySelectorAll("[data-head]")) {
  button.addEventListener("click", () =>
    run(button, () => api("/api/head", { method: "POST", body: { direction: button.dataset.head } }))
  );
}

$("#look-up").addEventListener("click", (event) =>
  run(event.currentTarget, async () => {
    await api("/api/look-up", { method: "POST" });
    toast("Head up for two minutes.");
  })
);

document.addEventListener("visibilitychange", () => {
  if (location.hash !== "#camera") return;
  if (document.hidden) closeCamera();
  else openCamera();
});

// Status ----------------------------------------------------------------------------------------

function statusRow(label, value, good) {
  const shown = element("span", { className: good === true ? "good" : good === false ? "bad" : "" }, value);
  return element("li", {}, [element("span", {}, label), shown]);
}

async function loadStatus() {
  const status = await run(null, () => api("/api/status"));
  if (!status) return;
  state.status = status;
  showPrivacy(status.privacy.on);
  const app = status.app;
  const rows = [
    statusRow("Reachy", app.running ? (app.sleeping ? "Asleep" : "Awake") : "Not running", app.running),
    statusRow("Privacy", status.privacy.on ? `On since ${ago(status.privacy.since)}` : "Off", status.privacy.on ? false : true),
    statusRow("WhatsApp", status.whatsapp.ok === null ? status.whatsapp.detail : status.whatsapp.ok ? "Linked" : status.whatsapp.detail, status.whatsapp.ok),
    statusRow("Calls", status.calls.ok === null ? "Not set up" : status.calls.ok ? "Ready" : status.calls.detail, status.calls.ok),
  ];
  if (app.quiet_hours) rows.push(statusRow("Quiet hours", "Now", null));
  const display = status.display;
  if (display.last_check_in) {
    const battery = display.battery_v ? `, battery ${display.battery_v} V` : "";
    rows.push(statusRow("E-ink screen", `Seen ${ago(display.last_check_in)}${battery}`, display.recent));
  }
  rows.push(statusRow("Free disk", `${status.disk_free_gb} GB`, status.disk_free_gb > 5));
  const list = $("#status");
  list.replaceChildren(...rows);
  if (status.volume !== null && status.volume !== undefined) list.append(volumeRow(status.volume));
  $("#restart").hidden = !status.can_restart;
  $("#wake").disabled = !app.running || status.privacy.on;
  $("#sleep").disabled = !app.running;
  showPushState();
}

function volumeRow(volume) {
  const slider = element("input", { type: "range", min: 0, max: 100, step: 5, value: volume, ariaLabel: "Volume" });
  slider.addEventListener("change", () =>
    run(null, () => api("/api/volume", { method: "POST", body: { level: Number(slider.value) } }))
  );
  return element("li", {}, [element("span", {}, "Volume"), slider]);
}

$("#wake").addEventListener("click", (event) => run(event.currentTarget, async () => {
  await api("/api/wake", { method: "POST" });
  loadStatus();
}));

$("#sleep").addEventListener("click", (event) => run(event.currentTarget, async () => {
  await api("/api/sleep", { method: "POST" });
  loadStatus();
}));

$("#restart").addEventListener("click", (event) => {
  if (!confirm("Restart Reachy? Anything he's doing stops.")) return;
  run(event.currentTarget, async () => {
    await api("/api/restart", { method: "POST" });
    toast("Restarting…");
    setTimeout(loadStatus, 15000);
  });
});

// Notifications ---------------------------------------------------------------------------------

function pushSupported() {
  return "serviceWorker" in navigator && "PushManager" in window && "Notification" in window;
}

async function showPushState() {
  const on = $("#push-on");
  const test = $("#push-test");
  if (!pushSupported()) {
    on.hidden = true;
    test.hidden = true;
    $("#push-hint").textContent = "Add Reachy to your home screen to get notifications.";
    return;
  }
  const registration = await navigator.serviceWorker.ready;
  const subscription = await registration.pushManager.getSubscription();
  on.textContent = subscription ? "Turn off notifications" : "Turn on notifications";
  test.hidden = !subscription;
}

function keyBytes(base64url) {
  const padded = (base64url + "===".slice((base64url.length + 3) % 4)).replace(/-/g, "+").replace(/_/g, "/");
  return Uint8Array.from(atob(padded), (c) => c.charCodeAt(0));
}

$("#push-on").addEventListener("click", (event) =>
  run(event.currentTarget, async () => {
    const registration = await navigator.serviceWorker.ready;
    const current = await registration.pushManager.getSubscription();
    if (current) {
      await api("/api/push/unsubscribe", { method: "POST", body: { endpoint: current.endpoint } });
      await current.unsubscribe();
    } else {
      if ((await Notification.requestPermission()) !== "granted") throw new Error("Notifications are blocked.");
      const { key } = await api("/api/push/key");
      const subscription = await registration.pushManager.subscribe({ userVisibleOnly: true, applicationServerKey: keyBytes(key) });
      await api("/api/push/subscribe", { method: "POST", body: subscription.toJSON() });
    }
    await showPushState();
  })
);

$("#push-test").addEventListener("click", (event) =>
  run(event.currentTarget, async () => {
    const result = await api("/api/push/test", { method: "POST" });
    toast(result.delivered ? "Sent." : "No phone accepted it.");
  })
);

// Activity --------------------------------------------------------------------------------------

const ACTIVITY_TEXT = {
  call_out: (e) => `Called ${e.who}`,
  call_in: (e) => (e.outcome === "missed" ? `Missed call from ${e.who}` : `${e.who} called`),
  voice_note: (e) => `Voice note to ${e.who}`,
  photo: (e) => `Photo to ${e.who}`,
};

function describe(entry) {
  const text = (ACTIVITY_TEXT[entry.kind] || ((e) => `${e.kind} ${e.who || ""}`))(entry);
  const parts = [text];
  if (entry.seconds) parts.push(`${Math.round(entry.seconds / 60) || "<1"} min`);
  if (entry.outcome && !["sent", "missed", "hung_up"].includes(entry.outcome)) parts.push(entry.outcome.replace(/_/g, " "));
  return parts.join(" · ");
}

async function loadActivity() {
  const payload = await run(null, () => api("/api/activity"));
  if (!payload) return;
  const row = (text, at) => element("li", {}, [element("span", {}, text), element("time", {}, ago(at))]);
  const activity = payload.activity.map((e) => row(describe(e), e.at));
  $("#activity").replaceChildren(...(activity.length ? activity : [element("li", {}, "Nothing yet.")]));
  const changes = payload.changes.map((e) => row(`${e.who.split("@")[0]}: ${e.action}${e.target ? ` (${e.target})` : ""}`, e.at));
  $("#changes").replaceChildren(...(changes.length ? changes : [element("li", {}, "No changes yet.")]));
  const totals = payload.usage.totals || {};
  $("#usage").textContent = totals.today === undefined
    ? "No cost data."
    : `Today $${totals.today.toFixed(2)} · 7 days $${totals.last_7_days.toFixed(2)} · 30 days $${totals.last_30_days.toFixed(2)} (estimates)`;
}

// Settings --------------------------------------------------------------------------------------

async function loadSettings() {
  const payload = await run(null, () => api("/api/settings"));
  if (!payload) return;
  const form = $("#settings");
  form.replaceChildren();
  state.settings = {};
  for (const setting of payload.settings) {
    state.settings[setting.key] = setting.value || setting.default;
    let input;
    if (setting.kind === "flag") {
      input = element("input", { type: "checkbox", name: setting.key, checked: (setting.value || setting.default) === "1" });
      form.append(element("label", { className: "toggle" }, [setting.label, input]));
      continue;
    }
    if (setting.kind === "choice") {
      input = element("select", { name: setting.key }, [element("option", { value: "" }, "Default")]);
      for (const choice of setting.choices) input.append(element("option", { value: choice }, choice));
    } else if (setting.kind === "number") {
      input = element("input", { type: "number", name: setting.key, min: setting.low, max: setting.high, step: 0.05, placeholder: "Default" });
    } else if (setting.kind === "hours") {
      input = element("input", { name: setting.key, placeholder: "21:30-07:00, or empty for none", inputMode: "numeric" });
    } else {
      input = element("input", { name: setting.key, placeholder: "eduardo,adriana", autocapitalize: "none" });
    }
    input.value = setting.value;
    form.append(element("label", {}, [setting.label, input]));
  }
  const save = element("button", { className: "primary", type: "submit" }, "Save and restart Reachy");
  form.append(save);
}

$("#settings").addEventListener("submit", (event) => {
  event.preventDefault();
  const changes = {};
  for (const input of event.target.elements) {
    if (!input.name) continue;
    const value = input.type === "checkbox" ? (input.checked ? "1" : "0") : input.value.trim();
    if (value !== state.settings[input.name]) changes[input.name] = value;
  }
  if (!Object.keys(changes).length) {
    toast("Nothing changed.");
    return;
  }
  run(event.submitter, async () => {
    await api("/api/settings", { method: "PUT", body: { changes } });
    try {
      await api("/api/restart", { method: "POST" });
      toast("Saved. Reachy is restarting.");
    } catch (error) {
      toast(`Saved. Restart Reachy for it to apply (${error.message})`);
    }
  });
});

// Start -----------------------------------------------------------------------------------------

window.addEventListener("hashchange", showTab);
if ("serviceWorker" in navigator) navigator.serviceWorker.register("/sw.js").catch(() => {});
api("/api/me").then((me) => ($("#title").textContent = me.name ? `Reachy · ${me.name.split(" ")[0]}` : "Reachy")).catch(() => {});
api("/api/status").then((status) => showPrivacy(status.privacy.on)).catch(() => {});
showTab();
