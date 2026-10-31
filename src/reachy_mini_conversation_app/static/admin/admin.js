"use strict";

const $ = (selector) => document.querySelector(selector);
const state = {
  me: null, status: null, people: [], versions: {}, shared: [], faces: [], facesAvailable: false,
  groups: null, created: [], groupsError: "", number: "", draft: null, editing: null, log: "activity", settings: null,
};

// Basics ----------------------------------------------------------------------------------------

async function api(path, { method = "GET", body } = {}) {
  const options = { method, headers: {} };
  if (body !== undefined) {
    options.headers["Content-Type"] = "application/json";
    options.body = JSON.stringify(body);
  }
  let response;
  try {
    response = await fetch(path, options);
  } catch (error) {
    throw new Error("Can't reach Reachy. Is Tailscale on?");
  }
  let payload = {};
  try {
    payload = await response.json();
  } catch (error) {
    payload = {};
  }
  if (!response.ok) throw new Error(payload.error || `Something went wrong (${response.status}).`);
  return payload;
}

function icon(name, className = "icon") {
  const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  svg.setAttribute("class", className);
  svg.setAttribute("aria-hidden", "true");
  const use = document.createElementNS("http://www.w3.org/2000/svg", "use");
  use.setAttribute("href", `#i-${name}`);
  svg.append(use);
  return svg;
}

function el(tag, props = {}, children = []) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(props)) {
    if (key.startsWith("aria") || key.startsWith("data-") || key === "role") node.setAttribute(key.replace(/^aria([A-Z])/, (_, c) => `aria-${c.toLowerCase()}`), value);
    else node[key] = value;
  }
  for (const child of [].concat(children)) {
    if (child === null || child === undefined || child === false) continue;
    node.append(child instanceof Node ? child : document.createTextNode(child));
  }
  return node;
}

function toast(message, tone = "good") {
  const box = $("#toast");
  box.replaceChildren(icon(tone === "bad" ? "alert" : "check"), el("span", {}, message));
  box.dataset.tone = tone;
  clearTimeout(toast.timer);
  // A popover sits in the top layer, so the message shows above an open sheet too; reopening keeps it on top.
  if (box.showPopover) {
    if (box.matches(":popover-open")) box.hidePopover();
    box.showPopover();
    toast.timer = setTimeout(() => box.hidePopover(), tone === "bad" ? 5000 : 3000);
  } else {
    box.hidden = false;
    toast.timer = setTimeout(() => (box.hidden = true), tone === "bad" ? 5000 : 3000);
  }
}

async function run(button, action) {
  if (button) button.disabled = true;
  try {
    return await action();
  } catch (error) {
    toast(error.message, "bad");
    return undefined;
  } finally {
    if (button) button.disabled = false;
  }
}

function ask({ title, body = "", yes = "OK", danger = false }) {
  const dialog = $("#ask");
  $("#ask-title").textContent = title;
  $("#ask-body").textContent = body;
  $("#ask-body").hidden = !body;
  const button = $("#ask-yes");
  button.textContent = yes;
  button.className = danger ? "pill danger-fill" : "pill sun";
  dialog.returnValue = "";
  dialog.showModal();
  return new Promise((resolve) => dialog.addEventListener("close", () => resolve(dialog.returnValue === "yes"), { once: true }));
}

function firstName(text) {
  return String(text || "").trim().split(/\s+/)[0] || "";
}

function hue(text) {
  let total = 0;
  for (const char of String(text)) total = (total * 31 + char.codePointAt(0)) % 360;
  return total;
}

function when(value) {
  const then = typeof value === "number" ? value * 1000 : Date.parse(value);
  return Number.isFinite(then) ? new Date(then) : null;
}

function ago(value) {
  const then = when(value);
  if (!then) return "";
  const minutes = Math.round((Date.now() - then) / 60000);
  if (minutes < 1) return "just now";
  if (minutes < 60) return `${minutes} min ago`;
  const hours = Math.round(minutes / 60);
  if (hours < 24) return `${hours} h ago`;
  return then.toLocaleDateString(undefined, { day: "numeric", month: "short" });
}

function clock(value) {
  const then = when(value);
  return then ? then.toLocaleTimeString(undefined, { hour: "numeric", minute: "2-digit" }) : "";
}

function digits(text) {
  return String(text || "").replace(/\D/g, "");
}

function setFill(slider) {
  const fill = ((slider.value - slider.min) / (slider.max - slider.min)) * 100;
  slider.style.setProperty("--fill", `${fill}%`);
}

// Tabs ------------------------------------------------------------------------------------------

const views = { home: loadHome, people: loadPeople, camera: openCamera, gallery: loadGallery, activity: loadActivity, settings: loadSettings };
const titles = { people: "People", camera: "Camera", gallery: "Gallery", activity: "Activity", settings: "Settings" };
let currentTab = "";

function showTab() {
  const tab = views[location.hash.slice(1)] ? location.hash.slice(1) : "home";
  currentTab = tab;
  for (const view of document.querySelectorAll(".view")) view.hidden = view.id !== `view-${tab}`;
  for (const link of document.querySelectorAll(".tabs a")) {
    if (link.dataset.tab === tab) link.setAttribute("aria-current", "page");
    else link.removeAttribute("aria-current");
  }
  if (tab === "settings") $(".gear").setAttribute("aria-current", "page");
  else $(".gear").removeAttribute("aria-current");
  showTitle();
  if (tab !== "camera") closeCamera();
  if ($("#person-sheet").open) $("#person-sheet").close();
  if ($("#viewer").open) $("#viewer").close();
  for (const sheet of document.querySelectorAll("#costs-sheet, #personality-sheet, #memory-sheet")) if (sheet.open) sheet.close();
  $("#save-bar").hidden = true;
  window.scrollTo(0, 0);
  views[tab]();
}

function showTitle() {
  const name = state.me && firstName(state.me.name);
  $("#title").textContent = currentTab === "home" ? (name ? `Hi, ${name}` : "Reachy") : titles[currentTab];
}

// Privacy ---------------------------------------------------------------------------------------

function showPrivacy(on) {
  const button = $("#privacy");
  button.setAttribute("aria-pressed", String(on));
  button.querySelector("use").setAttribute("href", on ? "#i-eye-off" : "#i-eye");
  button.querySelector("span").textContent = on ? "Privacy on" : "Privacy";
}

async function setPrivacy(on, button) {
  const yes = await ask(on
    ? { title: "Turn privacy on?", body: "His mic and camera switch off and he goes to sleep. Only this app turns them back on.", yes: "Turn on" }
    : { title: "Turn privacy off?", body: "His mic and camera come back on.", yes: "Turn off" });
  if (!yes) return;
  await run(button, async () => {
    const status = await api("/api/privacy", { method: "POST", body: { on } });
    showPrivacy(status.privacy.on);
    toast(on ? "Privacy on. Mic and camera are off." : "Privacy off.");
    if (currentTab === "home") loadHome();
    if (currentTab === "camera") openCamera();
  });
}

$("#privacy").addEventListener("click", (event) =>
  setPrivacy($("#privacy").getAttribute("aria-pressed") !== "true", event.currentTarget)
);

// Home ------------------------------------------------------------------------------------------

function reachyState(status) {
  const app = status.app;
  if (!app.running) return app.restarting ? "restarting" : "off";
  if (status.privacy.on) return "private";
  if (app.sleeping) return app.quiet_hours ? "quiet" : "asleep";
  return "awake";
}

const MOODS = {
  awake: { title: "Awake", detail: () => "He's listening.", action: ["Put to sleep", "moon", () => control("/api/sleep")] },
  asleep: { title: "Napping", detail: () => "He wakes up when he hears his name.", action: ["Wake up", "sun", () => control("/api/wake")] },
  quiet: { title: "Quiet hours", detail: () => "He won't listen or take calls until quiet hours end." },
  private: {
    title: "Privacy on",
    detail: (s) => `His mic and camera have been off since ${clock(s.privacy.since) || "earlier"}.`,
    action: ["Turn privacy off", "eye", (button) => setPrivacy(false, button)],
  },
  off: {
    title: "Not running",
    detail: (s) => (s.can_restart ? "Restart him to bring him back." : "Start Reachy on the Mac mini."),
    action: ["Restart Reachy", "power", (button) => restart(button)],
  },
  restarting: { title: "Restarting", detail: () => "Back in about a minute." },
};

async function control(path) {
  await api(path, { method: "POST" });
  await loadHome();
}

function renderHome(status) {
  const mood = reachyState(status);
  const copy = MOODS[mood];
  $("#stage").dataset.state = mood;
  $("#mood").textContent = copy.title;
  $("#mood-detail").textContent = copy.detail(status);
  const button = $("#stage-action");
  const action = mood === "off" && !status.can_restart ? null : copy.action;
  button.hidden = !action;
  if (action) {
    button.replaceChildren(icon(action[1]), action[0]);
    button.onclick = () => run(button, () => action[2](button));
  }

  const volume = status.volume;
  $("#volume-row").hidden = typeof volume !== "number";
  const slider = $("#volume");
  if (typeof volume === "number" && document.activeElement !== slider) {
    slider.value = volume;
    $("#volume-value").textContent = volume;
    setFill(slider);
  }

  renderGames(status.app.running ? status.app.game : null, mood);
  $("#health").replaceChildren(...healthCells(status));
  $("#restart").hidden = !status.can_restart || mood === "off";
}

// Games: start one from here (the tap is the yes), or pause and end the one he is playing.
function renderGames(game, mood) {
  const show = Boolean(game && game.games && game.games.length) && mood !== "private";
  $("#games").hidden = $("#games-title").hidden = !show;
  if (!show) return;
  const on = game.state === "playing" || game.state === "paused";
  const pill = (label, className, action) => {
    const button = el("button", { className: `pill small ${className}`, type: "button" }, label);
    button.onclick = () => run(button, action);
    return button;
  };
  const act = async (action, key) => {
    await api("/api/game", { method: "POST", body: { action, game: key } });
    await loadHome();
  };
  $("#games").replaceChildren(
    ...game.games.map((each) => {
      const name = each.title.en.charAt(0).toUpperCase() + each.title.en.slice(1);
      const current = on && game.game === each.key;
      const detail = !current
        ? `"Juguemos ${each.title.es}"`
        : game.state === "paused"
          ? `Paused${game.progress ? ` · ${game.progress}` : ""}`
          : game.progress || "Playing";
      const buttons = current
        ? [
            game.state === "paused" ? pill("Resume", "sun", () => act("resume")) : pill("Pause", "soft", () => act("pause")),
            pill("End", "ghost", () => act("end")),
          ]
        : [pill("Play", "sun", () => act("start", each.key))];
      if (!current && on) buttons[0].disabled = true;
      return el("div", { className: "row" }, [
        icon("game"),
        el("div", { className: "row-text" }, [el("span", { className: "row-label" }, name), el("span", { className: "row-sub" }, detail)]),
        ...buttons,
      ]);
    })
  );
}

function healthCells(status) {
  const cell = (iconName, label, value, good) =>
    el("div", { className: `cell ${good === true ? "good" : good === false ? "bad" : ""}` }, [icon(iconName), el("b", {}, label), el("span", {}, value)]);
  const whatsapp = status.whatsapp;
  const calls = status.calls;
  const display = status.display;
  let screen = "Not seen yet";
  if (display.last_check_in) screen = display.recent ? "Checked in" : `Last seen ${ago(display.last_check_in)}`;
  const volts = display.battery_v;
  if (typeof volts === "number" && display.recent) {
    screen = volts >= 4.3 ? "Charging" : `Battery ${Math.max(0, Math.min(100, Math.round(((volts - 3.4) / 0.75) * 100)))}%`;
  }
  return [
    cell("chat", "WhatsApp", whatsapp.ok === null ? "Not set up" : whatsapp.ok ? "Linked" : "Not linked", whatsapp.ok),
    cell("phone", "Calls", calls.ok === null ? "Not set up" : calls.ok ? "Ready" : "Not connected", calls.ok),
    cell("screen", "E-ink screen", screen, display.last_check_in ? Boolean(display.recent) : null),
    cell("drive", "Storage", `${Math.round(status.disk_free_gb)} GB free`, status.disk_free_gb > 5),
  ];
}

async function loadHome() {
  const status = await run(null, () => api("/api/status"));
  if (!status) return;
  state.status = status;
  showPrivacy(status.privacy.on);
  if (currentTab === "home") renderHome(status);
  showPushState();
}

let volumeTimer = null;
$("#volume").addEventListener("input", (event) => {
  const slider = event.target;
  $("#volume-value").textContent = slider.value;
  setFill(slider);
  clearTimeout(volumeTimer);
  volumeTimer = setTimeout(() => run(null, () => api("/api/volume", { method: "POST", body: { level: Number(slider.value) } })), 250);
});

async function restart(button) {
  const yes = await ask({ title: "Restart Reachy?", body: "Whatever he's doing stops. He's back in about a minute.", yes: "Restart" });
  if (!yes) return;
  await run(button, async () => {
    await api("/api/restart", { method: "POST" });
    toast("Restarting Reachy.");
    setTimeout(loadHome, 1500);
  });
}

$("#restart").addEventListener("click", (event) => restart(event.currentTarget));

setInterval(() => {
  if (currentTab === "home" && !document.hidden) loadHome();
}, 8000);

// Notifications ---------------------------------------------------------------------------------

function pushSupported() {
  return "serviceWorker" in navigator && "PushManager" in window && "Notification" in window;
}

async function showPushState() {
  const toggle = $("#push-on");
  if (!pushSupported()) {
    toggle.disabled = true;
    $("#push-hint").textContent = "Install the app to get notifications.";
    return;
  }
  const registration = await navigator.serviceWorker.ready;
  const subscription = await registration.pushManager.getSubscription();
  // Reachy forgets a phone the push service said was expired; telling it again keeps the two in step.
  if (subscription && !showPushState.synced) {
    showPushState.synced = true;
    api("/api/push/subscribe", { method: "POST", body: subscription.toJSON() }).catch(() => {});
  }
  toggle.setAttribute("aria-checked", String(Boolean(subscription)));
  $("#push-test").hidden = !subscription;
  if (Notification.permission === "denied") $("#push-hint").textContent = "Blocked. Allow notifications for this app in Android settings.";
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
      toast("Notifications off.");
    } else {
      if ((await Notification.requestPermission()) !== "granted") throw new Error("Notifications are blocked for this app.");
      await subscribePush(registration);
      toast("Notifications on.");
    }
    await showPushState();
  })
);

async function subscribePush(registration) {
  const { key } = await api("/api/push/key");
  const subscription = await registration.pushManager.subscribe({ userVisibleOnly: true, applicationServerKey: keyBytes(key) });
  await api("/api/push/subscribe", { method: "POST", body: subscription.toJSON() });
  return subscription;
}

$("#push-test").addEventListener("click", (event) =>
  run(event.currentTarget, async () => {
    const registration = await navigator.serviceWorker.ready;
    let subscription = await registration.pushManager.getSubscription();
    if (!subscription) throw new Error("Turn notifications on first.");
    await api("/api/push/subscribe", { method: "POST", body: subscription.toJSON() });
    let result = await api("/api/push/test", { method: "POST", body: { endpoint: subscription.endpoint } });
    if (result.gone) {
      // The push service retired this phone's subscription; a fresh one replaces it.
      await subscription.unsubscribe();
      subscription = await subscribePush(registration);
      result = await api("/api/push/test", { method: "POST", body: { endpoint: subscription.endpoint } });
    }
    if (result.delivered) toast("Sent. It should arrive in a few seconds.");
    else toast("Google's push service didn't take it. Turn notifications off and on, then try again.", "bad");
  })
);

// People ----------------------------------------------------------------------------------------

function faceById(id) {
  return state.faces.find((face) => face.id === id);
}

function photoUrl(face, photo) {
  return `/api/faces/photo?person_id=${encodeURIComponent(face.id)}&photo=${encodeURIComponent(photo)}`;
}

function avatar(name, faceId, size = "") {
  const face = faceById(faceId);
  const box = el("div", { className: `avatar ${size}` });
  box.style.setProperty("--hue", hue(name || "?"));
  if (face && face.photos.length) box.append(el("img", { src: photoUrl(face, face.photos[face.photos.length - 1]), alt: "", loading: "lazy" }));
  else box.textContent = (name || "?").trim().slice(0, 1).toUpperCase() || "?";
  return box;
}

function takePeople(payload) {
  state.people = payload.people;
  state.versions = payload.versions;
  state.shared = payload.shared;
  state.faces = payload.faces || [];
  state.facesAvailable = payload.faces !== null;
}

async function loadPeople() {
  const payload = await run(null, () => api("/api/people"));
  if (!payload) return;
  takePeople(payload);
  renderPeople();
  loadGroups();
}

async function loadGroups() {
  try {
    const payload = await api("/api/groups");
    // WhatsApp can take a moment to list a group just created here.
    const created = state.created.filter((group) => !payload.groups.some((g) => g.id === group.id));
    state.groups = [...payload.groups, ...created];
    state.number = payload.number || "";
    state.groupsError = "";
  } catch (error) {
    state.groups = state.groups || [];
    state.groupsError = error.message;
  }
  if (state.draft) renderGroups();
}

function renderPeople() {
  const list = $("#people");
  const sorted = state.people.map((person, index) => ({ person, index }))
    .sort((a, b) => a.person.name.localeCompare(b.person.name, undefined, { sensitivity: "base" }));
  list.replaceChildren(...sorted.map(({ person, index }) => {
    const cap = (on, iconName, label) => el("span", { className: `cap ${on ? "on" : ""}`, title: label, ariaLabel: label, role: "img" }, icon(iconName));
    const tile = el("button", { className: "tile", type: "button" }, [
      avatar(person.name, person.face),
      el("span", { className: "name" }, person.name),
      el("span", { className: "caps" }, [
        cap(person.face, "face", person.face ? "Knows their face" : "Face not added"),
        cap(person.phone, "phone", person.phone ? "WhatsApp calls" : "No calls"),
        cap(person.chat, "chat", person.chat ? "Gets voice notes and photos" : "No voice notes"),
      ]),
    ]);
    tile.addEventListener("click", () => openPerson(index));
    return el("li", {}, tile);
  }));
  if (!state.people.length) {
    list.replaceChildren(el("li", { className: "empty" }, [icon("people"), el("p", {}, "Add the people Reachy should know.")]));
  }
  const notice = $("#shared-names");
  notice.hidden = !state.shared.length;
  notice.replaceChildren(icon("alert"), el("span", {}, state.shared.map((s) =>
    `“${s.said}” could mean ${s.people.join(" or ")}, so Reachy won't know who you mean.`).join(" ")));
}

function openPerson(index) {
  const person = index === null ? { name: "", say: [], chat: "", phone: "", face: "" } : state.people[index];
  state.editing = index;
  state.draft = { ...person, say: [...person.say] };
  state.creatingGroup = false;
  const form = $("#person-form");
  form.name.value = person.name;
  form.phone.value = person.phone;
  $("#person-title").textContent = index === null ? "New person" : "";
  $("#person-delete").hidden = index === null;
  $("#person-delete").querySelector("span").textContent = index === null ? "Remove" : `Remove ${person.name}`;
  $("#person-error").hidden = true;
  $("#face-note").hidden = true;
  renderDraft();
  showPersonMemories(person);
  $("#person-sheet").showModal();
  $(".sheet-body").scrollTop = 0;
  if (index === null) form.name.focus();
  loadGroups();
}

function renderDraft() {
  $("#person-avatar").replaceWith(Object.assign(avatar(state.draft.name, state.draft.face, "xl"), { id: "person-avatar" }));
  renderFace();
  renderGroups();
  renderSay();
}

function faceOnlyCards() {
  return state.people.filter((p, i) => i !== state.editing && p.face && !p.chat && !p.phone);
}

function renderFace() {
  const draft = state.draft;
  const strip = $("#face-strip");
  $("#face-off").hidden = state.facesAvailable;
  strip.hidden = !state.facesAvailable;
  const face = faceById(draft.face);
  const photos = face ? face.photos.map((photo) => {
    const remove = el("button", { type: "button", ariaLabel: "Delete this photo" }, icon("x"));
    remove.addEventListener("click", async () => {
      if (!(await ask({ title: "Delete this photo?", yes: "Delete", danger: true }))) return;
      run(remove, async () => {
        const result = await api("/api/faces/forget-photo", { method: "POST", body: { person_id: face.id, photo } });
        if (!result.person) draft.face = "";
        await refreshFaces();
      });
    });
    return el("div", { className: "face" }, [el("img", { src: photoUrl(face, photo), alt: "" }), remove]);
  }) : [];
  const upload = el("button", { className: "add-face", type: "button" }, [icon("image"), "Photos"]);
  upload.addEventListener("click", () => $("#face-upload").click());
  const camera = el("button", { className: "add-face", type: "button" }, [icon("camera"), "Reachy's camera"]);
  camera.addEventListener("click", () => enrollFromCamera(camera));
  strip.replaceChildren(...photos, upload, camera);

  const candidates = draft.face ? [] : faceOnlyCards();
  $("#face-link").hidden = !state.facesAvailable || !candidates.length;
  $("#face-link-chips").replaceChildren(...candidates.map((card) => {
    const chip = el("button", { className: "chip", type: "button" }, [avatar(card.name, card.face, "sm"), card.name]);
    chip.addEventListener("click", () => {
      draft.face = card.face;
      draft.linkedFrom = card.name;
      if (!draft.name.trim()) {
        draft.name = card.name;
        $("#person-form").name.value = card.name;
      }
      renderDraft();
    });
    return chip;
  }));
}

async function refreshFaces() {
  const payload = await api("/api/people");
  state.faces = payload.faces || [];
  state.facesAvailable = payload.faces !== null;
  state.versions = payload.versions;
  state.people = payload.people;
  if (state.editing !== null && state.people[state.editing] === undefined) state.editing = null;
  renderDraft();
}

function faceName() {
  const face = faceById(state.draft.face);
  const name = face ? face.name : state.draft.name.trim();
  if (!name) throw new Error("Type their name first.");
  return name;
}

// Cropping: the box is kept as fractions of the photo, so it maps straight onto the full-size image.
const crop = { box: null, resolve: null, drag: null, url: null, anyway: false };
const CROP_MIN = 0.08;

function placeCropBox() {
  const box = $("#crop-box");
  const { x, y, w, h } = crop.box;
  Object.assign(box.style, { left: `${x * 100}%`, top: `${y * 100}%`, width: `${w * 100}%`, height: `${h * 100}%` });
}

function loadCropImage(file) {
  return new Promise((resolve, reject) => {
    const image = $("#crop-image");
    if (crop.url) URL.revokeObjectURL(crop.url);
    crop.url = URL.createObjectURL(file);
    image.onload = () => {
      // A centred square a little over half the shorter side, as a starting point around one face.
      const side = 0.6 * Math.min(image.naturalWidth, image.naturalHeight);
      const w = side / image.naturalWidth;
      const h = side / image.naturalHeight;
      crop.box = { x: (1 - w) / 2, y: (1 - h) / 2, w, h };
      placeCropBox();
      resolve();
    };
    image.onerror = () => reject(new Error("That photo couldn't be read."));
    image.src = crop.url;
  });
}

function showCropProblem(text) {
  const problem = $("#crop-problem");
  problem.textContent = text || "";
  problem.hidden = !text;
  $("#crop-use").textContent = crop.anyway ? "Use anyway" : "Use this";
}

// Shows one photo to crop; resolves with its JPEG once Reachy says it would work, or with "skip" or "cancel".
function cropPhoto(file, index, total, name) {
  return new Promise((resolve, reject) => {
    crop.anyway = false;
    showCropProblem("");
    $("#crop-title").textContent = total > 1 ? `Photo ${index + 1} of ${total}` : "";
    loadCropImage(file).catch(() => showCropProblem("This photo couldn't be read. Pick another one."));
    crop.resolve = async (choice) => {
      if (choice === "skip" || choice === "cancel") {
        crop.resolve = null;
        resolve(choice);
        return;
      }
      if (!$("#crop-image").naturalWidth) return;
      const jpeg = cropToJpeg($("#crop-image"), choice === "whole" ? null : crop.box);
      const verdict = crop.anyway ? { ok: true } : await checkFacePhoto(name, jpeg);
      if (verdict.ok) {
        crop.resolve = null;
        resolve(jpeg);
        return;
      }
      crop.anyway = verdict.problem === "someone_else";
      showCropProblem(CROP_PROBLEMS[verdict.problem]?.(verdict.detail, name) || "Reachy can't use this one. Try another photo.");
    };
    if (!$("#crop-sheet").open) $("#crop-sheet").showModal();
  });
}

const CROP_PROBLEMS = {
  no_face: () => "Reachy can't see a whole face here. Make the box bigger, or pick another photo.",
  several_faces: () => "There's more than one face in the box. Make it smaller, around just them.",
  unreadable: () => "This photo couldn't be read. Pick another one.",
  someone_else: (other, name) => `This looks like ${other}, not ${name}. Move the box, or use it anyway.`,
};

async function checkFacePhoto(name, jpeg) {
  const button = $("#crop-use");
  const label = button.textContent;
  for (const each of document.querySelectorAll(".crop-actions button")) each.disabled = true;
  button.textContent = "Checking…";
  try {
    return await api("/api/faces/check", { method: "POST", body: { name, photo: jpeg } });
  } catch (error) {
    return { ok: true }; // can't check now: the upload checks again
  } finally {
    for (const each of document.querySelectorAll(".crop-actions button")) each.disabled = false;
    button.textContent = label;
  }
}

$("#crop-replace").addEventListener("change", (event) => {
  const [file] = event.target.files;
  event.target.value = "";
  if (!file) return;
  crop.anyway = false;
  showCropProblem("");
  loadCropImage(file).catch(() => showCropProblem("This photo couldn't be read. Pick another one."));
});
$("#crop-another").addEventListener("click", () => $("#crop-replace").click());

// Reachy refuses a face that touches the photo's edge, so a box drawn tight around a face gets some room added.
const CROP_ROOM = 0.15;

function cropToJpeg(image, box) {
  let area = { x: 0, y: 0, w: 1, h: 1 };
  if (box) {
    const x = Math.max(0, box.x - box.w * CROP_ROOM);
    const y = Math.max(0, box.y - box.h * CROP_ROOM);
    area = { x, y, w: Math.min(1, box.x + box.w * (1 + CROP_ROOM)) - x, h: Math.min(1, box.y + box.h * (1 + CROP_ROOM)) - y };
  }
  const sx = area.x * image.naturalWidth;
  const sy = area.y * image.naturalHeight;
  const sw = area.w * image.naturalWidth;
  const sh = area.h * image.naturalHeight;
  // Small crops are enlarged a little so the face finder has enough to work with.
  const scale = Math.min(1280 / Math.max(sw, sh), Math.max(1, 480 / Math.max(sw, sh)));
  const canvas = document.createElement("canvas");
  canvas.width = Math.round(sw * scale);
  canvas.height = Math.round(sh * scale);
  const context = canvas.getContext("2d");
  context.imageSmoothingQuality = "high";
  context.drawImage(image, sx, sy, sw, sh, 0, 0, canvas.width, canvas.height);
  return canvas.toDataURL("image/jpeg", 0.9).split(",")[1];
}

$("#crop-use").addEventListener("click", () => crop.resolve?.("box"));
$("#crop-whole").addEventListener("click", () => crop.resolve?.("whole"));
$("#crop-skip").addEventListener("click", () => crop.resolve?.("skip"));
$("#crop-cancel").addEventListener("click", () => crop.resolve?.("cancel"));
$("#crop-sheet").addEventListener("cancel", (event) => {
  event.preventDefault();
  crop.resolve?.("cancel");
});

$("#crop-frame").addEventListener("pointerdown", (event) => {
  const handle = event.target.closest("[data-handle]");
  if (!handle && !event.target.closest("#crop-box")) return;
  event.preventDefault();
  const frame = $("#crop-frame").getBoundingClientRect();
  crop.drag = { handle: handle ? handle.dataset.handle : "move", x: event.clientX, y: event.clientY, start: { ...crop.box }, frame };
  $("#crop-frame").setPointerCapture(event.pointerId);
});

$("#crop-frame").addEventListener("pointermove", (event) => {
  const drag = crop.drag;
  if (!drag) return;
  const dx = (event.clientX - drag.x) / drag.frame.width;
  const dy = (event.clientY - drag.y) / drag.frame.height;
  const s = drag.start;
  let { x, y, w, h } = s;
  if (drag.handle === "move") {
    x = Math.min(Math.max(0, s.x + dx), 1 - s.w);
    y = Math.min(Math.max(0, s.y + dy), 1 - s.h);
  } else {
    const right = s.x + s.w;
    const bottom = s.y + s.h;
    if (drag.handle.includes("w")) x = Math.min(Math.max(0, s.x + dx), right - CROP_MIN);
    if (drag.handle.includes("n")) y = Math.min(Math.max(0, s.y + dy), bottom - CROP_MIN);
    w = drag.handle.includes("w") ? right - x : Math.min(Math.max(CROP_MIN, s.w + dx), 1 - s.x);
    h = drag.handle.includes("n") ? bottom - y : Math.min(Math.max(CROP_MIN, s.h + dy), 1 - s.y);
  }
  crop.box = { x, y, w, h };
  placeCropBox();
  if (crop.anyway) {
    crop.anyway = false;
    showCropProblem("");
  }
});

for (const name of ["pointerup", "pointercancel"]) {
  $("#crop-frame").addEventListener(name, () => (crop.drag = null));
}

$("#crop-box").addEventListener("keydown", (event) => {
  const step = event.shiftKey ? 0.05 : 0.01;
  const moves = { ArrowLeft: [-step, 0], ArrowRight: [step, 0], ArrowUp: [0, -step], ArrowDown: [0, step] };
  if (!moves[event.key]) return;
  event.preventDefault();
  const [dx, dy] = moves[event.key];
  const b = crop.box;
  crop.box = { ...b, x: Math.min(Math.max(0, b.x + dx), 1 - b.w), y: Math.min(Math.max(0, b.y + dy), 1 - b.h) };
  placeCropBox();
});

$("#face-upload").addEventListener("change", (event) => {
  const files = [...event.target.files].slice(0, 6);
  event.target.value = "";
  if (!files.length) return;
  run(null, async () => {
    const name = faceName();
    const photos = [];
    try {
      for (const [index, file] of files.entries()) {
        const choice = await cropPhoto(file, index, files.length, name);
        if (choice === "cancel") return;
        if (choice !== "skip") photos.push(choice);
      }
    } finally {
      $("#crop-sheet").close();
    }
    if (!photos.length) return;
    const note = $("#face-note");
    note.hidden = true;
    toast(photos.length > 1 ? `Adding ${photos.length} photos…` : "Adding the photo…");
    let result;
    try {
      result = await api("/api/faces/upload", { method: "POST", body: { name, photos } });
    } catch (error) {
      note.className = "face-note bad";
      note.replaceChildren(faceProblem(error.message, photos.length));
      note.hidden = false;
      $("#toast").hidePopover?.();
      return;
    }
    state.draft.face = result.person.id;
    await refreshFaces();
    const skipped = result.skipped || [];
    const added = photos.length - skipped.length;
    toast(added === 1 ? "Photo added." : `${added} photos added.`);
    if (skipped.length) {
      note.className = "face-note";
      note.replaceChildren(skipped.map((reason) => faceProblem(reason, photos.length)).join(" "));
      note.hidden = false;
    }
  });
});

// Reachy's reasons, said the way a person would ("photo 2 has more than one face" and so on).
function faceProblem(reason, total) {
  const text = String(reason || "");
  if (/; /.test(text)) return text.split("; ").map((part) => faceProblem(part, total)).join(" ");
  const photo = text.match(/photo (\d+)/i);
  const which = photo && total > 1 ? `Photo ${photo[1]}` : "The photo";
  if (/uncropped|no clear/i.test(text)) return `${which} wasn't added: Reachy couldn't see a whole face. Give their head a bit more room.`;
  if (/more than one face/i.test(text)) return `${which} wasn't added: it shows more than one face. Crop to just them.`;
  if (/same person/i.test(text)) return `${which} wasn't added: it doesn't look like the same person.`;
  if (/different people/i.test(text)) return "Nothing was added: the photos seem to show different people.";
  if (/already enrolled as/i.test(text)) return `Nothing was added: this face already belongs to ${text.split("as ").pop()}.`;
  if (/could not be read/i.test(text)) return `${which} couldn't be read.`;
  return `Nothing was added: ${text}`;
}

function enrollFromCamera(button) {
  run(button, async () => {
    const name = faceName();
    toast(`Ask ${state.draft.name.trim() || name} to look at Reachy.`);
    const result = await api("/api/faces/enroll", { method: "POST", body: { name } });
    state.draft.face = result.person.id;
    await refreshFaces();
    toast("Face added.");
  });
}

function groupOption({ checked, badge, label, detail, onPick, add = false }) {
  const option = el("button", { className: `option ${add ? "add" : ""}`, type: "button", role: add ? "button" : "radio" }, [
    el("span", { className: "group-badge" }, icon(badge)),
    el("span", { className: "label" }, [label, detail ? el("small", {}, detail) : null]),
    add ? null : el("span", { className: "tick" }, icon("check")),
  ]);
  if (!add) option.setAttribute("aria-checked", String(checked));
  option.addEventListener("click", onPick);
  return option;
}

function renderGroups() {
  const draft = state.draft;
  const pick = (chat) => () => {
    draft.chat = chat;
    renderGroups();
  };
  const options = [groupOption({ checked: !draft.chat, badge: "x", label: "Nowhere", onPick: pick("") })];
  for (const group of state.groups || []) {
    options.push(groupOption({ checked: draft.chat === group.id, badge: "chat", label: group.name, onPick: pick(group.id) }));
  }
  if (draft.chat && state.groups && !state.groups.some((g) => g.id === draft.chat)) {
    options.push(groupOption({ checked: true, badge: "chat", label: "A group Reachy left", detail: "Pick another one", onPick: pick(draft.chat) }));
  }
  const canCreate = state.groups !== null && !state.groupsError && digits(draft.phone).length >= 7;
  if (canCreate) {
    options.push(groupOption({
      badge: "plus", label: `New group with ${draft.name.trim() || "them"}`, add: true,
      onPick: () => {
        state.creatingGroup = true;
        $("#new-group-name").value = `Reachy & ${draft.name.trim() || "family"}`;
        renderGroups();
        $("#new-group-name").focus();
      },
    }));
  }
  $("#group-list").replaceChildren(...options);
  $("#new-group").hidden = !(state.creatingGroup && canCreate);

  const help = $("#group-help");
  if (state.groupsError) {
    help.replaceChildren(state.groupsError);
  } else if (state.groups === null) {
    help.replaceChildren("Loading groups…");
  } else if (state.number) {
    const copy = el("button", { className: "copy", type: "button" }, state.number);
    copy.addEventListener("click", () => navigator.clipboard.writeText(state.number).then(() => toast("Number copied.")).catch(() => {}));
    help.replaceChildren(canCreate ? "Or add Reachy to any WhatsApp group: " : "Add Reachy to a WhatsApp group to see it here: ", copy);
  } else {
    help.replaceChildren(canCreate ? "" : "Add their number to start a new group with them.");
  }
}

$("#new-group-create").addEventListener("click", (event) =>
  run(event.currentTarget, async () => {
    const title = $("#new-group-name").value.trim();
    if (!title) throw new Error("Give the group a name.");
    const group = await api("/api/groups", { method: "POST", body: { title, phones: [state.draft.phone] } });
    state.creatingGroup = false;
    state.draft.chat = group.id;
    state.created.push(group);
    state.groups = [...(state.groups || []), group];
    renderGroups();
    toast(`Created “${group.name}”.`);
    loadGroups();
  })
);

function renderSay() {
  const box = $("#say-chips");
  const input = $("#say-input");
  box.replaceChildren(...state.draft.say.map((said, index) => {
    const remove = el("button", { className: "remove", type: "button", ariaLabel: `Remove ${said}` }, icon("x"));
    remove.addEventListener("click", () => {
      state.draft.say.splice(index, 1);
      renderSay();
    });
    return el("span", { className: "chip" }, [said, remove]);
  }), input);
}

function addSaid() {
  const input = $("#say-input");
  for (const part of input.value.split(",")) {
    const said = part.trim();
    if (said && !state.draft.say.some((s) => s.toLowerCase() === said.toLowerCase())) state.draft.say.push(said);
  }
  input.value = "";
  renderSay();
  input.focus();
}

$("#say-input").addEventListener("keydown", (event) => {
  if (event.key === "Enter" || event.key === ",") {
    event.preventDefault();
    addSaid();
  } else if (event.key === "Backspace" && !event.target.value && state.draft.say.length) {
    state.draft.say.pop();
    renderSay();
    $("#say-input").focus();
  }
});
$("#say-input").addEventListener("input", (event) => {
  if (event.target.value.includes(",")) addSaid();
});
$("#say-input").addEventListener("blur", () => {
  if ($("#say-input").value.trim()) addSaid();
});

$("#person-form").name.addEventListener("input", (event) => {
  state.draft.name = event.target.value;
  const current = $("#person-avatar");
  if (!faceById(state.draft.face)) {
    current.textContent = event.target.value.trim().slice(0, 1).toUpperCase() || "?";
    current.style.setProperty("--hue", hue(event.target.value || "?"));
  }
  renderGroups();
});
$("#person-form").phone.addEventListener("input", (event) => {
  state.draft.phone = event.target.value;
  renderGroups();
});

async function savePeople(people) {
  takePeople(await api("/api/people", { method: "PUT", body: { people, versions: state.versions } }));
  renderPeople();
}

$("#person-form").addEventListener("submit", (event) => {
  event.preventDefault();
  const draft = state.draft;
  if ($("#say-input").value.trim()) addSaid();
  const person = { name: draft.name.trim(), say: draft.say, chat: draft.chat, phone: draft.phone.trim(), face: draft.face };
  const error = $("#person-error");
  if (!person.name) {
    error.textContent = "Add their name.";
    error.hidden = false;
    $("#person-form").name.focus();
    return;
  }
  let people = state.people.map((p, i) => (i === state.editing ? person : p));
  if (state.editing === null) people.push(person);
  // A face picked from a face-only card moves here, so that card goes.
  people = people.filter((p) => p === person || !(p.face === person.face && person.face && !p.chat && !p.phone && !p.say.length));
  run($("#person-save"), async () => {
    try {
      await savePeople(people);
      $("#person-sheet").close();
      toast(`${person.name} saved.`);
    } catch (problem) {
      error.textContent = problem.message;
      error.hidden = false;
    }
  });
});

$("#person-cancel").addEventListener("click", () => $("#person-sheet").close());
$("#person-sheet").addEventListener("close", () => (state.draft = null));
$("#add-person").addEventListener("click", () => openPerson(null));

$("#person-delete").addEventListener("click", async (event) => {
  const person = state.people[state.editing];
  const face = faceById(person.face);
  const yes = await ask({
    title: `Remove ${person.name}?`,
    body: face ? "Reachy forgets their face, number and group." : "Reachy won't message or call them any more.",
    yes: "Remove",
    danger: true,
  });
  if (!yes) return;
  const button = event.currentTarget;
  run(button, async () => {
    if (face) await api("/api/faces/forget", { method: "POST", body: { person_id: face.id } });
    const fresh = await api("/api/people");
    takePeople(fresh);
    await savePeople(state.people.filter((p) => p.name !== person.name && !(face && p.face === face.id)));
    $("#person-sheet").close();
    toast(`${person.name} removed.`);
  });
});

// Camera ----------------------------------------------------------------------------------------

async function openCamera() {
  const image = $("#camera");
  const note = $("#camera-note");
  const showNote = (message) => {
    closeCamera();
    note.querySelector("p").textContent = message;
    note.hidden = false;
    $("#live").hidden = true;
  };
  note.hidden = true;
  let status;
  try {
    status = await api("/api/status");
  } catch (error) {
    showNote(error.message);
    return;
  }
  if (currentTab !== "camera") return;
  state.status = status;
  showPrivacy(status.privacy.on);
  const asleep = status.app.running && status.app.sleeping;
  $("#look-up").hidden = !asleep || status.privacy.on;
  $("#camera-hint").hidden = !asleep || status.privacy.on;
  for (const button of document.querySelectorAll("[data-head]")) button.disabled = !status.app.running || status.privacy.on;
  if (!status.app.running) return showNote("Reachy isn't running.");
  if (status.privacy.on) return showNote("Privacy is on, so his camera is off.");
  image.onerror = () => showNote("The camera stopped. Tap to try again.");
  image.onload = () => ($("#live").hidden = false);
  image.src = `/api/camera?t=${Date.now()}`;
  $("#live").hidden = false;
}

function closeCamera() {
  const image = $("#camera");
  image.onerror = null;
  image.onload = null;
  image.removeAttribute("src");
}

$("#camera-note").addEventListener("click", () => openCamera());

for (const button of document.querySelectorAll("[data-head]")) {
  button.addEventListener("click", () =>
    run(null, () => api("/api/head", { method: "POST", body: { direction: button.dataset.head } }))
  );
}

$("#look-up").addEventListener("click", (event) =>
  run(event.currentTarget, async () => {
    await api("/api/look-up", { method: "POST" });
    toast("Head up for two minutes.");
  })
);

document.addEventListener("visibilitychange", () => {
  if (currentTab === "camera") {
    if (document.hidden) closeCamera();
    else openCamera();
  }
  if (currentTab === "home" && !document.hidden) loadHome();
  if (currentTab === "gallery" && !document.hidden && !$("#viewer").open) loadGallery();
});

// Activity --------------------------------------------------------------------------------------

const OUTCOMES = {
  declined_privacy: "Declined, privacy was on",
  declined_quiet_hours: "Declined, quiet hours",
  declined: "Declined",
  failed: "Didn't go through",
  no_answer: "No answer",
};

function minutes(seconds) {
  if (!seconds) return "";
  const whole = Math.round(seconds / 60);
  return whole < 1 ? "Under a minute" : `${whole} min`;
}

function describeActivity(entry) {
  const who = entry.who || "Someone";
  const detail = OUTCOMES[entry.outcome] || minutes(entry.seconds);
  switch (entry.kind) {
    case "call_out":
      return { badge: "call", icon: "phone-out", title: `Called ${who}`, detail };
    case "call_in":
      if (entry.outcome === "missed") return { badge: "missed", icon: "phone-missed", title: `Missed call from ${who}`, detail: "" };
      return { badge: "call", icon: "phone-in", title: `${who} called`, detail };
    case "voice_note":
      return { badge: "note", icon: "mic", title: `Voice note to ${who}`, detail: entry.outcome === "failed" ? OUTCOMES.failed : "" };
    case "photo":
      return { badge: "photo", icon: "image", title: `Photo to ${who}`, detail: entry.outcome === "failed" ? OUTCOMES.failed : "" };
    default:
      return { badge: "", icon: "spark", title: `${entry.kind} ${entry.who || ""}`.trim(), detail };
  }
}

function personFor(login) {
  const local = String(login || "").split("@")[0].toLowerCase();
  if (state.me && state.me.login === login) return firstName(state.me.name) || "You";
  const match = state.people.find((p) => {
    const first = firstName(p.name).toLowerCase().normalize("NFD").replace(/[̀-ͯ]/g, "");
    return first.length >= 3 && local.startsWith(first);
  });
  return match ? firstName(match.name) : local;
}

const CHANGES = {
  "privacy on": () => "turned privacy on",
  "privacy off": () => "turned privacy off",
  "put to sleep": () => "put Reachy to sleep",
  "woken up": () => "woke Reachy up",
  "volume set": (t) => `set the volume to ${t}`,
  "restarted Reachy": () => "restarted Reachy",
  "people saved": () => "updated people",
  "settings changed": () => "changed settings",
  "group created": (t) => `created the group “${t}”`,
  "face photos added": (t) => (t ? `added face photos of ${t}` : "added face photos"),
  "face enrolled from the camera": (t) => (t ? `added ${t}'s face from the camera` : "added a face from the camera"),
  "face photo deleted": () => "deleted a face photo",
  "face forgotten": (t) => (t ? `made Reachy forget ${t}` : "made Reachy forget a face"),
  "personality changed": () => "changed Reachy's personality",
  "personality reset": () => "put Reachy's personality back to the original",
  "memory corrected": (t) => (t ? `corrected something Reachy remembers about ${subjectName(t)}` : "corrected a memory"),
  "memory forgotten": (t) => (t ? `made Reachy forget something about ${subjectName(t)}` : "made Reachy forget something"),
  "picture deleted": (t) => (t === "photos" ? "deleted a photo" : "deleted a picture"),
};

function describeChange(entry) {
  const phrase = (CHANGES[entry.action] || ((t) => `${entry.action}${t ? ` ${t}` : ""}`))(entry.target || "");
  return { badge: "", icon: "settings", title: `${personFor(entry.who)} ${phrase}`, detail: "" };
}

function dayLabel(date) {
  const today = new Date();
  const yesterday = new Date(Date.now() - 86400000);
  if (date.toDateString() === today.toDateString()) return "Today";
  if (date.toDateString() === yesterday.toDateString()) return "Yesterday";
  return date.toLocaleDateString(undefined, { weekday: "long", day: "numeric", month: "long" });
}

function renderLog(entries, describe, emptyText) {
  const box = $("#log");
  if (!entries.length) {
    box.replaceChildren(el("div", { className: "empty" }, [icon("activity"), el("p", {}, emptyText)]));
    return;
  }
  const days = [];
  for (const entry of entries) {
    const date = when(entry.at) || new Date();
    const label = dayLabel(date);
    if (!days.length || days[days.length - 1].label !== label) days.push({ label, items: [] });
    const text = describe(entry);
    days[days.length - 1].items.push(el("li", { className: "event" }, [
      el("span", { className: `badge ${text.badge}` }, icon(text.icon)),
      el("span", { className: "event-text" }, [el("b", {}, text.title), text.detail ? el("span", {}, text.detail) : null]),
      el("time", { dateTime: date.toISOString() }, clock(entry.at)),
    ]));
  }
  box.replaceChildren(...days.flatMap((day) => [el("h3", { className: "day" }, day.label), el("ul", { className: "events" }, day.items)]));
}

async function loadActivity() {
  const [payload] = await Promise.all([
    run(null, () => api("/api/activity")),
    state.people.length ? null : api("/api/people").then(takePeople).catch(() => {}),
  ]);
  if (!payload) return;
  state.activity = payload;
  renderActivity();
}

function renderActivity() {
  const payload = state.activity;
  for (const button of document.querySelectorAll("[data-log]")) button.setAttribute("aria-selected", String(button.dataset.log === state.log));
  const totals = (payload.usage && payload.usage.totals) || {};
  const usage = $("#usage");
  usage.hidden = state.log !== "activity" || totals.today === undefined;
  if (!usage.hidden) {
    const cost = (value, label) => el("div", { className: "cost" }, [el("b", {}, `$${Number(value || 0).toFixed(2)}`), el("span", {}, label)]);
    usage.replaceChildren(
      el("div", { className: "cost-row" }, [cost(totals.today, "AI today"), cost(totals.last_7_days, "This week"), cost(totals.last_30_days, "This month")]),
      el("span", { className: "more" }, ["See where it goes", icon("chevron")]),
    );
  }
  if (state.log === "activity") renderLog(payload.activity, describeActivity, "Calls, voice notes and photos show up here.");
  else renderLog(payload.changes, describeChange, "Changes made in this app show up here.");
}

for (const button of document.querySelectorAll("[data-log]")) {
  button.addEventListener("click", () => {
    state.log = button.dataset.log;
    if (state.activity) renderActivity();
  });
}

// Gallery ---------------------------------------------------------------------------------------

const KINDS = {
  photo: { label: "Photos", one: "Photo", icon: "camera" },
  drawing: { label: "Drawings", one: "Drawing", icon: "pencil" },
  lesson: { label: "Lessons", one: "Drawing lesson", icon: "steps" },
  picture: { label: "Pictures", one: "Picture from the web", icon: "globe" },
};
const gallery = { items: [], kind: "all", query: "", shown: [], index: 0 };

function fileUrl(item, thumb = false) {
  return `/api/gallery/file?id=${encodeURIComponent(item.id)}${thumb ? "&thumb=1" : ""}`;
}

function fold(text) {
  return String(text || "").toLowerCase().normalize("NFD").replace(/[̀-ͯ]/g, "");
}

async function loadGallery() {
  const payload = await run(null, () => api("/api/gallery"));
  if (!payload) return;
  gallery.items = payload.items;
  renderGallery();
}

function matches(item) {
  if (gallery.kind !== "all" && item.kind !== gallery.kind) return false;
  if (!gallery.query) return true;
  const haystack = fold([item.title, item.who, item.edit, KINDS[item.kind].label, ...(item.steps || [])].join(" "));
  return fold(gallery.query).split(/\s+/).every((word) => haystack.includes(word));
}

function renderGallery() {
  const counts = {};
  for (const item of gallery.items) counts[item.kind] = (counts[item.kind] || 0) + 1;
  const filter = (kind, label, count) => {
    const chip = el("button", { className: "chip", type: "button", role: "tab" }, [label, el("span", {}, String(count))]);
    chip.setAttribute("aria-selected", String(gallery.kind === kind));
    chip.addEventListener("click", () => {
      gallery.kind = kind;
      renderGallery();
    });
    return chip;
  };
  $("#gallery-filters").replaceChildren(
    filter("all", "All", gallery.items.length),
    ...Object.entries(KINDS).filter(([kind]) => counts[kind]).map(([kind, info]) => filter(kind, info.label, counts[kind])),
  );

  gallery.shown = gallery.items.filter(matches);
  const box = $("#gallery");
  if (!gallery.shown.length) {
    const text = gallery.query
      ? `Nothing matches “${gallery.query}”.`
      : gallery.kind === "photo" ? "Photos Reachy takes show up here." : "Pictures Reachy makes show up here.";
    box.replaceChildren(el("div", { className: "empty" }, [icon(gallery.query ? "search" : "gallery"), el("p", {}, text)]));
    return;
  }
  const days = [];
  gallery.shown.forEach((item, index) => {
    const label = dayLabel(when(item.at) || new Date());
    if (!days.length || days[days.length - 1].label !== label) days.push({ label, tiles: [] });
    const image = el("img", { src: fileUrl(item, true), alt: item.title, loading: "lazy", decoding: "async" });
    image.setAttribute("data-loading", "");
    image.addEventListener("load", () => image.removeAttribute("data-loading"));
    const tile = el("button", { className: "shot", type: "button", ariaLabel: item.title }, [
      image,
      gallery.kind === "all" ? el("span", { className: "kind" }, icon(KINDS[item.kind].icon)) : null,
    ]);
    tile.addEventListener("click", () => openViewer(index));
    days[days.length - 1].tiles.push(tile);
  });
  box.replaceChildren(...days.flatMap((day) => [el("h3", { className: "day" }, day.label), el("div", { className: "grid" }, day.tiles)]));
}

let searchTimer = null;
$("#gallery-search").addEventListener("input", (event) => {
  clearTimeout(searchTimer);
  searchTimer = setTimeout(() => {
    gallery.query = event.target.value.trim();
    renderGallery();
  }, 150);
});

function openViewer(index) {
  gallery.index = index;
  showViewerItem();
  if (!$("#viewer").open) $("#viewer").showModal();
}

function showViewerItem() {
  const item = gallery.shown[gallery.index];
  if (!item) return;
  const image = $("#viewer-image");
  image.src = fileUrl(item);
  image.alt = item.title;
  image.classList.toggle("ink", item.kind !== "photo");
  image.style.transform = "";
  $("#viewer-title").textContent = item.title;
  const date = when(item.at);
  const day = date ? dayLabel(date) : "";
  const meta = $("#viewer-meta");
  meta.replaceChildren(
    item.kind !== "photo" ? KINDS[item.kind].one : item.edit ? "Photo edit" : "Taken",
    date ? `, ${["Today", "Yesterday"].includes(day) ? day.toLowerCase() : day} at ${clock(item.at)}` : "",
  );
  if (item.source) meta.append(". ", el("a", { className: "source", href: item.source, target: "_blank", rel: "noopener noreferrer" }, "Where it's from"));
  if (item.old_style) meta.append(". An older drawing style");
  const steps = $("#viewer-steps");
  steps.hidden = !(item.steps && item.steps.length);
  steps.replaceChildren(...(item.steps || []).map((step) => el("li", {}, step)));
  $("#viewer-count").textContent = `${gallery.index + 1} of ${gallery.shown.length}`;
  const save = $("#viewer-save");
  save.href = fileUrl(item);
  save.download = `${item.title}.${item.id.endsWith(".jpg") ? "jpg" : "png"}`;
  $("#viewer-prev").hidden = gallery.index === 0;
  $("#viewer-next").hidden = gallery.index >= gallery.shown.length - 1;
  for (const offset of [-1, 1]) {
    const neighbour = gallery.shown[gallery.index + offset];
    if (neighbour) new Image().src = fileUrl(neighbour);
  }
}

function stepViewer(offset) {
  const next = gallery.index + offset;
  if (next < 0 || next >= gallery.shown.length) return;
  gallery.index = next;
  showViewerItem();
}

$("#viewer-close").addEventListener("click", () => $("#viewer").close());
$("#viewer-prev").addEventListener("click", () => stepViewer(-1));
$("#viewer-next").addEventListener("click", () => stepViewer(1));
$("#viewer").addEventListener("keydown", (event) => {
  if (event.key === "ArrowLeft") stepViewer(-1);
  if (event.key === "ArrowRight") stepViewer(1);
});

let swipe = null;
$("#viewer-stage").addEventListener("pointerdown", (event) => {
  if (event.target.closest("button")) return;
  swipe = { x: event.clientX, y: event.clientY };
});
$("#viewer-stage").addEventListener("pointermove", (event) => {
  if (!swipe) return;
  $("#viewer-image").style.transform = `translateX(${(event.clientX - swipe.x) * 0.6}px)`;
});
for (const name of ["pointerup", "pointercancel"]) {
  $("#viewer-stage").addEventListener(name, (event) => {
    if (!swipe) return;
    const dx = event.clientX - swipe.x;
    const dy = event.clientY - swipe.y;
    swipe = null;
    $("#viewer-image").style.transform = "";
    if (Math.abs(dx) > 50 && Math.abs(dx) > Math.abs(dy)) stepViewer(dx < 0 ? 1 : -1);
    else if (dy > 90) $("#viewer").close();
  });
}

$("#viewer-share").addEventListener("click", (event) =>
  run(event.currentTarget, async () => {
    const item = gallery.shown[gallery.index];
    const response = await fetch(fileUrl(item));
    const blob = await response.blob();
    const file = new File([blob], `${item.title}.${blob.type === "image/jpeg" ? "jpg" : "png"}`, { type: blob.type });
    if (navigator.canShare && navigator.canShare({ files: [file] })) {
      try {
        await navigator.share({ files: [file], title: item.title });
      } catch (error) {
        if (error.name !== "AbortError") throw new Error("Sharing didn't work.");
      }
    } else {
      $("#viewer-save").click();
    }
  })
);

$("#viewer-delete").addEventListener("click", async (event) => {
  const item = gallery.shown[gallery.index];
  const button = event.currentTarget;
  const yes = await ask({
    title: item.kind === "photo" ? "Delete this photo?" : `Delete “${item.title}”?`,
    body: item.kind === "photo" ? "It stays in WhatsApp if it was sent." : "Reachy makes it again if someone asks for it.",
    yes: "Delete",
    danger: true,
  });
  if (!yes) return;
  run(button, async () => {
    await api("/api/gallery/delete", { method: "POST", body: { id: item.id } });
    gallery.items = gallery.items.filter((i) => i.id !== item.id);
    renderGallery();
    toast("Deleted.");
    if (!gallery.shown.length) $("#viewer").close();
    else {
      gallery.index = Math.min(gallery.index, gallery.shown.length - 1);
      showViewerItem();
    }
  });
});

// Costs -----------------------------------------------------------------------------------------

const FEATURES = {
  conversation: ["Conversations", "Talking with him"],
  memory_capture: ["Remembering", "Noting what's worth keeping"],
  memory_review: ["Tidying memories", ""],
  quick_reply_speech: ["Quick replies", "Short spoken answers"],
  addressee_check: ["Was that for him?", "Checking who was spoken to"],
  display_drawing: ["Drawings", "For the e-ink screen"],
  display_photo_edit: ["Photo edits", "Pirates, astronauts and friends"],
  display_picture_search: ["Picture search", "Finding pictures on the web"],
  display_picture_check: ["Picture checks", "Making sure pictures are OK for kids"],
  display_lesson: ["Drawing lessons", ""],
  display_lesson_captions: ["Lesson captions", ""],
  game_color_check: ["Color hunt", "Checking the colors shown to his camera"],
};
const PARTS = {
  input_text: ["Reading", "His instructions, memories and what was said"],
  input_audio: ["Hearing", "Listening to the room"],
  input_image: ["Seeing", "Camera frames"],
  output_audio: ["Speaking", "His voice"],
  output_text: ["Writing", "Text he produced"],
  thinking: ["Thinking", "Working things out"],
};
const costs = { days: 7, data: null, picked: null };

function money(value) {
  if (!value) return "$0";
  return value < 0.01 ? "<$0.01" : `$${value.toFixed(2)}`;
}

function openCosts() {
  $("#costs-sheet").showModal();
  loadCosts();
}

async function loadCosts() {
  for (const button of document.querySelectorAll("[data-days]")) button.setAttribute("aria-selected", String(Number(button.dataset.days) === costs.days));
  const data = await run(null, () => api(`/api/costs?days=${costs.days}`));
  if (!data) return;
  costs.data = data;
  costs.picked = null;
  renderCosts();
}

for (const button of document.querySelectorAll("[data-days]")) {
  button.addEventListener("click", () => {
    costs.days = Number(button.dataset.days);
    loadCosts();
  });
}

function share(fraction) {
  const percent = Math.round(fraction * 100);
  return fraction > 0 && percent === 0 ? "<1%" : `${percent}%`;
}

function breakdownList(rows) {
  const top = Math.max(...rows.map((row) => row.cost), 0.0001);
  const total = rows.reduce((sum, row) => sum + row.cost, 0) || 1;
  return el("ul", { className: "breakdown" }, rows.map((row) => {
    const meter = el("span", { className: "meter" }, el("i"));
    meter.firstChild.style.width = `${Math.max(1, (row.cost / top) * 100)}%`;
    return el("li", {}, [
      el("span", { className: "what" }, [row.label, row.detail ? el("small", {}, row.detail) : null]),
      el("span", { className: "amount" }, [money(row.cost), el("small", {}, share(row.cost / total))]),
      meter,
    ]);
  }));
}

function chart(data) {
  const series = data.series;
  const top = Math.max(...series.map((point) => point.cost), 0);
  const box = el("div", { className: "chart" });
  const tip = el("span", { className: "tip", hidden: true });
  const label = (point) => {
    if (data.days === 1) return `${Number(point.label)}:00`;
    return new Date(`${point.label}T12:00`).toLocaleDateString(undefined, { weekday: data.days <= 7 ? "short" : undefined, day: "numeric", month: "short" });
  };
  const bars = el("div", { className: "bars", role: "list" }, series.map((point, index) => {
    const bar = el("button", { className: `bar ${point.cost ? "" : "zero"}`, type: "button", role: "listitem", ariaLabel: `${label(point)}: ${money(point.cost)}` }, el("i"));
    bar.firstChild.style.height = top ? `${Math.max(2, (point.cost / top) * 100)}%` : "2px";
    const show = () => {
      for (const other of bars.children) other.removeAttribute("aria-pressed");
      bar.setAttribute("aria-pressed", "true");
      tip.textContent = `${label(point)}: ${money(point.cost)}`;
      tip.hidden = false;
      tip.style.left = `${Math.min(85, Math.max(15, ((index + 0.5) / series.length) * 100))}%`;
    };
    bar.addEventListener("click", show);
    bar.addEventListener("mouseenter", show);
    return bar;
  }));
  bars.addEventListener("mouseleave", () => {
    tip.hidden = true;
    for (const other of bars.children) other.removeAttribute("aria-pressed");
  });
  const first = series[0];
  const last = series[series.length - 1];
  box.append(
    el("span", { className: "grid-label" }, money(top)),
    el("span", { className: "grid-line" }),
    tip,
    bars,
    el("div", { className: "axis" }, [el("span", {}, data.days === 1 ? "Midnight" : label(first)), el("span", {}, data.days === 1 ? "11 pm" : label(last))]),
  );
  return box;
}

function renderCosts() {
  const data = costs.data;
  const box = $("#costs");
  const period = { 1: "today", 7: "the last 7 days", 30: "the last 30 days" }[data.days];
  const before = { 1: "yesterday", 7: "the 7 days before", 30: "the 30 days before" }[data.days];
  let comparison = `Nothing ${before}.`;
  if (data.previous) {
    const change = Math.round(((data.total - data.previous) / data.previous) * 100);
    comparison = change === 0 ? `Same as ${before}.` : `${change > 0 ? "Up" : "Down"} ${Math.abs(change)}% on ${before} (${money(data.previous)}).`;
  }
  const parts = [
    el("div", { className: "hero" }, [el("b", {}, money(data.total)), el("span", {}, `Spent ${period}. ${comparison}`)]),
    chart(data),
  ];
  if (data.features.length) {
    parts.push(el("h3", { className: "section-title" }, "Where it goes"));
    parts.push(breakdownList(data.features.map((feature) => {
      const [label, detail] = FEATURES[feature.name] || [feature.name.replace(/_/g, " "), ""];
      const count = feature.name === "conversation" ? `${feature.count} conversation${feature.count === 1 ? "" : "s"}` : `${feature.count} time${feature.count === 1 ? "" : "s"}`;
      return { label, detail: detail ? `${detail}, ${count}` : count, cost: feature.cost };
    })));
  }
  const talk = data.conversations;
  if (talk.count) {
    const each = money(talk.cost / talk.count);
    parts.push(el("h3", { className: "section-title" }, "Inside a conversation"));
    parts.push(el("p", { className: "hint lead" }, `${talk.count} conversations, ${Math.round(talk.minutes)} minutes in all, about ${each} each.`));
    parts.push(breakdownList(talk.parts.map((part) => {
      const [label, detail] = PARTS[part.name] || [part.name.replace(/_/g, " "), ""];
      return { label, detail, cost: part.cost };
    })));
  }
  if (data.models.length) {
    parts.push(el("details", {}, [
      el("summary", {}, "By AI model"),
      breakdownList(data.models.map((model) => ({ label: model.name, detail: "", cost: model.cost }))),
    ]));
  }
  if (!data.total && !data.features.length) parts.push(el("div", { className: "empty" }, [icon("spark"), el("p", {}, `Nothing spent ${period}.`)]));
  const unpriced = data.unpriced ? ` ${data.unpriced} use${data.unpriced === 1 ? " has" : "s have"} no price and ${data.unpriced === 1 ? "isn't" : "aren't"} counted.` : "";
  parts.push(el("p", { className: "footnote" }, `Estimated from usage at list prices; the bills can differ a little.${unpriced}`));
  box.replaceChildren(...parts);
}

$("#usage").addEventListener("click", openCosts);

// Personality -----------------------------------------------------------------------------------

const personality = { data: null, sections: [] };

function splitSections(text) {
  const sections = [];
  let current = { title: "", body: [] };
  for (const line of text.split("\n")) {
    const heading = line.match(/^##\s+(.+?)\s*$/);
    if (heading) {
      if (current.title || current.body.join("").trim()) sections.push(current);
      current = { title: heading[1], body: [] };
    } else {
      current.body.push(line);
    }
  }
  if (current.title || current.body.join("").trim()) sections.push(current);
  return sections.map((section) => ({ title: section.title, body: section.body.join("\n").trim() }));
}

function joinSections(sections) {
  return sections.filter((s) => s.title || s.body.trim())
    .map((s) => (s.title ? `## ${s.title}\n${s.body.trim()}` : s.body.trim())).join("\n\n");
}

function sectionName(title) {
  if (!title) return "Opening";
  const lower = title.toLowerCase();
  return lower[0].toUpperCase() + lower.slice(1);
}

function grow(area) {
  area.style.height = "auto";
  area.style.height = `${area.scrollHeight + 4}px`;
}

async function openPersonality() {
  const data = await run(null, () => api("/api/personality"));
  if (!data) return;
  personality.data = data;
  personality.sections = splitSections(data.text);
  $("#personality-error").hidden = true;
  renderPersonality();
  $("#personality-sheet").showModal();
  $("#personality-sheet .sheet-body").scrollTop = 0;
  for (const area of document.querySelectorAll(".prompt-section textarea")) grow(area);
}

function renderPersonality() {
  const original = new Map(splitSections(personality.data.original).map((s) => [s.title, s.body]));
  const edited = joinSections(personality.sections) !== personality.data.original;
  const notice = $("#personality-edited");
  notice.hidden = !edited;
  if (edited) {
    const undo = el("button", { className: "copy", type: "button" }, "Go back to the original");
    undo.addEventListener("click", async () => {
      if (!(await ask({ title: "Go back to the original?", body: "All the changes to his personality are undone when you save.", yes: "Go back" }))) return;
      personality.sections = splitSections(personality.data.original);
      renderPersonality();
      for (const area of document.querySelectorAll(".prompt-section textarea")) grow(area);
    });
    notice.replaceChildren(icon("pencil"), el("span", {}, ["This has been changed from the original. ", undo]));
  }
  $("#personality-sections").replaceChildren(...personality.sections.map((section, index) => {
    const changed = original.get(section.title) !== section.body;
    const area = el("textarea", { value: section.body, ariaLabel: sectionName(section.title), spellcheck: true });
    area.addEventListener("input", () => {
      section.body = area.value;
      grow(area);
      const nowChanged = original.get(section.title) !== section.body;
      if (nowChanged !== changed) {
        renderPersonality();
        const again = document.querySelectorAll(".prompt-section textarea")[index];
        again.focus();
        again.setSelectionRange(area.selectionStart, area.selectionEnd);
        for (const each of document.querySelectorAll(".prompt-section textarea")) grow(each);
      }
    });
    const header = [el("h4", {}, sectionName(section.title))];
    if (changed) {
      header.push(el("span", { className: "badge-edited" }, original.has(section.title) ? "Edited" : "New"));
      const undo = el("button", { className: "text-button", type: "button" }, original.has(section.title) ? "Undo" : "Remove");
      undo.addEventListener("click", () => {
        if (original.has(section.title)) section.body = original.get(section.title);
        else personality.sections.splice(index, 1);
        renderPersonality();
        for (const each of document.querySelectorAll(".prompt-section textarea")) grow(each);
      });
      header.push(undo);
    }
    return el("section", { className: "prompt-section" }, [el("header", {}, header), area]);
  }));
}

$("#new-section-add").addEventListener("click", () => {
  const input = $("#new-section-name");
  const title = input.value.trim().replace(/^#+\s*/, "");
  if (!title) {
    input.focus();
    return;
  }
  personality.sections.push({ title: title.toUpperCase(), body: "" });
  input.value = "";
  renderPersonality();
  const areas = document.querySelectorAll(".prompt-section textarea");
  areas[areas.length - 1].focus();
});

$("#personality-form").addEventListener("submit", (event) => {
  event.preventDefault();
  const text = joinSections(personality.sections);
  run($("#personality-save"), async () => {
    try {
      personality.data = await api("/api/personality", { method: "PUT", body: { text, version: personality.data.version } });
      $("#personality-sheet").close();
      toast(personality.data.edited ? "Saved. He'll use it next time he wakes up." : "Back to the original.");
    } catch (problem) {
      $("#personality-error").textContent = problem.message;
      $("#personality-error").hidden = false;
    }
  });
});

for (const button of document.querySelectorAll("[data-close]")) {
  button.addEventListener("click", () => button.closest("dialog").close());
}

// Memory ----------------------------------------------------------------------------------------

const MEMORY_KINDS = [
  ["fact", "What he knows"],
  ["note", "Notes"],
  ["plan", "Plans"],
  ["situation", "For now"],
];
const memory = { entries: null, subject: "all", query: "", editing: null, showOld: false };

function subjectName(subject) {
  if (!subject) return "Nobody in particular";
  if (subject === "household") return "The family";
  const person = state.people.find((p) => p.face === subject || fold(firstName(p.name)) === fold(subject) || fold(p.name) === fold(subject));
  return person ? person.name : subject[0].toUpperCase() + subject.slice(1);
}

function shortDate(value) {
  const date = typeof value === "string" && /^\d{4}-\d{2}-\d{2}$/.test(value) ? new Date(`${value}T12:00`) : when(value);
  return date ? date.toLocaleDateString(undefined, { day: "numeric", month: "short" }) : "";
}

async function fetchMemory() {
  const payload = await api("/api/memory");
  memory.entries = payload.entries;
  return memory.entries;
}

async function openMemory(subject = "all") {
  memory.subject = subject;
  memory.query = "";
  memory.editing = null;
  memory.showOld = false;
  $("#memory-search").value = "";
  $("#memory-list").replaceChildren(el("p", { className: "hint" }, "Loading…"));
  $("#memory-filters").replaceChildren();
  if (!$("#memory-sheet").open) $("#memory-sheet").showModal();
  const [entries] = await Promise.all([
    run(null, fetchMemory),
    state.people.length ? null : api("/api/people").then(takePeople).catch(() => {}),
  ]);
  if (!entries) {
    $("#memory-list").replaceChildren(el("div", { className: "empty" }, [icon("brain"), el("p", {}, "His memory can't be read right now.")]));
    return;
  }
  renderMemory();
}

function renderMemory() {
  const current = memory.entries.filter((entry) => entry.status === "current");
  const counts = new Map();
  for (const entry of current) counts.set(entry.subject || "", (counts.get(entry.subject || "") || 0) + 1);
  const chip = (subject, label, count) => {
    const button = el("button", { className: "chip", type: "button", role: "tab" }, [label, el("span", {}, String(count))]);
    button.setAttribute("aria-selected", String(memory.subject === subject));
    button.addEventListener("click", () => {
      memory.subject = subject;
      memory.editing = null;
      renderMemory();
    });
    return button;
  };
  $("#memory-filters").replaceChildren(
    chip("all", "Everyone", current.length),
    ...[...counts.entries()].sort((a, b) => b[1] - a[1]).map(([subject, count]) => chip(subject, subjectName(subject), count)),
  );

  const query = fold(memory.query);
  const matches = (entry) =>
    (memory.subject === "all" || (entry.subject || "") === memory.subject) &&
    (!query || query.split(/\s+/).every((word) => fold(`${entry.text} ${subjectName(entry.subject)}`).includes(word)));
  const shown = current.filter(matches);
  const older = memory.entries.filter((entry) => entry.status !== "current" && matches(entry));
  const box = $("#memory-list");
  if (!shown.length && !older.length) {
    const text = query ? `Nothing matches “${memory.query}”.` : "He hasn't saved anything here yet.";
    box.replaceChildren(el("div", { className: "empty" }, [icon("brain"), el("p", {}, text)]));
    return;
  }
  const parts = [];
  for (const [kind, label] of MEMORY_KINDS) {
    const group = shown.filter((entry) => entry.kind === kind)
      .sort((a, b) => String(b.learned_at).localeCompare(String(a.learned_at)));
    if (!group.length) continue;
    parts.push(el("h3", { className: "section-title" }, label));
    parts.push(el("ul", { className: "memories" }, group.map(memoryRow)));
  }
  if (older.length) {
    if (memory.showOld) {
      parts.push(el("h3", { className: "section-title" }, "Replaced or done"));
      parts.push(el("ul", { className: "memories" }, older.map(memoryRow)));
    } else {
      const more = el("button", { className: "text-button show-older", type: "button" }, `Show ${older.length} replaced or done`);
      more.addEventListener("click", () => {
        memory.showOld = true;
        renderMemory();
      });
      parts.push(more);
    }
  }
  parts.push(el("p", { className: "footnote" }, "Changes reach him at his next conversation."));
  box.replaceChildren(...parts);
}

function memoryMeta(entry) {
  const bits = [];
  if (memory.subject === "all") bits.push(subjectName(entry.subject));
  bits.push(`saved ${shortDate(entry.learned_at)}`);
  if (entry.event_start) bits.push(entry.event_end && entry.event_end !== entry.event_start ? `${shortDate(entry.event_start)} to ${shortDate(entry.event_end)}` : `on ${shortDate(entry.event_start)}`);
  else if (entry.active_until && entry.status === "current") bits.push(`until ${shortDate(entry.active_until)}`);
  const text = bits.join(", ");
  return [text[0].toUpperCase() + text.slice(1), entry.certainty === "tentative" ? el("span", { className: "maybe" }, ". Not sure yet") : null];
}

function memoryRow(entry) {
  const row = el("li", { className: `memory ${entry.status === "current" ? "" : "old"}` });
  const view = el("button", { type: "button", ariaExpanded: String(memory.editing === entry.id) }, [
    el("span", { className: "text" }, entry.text),
    el("span", { className: "meta" }, memoryMeta(entry)),
  ]);
  view.addEventListener("click", () => {
    memory.editing = memory.editing === entry.id ? null : entry.id;
    renderMemory();
    if (memory.editing) document.querySelector(".memory-edit textarea")?.focus();
  });
  row.append(view);
  if (memory.editing === entry.id) {
    const area = el("textarea", { value: entry.text, maxLength: 280, ariaLabel: "Memory" });
    const save = el("button", { className: "pill sun small", type: "button" }, "Save");
    const forget = el("button", { className: "text-button danger", type: "button" }, [icon("trash"), "Forget"]);
    const cancel = el("button", { className: "pill ghost small", type: "button" }, "Cancel");
    cancel.addEventListener("click", () => {
      memory.editing = null;
      renderMemory();
    });
    save.addEventListener("click", () => run(save, async () => {
      const text = area.value.trim();
      if (!text || text === entry.text) {
        memory.editing = null;
        renderMemory();
        return;
      }
      const { entry: updated } = await api("/api/memory/update", { method: "POST", body: { id: entry.id, text } });
      memory.entries = memory.entries.map((e) => (e.id === entry.id ? updated : e));
      memory.editing = null;
      renderMemory();
      toast("Corrected.");
    }));
    forget.addEventListener("click", async () => {
      if (!(await ask({ title: "Forget this?", body: `“${entry.text}”`, yes: "Forget", danger: true }))) return;
      run(forget, async () => {
        await api("/api/memory/forget", { method: "POST", body: { id: entry.id } });
        memory.entries = memory.entries.filter((e) => e.id !== entry.id);
        memory.editing = null;
        renderMemory();
        toast("Forgotten.");
      });
    });
    row.append(el("div", { className: "memory-edit" }, [area, el("div", { className: "buttons-row" }, [forget, cancel, save])]));
  }
  return row;
}

let memorySearchTimer = null;
$("#memory-search").addEventListener("input", (event) => {
  clearTimeout(memorySearchTimer);
  memorySearchTimer = setTimeout(() => {
    memory.query = event.target.value.trim();
    memory.editing = null;
    renderMemory();
  }, 150);
});

function subjectFor(person) {
  const keys = new Set([person.face, fold(firstName(person.name)), fold(person.name)].filter(Boolean));
  const subjects = new Set((memory.entries || []).map((entry) => entry.subject).filter(Boolean));
  return [...subjects].find((subject) => keys.has(subject) || keys.has(fold(subject))) || null;
}

async function showPersonMemories(person) {
  const link = $("#person-memories");
  link.hidden = true;
  if (!person.name) return;
  try {
    if (memory.entries === null) await fetchMemory();
  } catch (error) {
    return;
  }
  const subject = subjectFor(person);
  const count = subject ? memory.entries.filter((e) => e.subject === subject && e.status === "current").length : 0;
  if (!count || !state.draft) return;
  $("#person-memories-label").textContent = `What he remembers about ${person.name} (${count})`;
  link.hidden = false;
  link.onclick = () => openMemory(subject);
}

// Settings --------------------------------------------------------------------------------------

const LANGUAGES = { auto: "Detect automatically", es: "Español", en: "English", it: "Italiano", pt: "Português" };
const WAKE = { low: 0.05, high: 0.99 };

function settingRow({ iconName, label, sub, control, body }) {
  return el("div", { className: "setting" }, [
    el("div", { className: "setting-head" }, [
      icon(iconName),
      el("span", { className: "row-text" }, [el("span", { className: "row-label" }, label), sub ? el("span", { className: "row-sub" }, sub) : null]),
      control || null,
    ]),
    body ? el("div", { className: "setting-body" }, body) : null,
  ]);
}

function switchFor(key, label) {
  const toggle = el("button", { className: "switch", type: "button", role: "switch", ariaLabel: label });
  toggle.setAttribute("aria-checked", String(state.settings.draft[key] === "1"));
  toggle.addEventListener("click", () => {
    state.settings.draft[key] = state.settings.draft[key] === "1" ? "0" : "1";
    toggle.setAttribute("aria-checked", String(state.settings.draft[key] === "1"));
    settingsChanged();
  });
  return toggle;
}

function selectFor(key, choices, label) {
  const select = el("select", { ariaLabel: label }, choices.map((choice) => el("option", { value: choice }, LANGUAGES[choice] || choice)));
  select.value = state.settings.draft[key] || choices[0];
  select.addEventListener("change", () => {
    state.settings.draft[key] = select.value;
    settingsChanged();
  });
  return select;
}

async function loadSettings() {
  const [payload] = await Promise.all([
    run(null, () => api("/api/settings")),
    api("/api/people").then(takePeople).catch(() => {}),
  ]);
  if (!payload) return;
  const original = {};
  const meta = {};
  for (const setting of payload.settings) {
    original[setting.key] = setting.value || setting.default;
    meta[setting.key] = setting;
  }
  state.settings = { original, draft: { ...original }, meta };
  renderSettings();
}

function renderSettings() {
  const { draft, meta } = state.settings;
  const form = $("#settings");
  const panels = [];
  const open = el("button", { className: "row open-row", type: "button" }, [
    icon("spark"),
    el("span", { className: "row-text" }, [el("span", { className: "row-label" }, "Personality"), el("span", { className: "row-sub" }, "How he talks and behaves")]),
    icon("chevron"),
  ]);
  open.addEventListener("click", () => openPersonality());
  const remembered = el("button", { className: "row open-row", type: "button" }, [
    icon("brain"),
    el("span", { className: "row-text" }, [el("span", { className: "row-label" }, "Memory"), el("span", { className: "row-sub" }, "What he remembers about everyone")]),
    icon("chevron"),
  ]);
  remembered.addEventListener("click", () => openMemory());
  panels.push(el("div", { className: "panel" }, [open, remembered]));

  if (meta.QUIET_HOURS) {
    const [from, to] = (draft.QUIET_HOURS || "21:30-07:00").split("-");
    const on = Boolean(draft.QUIET_HOURS);
    const toggle = el("button", { className: "switch", type: "button", role: "switch", ariaLabel: "Quiet hours" });
    toggle.setAttribute("aria-checked", String(on));
    const time = (label, value, index) => {
      const input = el("input", { type: "time", value, required: true });
      input.addEventListener("change", () => {
        const parts = (state.settings.draft.QUIET_HOURS || "21:30-07:00").split("-");
        parts[index] = input.value || parts[index];
        state.settings.draft.QUIET_HOURS = parts.join("-");
        settingsChanged();
      });
      return el("label", {}, [label, input]);
    };
    toggle.addEventListener("click", () => {
      state.settings.draft.QUIET_HOURS = on ? "" : `${from}-${to}`;
      renderSettings();
      settingsChanged();
    });
    panels.push(el("div", { className: "panel" }, settingRow({
      iconName: "moon", label: "Quiet hours", sub: "No listening, no calls", control: toggle,
      body: on ? el("div", { className: "times" }, [time("From", from, 0), time("Until", to, 1)]) : null,
    })));
  }

  const languages = [];
  if (meta.REALTIME_TRANSCRIPTION_LANGUAGE) {
    languages.push(settingRow({ iconName: "mic", label: "Language he listens for", body: selectFor("REALTIME_TRANSCRIPTION_LANGUAGE", meta.REALTIME_TRANSCRIPTION_LANGUAGE.choices, "Language he listens for") }));
  }
  if (meta.WHATSAPP_CALLS_LANGUAGE) {
    languages.push(settingRow({ iconName: "phone-in", label: "Language for incoming calls", body: selectFor("WHATSAPP_CALLS_LANGUAGE", meta.WHATSAPP_CALLS_LANGUAGE.choices, "Language for incoming calls") }));
  }
  if (languages.length) panels.push(el("div", { className: "panel" }, languages));

  if (meta.WHATSAPP_CALLERS) {
    const chosen = new Set((draft.WHATSAPP_CALLERS || "").split(",").filter(Boolean));
    const known = state.faces.length ? state.faces : [...chosen].map((id) => ({ id, name: id }));
    const chips = known.map((face) => {
      const chip = el("button", { className: `chip ${chosen.has(face.id) ? "on" : ""}`, type: "button" }, [avatar(face.name, face.id, "sm"), face.name]);
      chip.setAttribute("aria-pressed", String(chosen.has(face.id)));
      chip.addEventListener("click", () => {
        if (chosen.has(face.id)) chosen.delete(face.id);
        else chosen.add(face.id);
        state.settings.draft.WHATSAPP_CALLERS = [...chosen].join(",");
        renderSettings();
        settingsChanged();
      });
      return chip;
    });
    panels.push(el("div", { className: "panel" }, settingRow({
      iconName: "phone-out", label: "Who can ask him to call", sub: "He checks their face first",
      body: chips.length ? el("div", { className: "chips" }, chips) : el("p", { className: "hint" }, "Add faces in People first."),
    })));
  }

  if (meta.LOCAL_ACOUSTIC_WAKE_THRESHOLD) {
    const threshold = Number(draft.LOCAL_ACOUSTIC_WAKE_THRESHOLD || meta.LOCAL_ACOUSTIC_WAKE_THRESHOLD.default || 0.5);
    const slider = el("input", { type: "range", min: 0, max: 100, step: 1, ariaLabel: "Wake sensitivity" });
    slider.value = Math.round(((WAKE.high - threshold) / (WAKE.high - WAKE.low)) * 100);
    setFill(slider);
    slider.addEventListener("input", () => {
      setFill(slider);
      const value = WAKE.high - (slider.value / 100) * (WAKE.high - WAKE.low);
      state.settings.draft.LOCAL_ACOUSTIC_WAKE_THRESHOLD = String(Math.round(value * 100) / 100);
      settingsChanged();
    });
    const reset = el("button", { className: "text-button reset", type: "button" }, "Reset");
    reset.hidden = String(threshold) === String(Number(meta.LOCAL_ACOUSTIC_WAKE_THRESHOLD.default));
    reset.addEventListener("click", () => {
      state.settings.draft.LOCAL_ACOUSTIC_WAKE_THRESHOLD = meta.LOCAL_ACOUSTIC_WAKE_THRESHOLD.default;
      renderSettings();
      settingsChanged();
    });
    panels.push(el("div", { className: "panel" }, settingRow({
      iconName: "spark", label: "Wake sensitivity", control: reset,
      body: [slider, el("div", { className: "scale" }, [el("span", {}, "Harder to wake"), el("span", {}, "Wakes easily")])],
    })));
  }

  const flags = [
    ["FACE_RECOGNITION_ENABLED", "face", "Recognize faces", "Greets people by name"],
    ["SPEAKER_FOLLOWING_ENABLED", "speaker", "Turn toward voices", "Looks at whoever is talking"],
    ["DISPLAY_NOW_PLAYING", "screen", "Show the song on the e-ink screen", ""],
    ["PHOTO_ARCHIVE_ENABLED", "gallery", "Keep photos in the gallery", "Photos he takes stay on the Mac mini"],
  ].filter(([key]) => meta[key]);
  if (flags.length) {
    panels.push(el("div", { className: "panel" }, flags.map(([key, iconName, label, sub]) => settingRow({ iconName, label, sub, control: switchFor(key, label) }))));
  }
  form.replaceChildren(...panels);
}

function settingsChanged() {
  const { original, draft } = state.settings;
  $("#save-bar").hidden = !Object.keys(draft).some((key) => draft[key] !== original[key]);
}

$("#settings-discard").addEventListener("click", () => {
  state.settings.draft = { ...state.settings.original };
  renderSettings();
  settingsChanged();
});

$("#settings").addEventListener("submit", (event) => {
  event.preventDefault();
  const { original, draft } = state.settings;
  const changes = {};
  for (const key of Object.keys(draft)) if (draft[key] !== original[key]) changes[key] = draft[key];
  if (!Object.keys(changes).length) return;
  run($("#settings-save"), async () => {
    await api("/api/settings", { method: "PUT", body: { changes } });
    $("#save-bar").hidden = true;
    try {
      await api("/api/restart", { method: "POST" });
      toast("Saved. Reachy is restarting.");
    } catch (error) {
      toast(`Saved. Restart Reachy for it to apply. ${error.message}`, "bad");
    }
    loadSettings();
  });
});

// Start -----------------------------------------------------------------------------------------

window.addEventListener("hashchange", showTab);
if ("serviceWorker" in navigator) navigator.serviceWorker.register("/sw.js").catch(() => {});
api("/api/me").then((me) => {
  state.me = me;
  showTitle();
}).catch(() => {});
api("/api/status").then((status) => showPrivacy(status.privacy.on)).catch(() => {});
showTab();
