/** Settings view: realtime provider connection, voice, and runtime status. */

import {
  applyVoice,
  describeError,
  getCurrentVoice,
  getStatus,
  listVoices,
  saveBackendConfig,
  untilReady,
} from "../api.js";
import { h } from "../ui.js";

const HF_CONNECTION_MODES = Object.freeze({
  DEPLOYED: "deployed",
  LOCAL: "local",
});

const REALTIME_PROVIDERS = Object.freeze({
  HUGGINGFACE: "huggingface",
  GEMINI: "gemini",
  OPENAI_LIVE: "openai_live",
});

const PROVIDER_LABELS = Object.freeze({
  [REALTIME_PROVIDERS.HUGGINGFACE]: "Hugging Face",
  [REALTIME_PROVIDERS.GEMINI]: "Gemini Live",
  [REALTIME_PROVIDERS.OPENAI_LIVE]: "OpenAI Live",
});

const CLOUD_PROVIDER_INFO = Object.freeze({
  [REALTIME_PROVIDERS.GEMINI]: Object.freeze({
    keyName: "GEMINI_API_KEY",
    legacyReadyField: "can_proceed_with_gemini",
    configurationLabel: "Gemini configuration",
  }),
  [REALTIME_PROVIDERS.OPENAI_LIVE]: Object.freeze({
    keyName: "OPENAI_API_KEY",
    legacyReadyField: "can_proceed_with_openai_live",
    configurationLabel: "OpenAI Live configuration",
  }),
});

const DEFAULT_HF_HOST = "localhost";
const DEFAULT_HF_PORT = 8765;

const HF_MODE_HINTS = Object.freeze({
  [HF_CONNECTION_MODES.DEPLOYED]: "Uses the hosted Hugging Face backend. No API key required.",
  [HF_CONNECTION_MODES.LOCAL]: "Connects directly to the host and port below.",
});

export async function mountSettingsView({ outlet, signal }) {
  const connectionSection = buildConnectionSection({
    onSaved: () =>
      Promise.all([
        refreshStatus({ statusSection, connectionSection, signal }),
        refreshVoices({ voiceSection, signal }),
      ]),
  });
  const voiceSection = buildVoiceSection();
  const statusSection = buildStatusSection();
  const memorySection = buildMemorySection(signal);
  const facesSection = buildFacesSection(signal);

  const view = h(
    "section",
    { class: "view view--settings" },
    h(
      "header",
      { class: "view-header" },
      h("h1", { class: "view-title" }, "Settings"),
      h("p", { class: "view-subtitle" }, "Connection, voice, and runtime state for Reachy Mini.")
    ),
    connectionSection.element,
    voiceSection.element,
    statusSection.element,
    memorySection,
    facesSection
  );
  outlet.replaceChildren(view);

  await Promise.all([
    refreshStatus({ statusSection, connectionSection, signal }),
    refreshVoices({ voiceSection, signal }),
  ]);
}

const MAX_FACE_PHOTOS = 5;
const FACE_PHOTO_MAX_SIDE = 1600;

/** Shrink a photo (upright, per its EXIF orientation) to a JPEG and return it base64-encoded. */
async function photoAsJpegBase64(file) {
  const bitmap = await createImageBitmap(file);
  const scale = Math.min(1, FACE_PHOTO_MAX_SIDE / Math.max(bitmap.width, bitmap.height));
  const canvas = document.createElement("canvas");
  canvas.width = Math.max(1, Math.round(bitmap.width * scale));
  canvas.height = Math.max(1, Math.round(bitmap.height * scale));
  canvas.getContext("2d").drawImage(bitmap, 0, 0, canvas.width, canvas.height);
  bitmap.close?.();
  const blob = await new Promise((resolve, reject) =>
    canvas.toBlob((result) => (result ? resolve(result) : reject(new Error("encode failed"))), "image/jpeg", 0.9)
  );
  const bytes = new Uint8Array(await blob.arrayBuffer());
  let binary = "";
  for (let i = 0; i < bytes.length; i += 0x8000) binary += String.fromCharCode(...bytes.subarray(i, i + 0x8000));
  return btoa(binary);
}

function buildFacesSection(signal) {
  const message = h("p", { class: "settings-hint" }, "People Reachy recognizes on camera. Type a name, then have them look at Reachy or upload up to 5 photos of only them. Faces stay on this computer.");
  const people = h("div", {});
  const show = h("button", { type: "button", class: "btn" }, "Show faces");
  const nameInput = h("input", { type: "text", name: "face_name", autocomplete: "off", maxlength: "60", placeholder: "Name", class: "settings-input" });
  const enroll = h("button", { type: "button", class: "btn" }, "Remember my face");
  const upload = h("button", { type: "button", class: "btn" }, "Upload photos");
  const fileInput = h("input", { type: "file", accept: "image/*", multiple: true, hidden: true });
  const enrollRow = h("div", { class: "settings-field-row" }, nameInput, enroll, upload, fileInput);

  async function request(url, options = {}) {
    const response = await fetch(url, { cache: "no-store", signal, ...options });
    if (response.status === 404) throw new Error("Face recognition is not enabled (FACE_RECOGNITION_ENABLED=1).");
    if (response.status === 403) throw new Error("Open these settings on Reachy’s computer to manage faces.");
    const result = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(result.error || "Faces are unavailable.");
    return result;
  }

  async function refresh() {
    show.disabled = true;
    try {
      const result = await request("/api/faces");
      if (signal.aborted) return;
      people.replaceChildren();
      if (!result.people.length) message.textContent = "No saved faces yet. Type a name, look at Reachy, and press the button.";
      for (const person of result.people) {
        const forget = h("button", { type: "button", class: "btn" }, "Forget");
        const thumbs = h("div", { style: { display: "flex", gap: "6px", flexWrap: "wrap" } });
        for (const photo of person.photos.slice(-4)) {
          const query = new URLSearchParams({ person_id: person.id, photo });
          thumbs.append(h("img", { src: `/api/faces/photo?${query}`, alt: person.name, width: "64", height: "64", style: { borderRadius: "8px", objectFit: "cover" } }));
        }
        people.append(h("div", { class: "settings-section" }, h("p", {}, `${person.name} · ${person.samples} samples`), thumbs, forget));
        forget.addEventListener("click", async () => {
          forget.disabled = true;
          try {
            await request("/api/faces/forget", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ person_id: person.id }) });
            await refresh();
            if (!signal.aborted) message.textContent = `Forgot ${person.name}'s face.`;
          } catch (error) {
            if (!signal.aborted) message.textContent = error.message;
          } finally {
            forget.disabled = false;
          }
        });
      }
    } catch (error) {
      if (!signal.aborted) {
        people.replaceChildren();
        message.textContent = error.message;
      }
    } finally {
      show.disabled = false;
    }
  }

  enroll.addEventListener("click", async () => {
    const name = nameInput.value.trim();
    if (!name) {
      message.textContent = "Type the name of the person looking at Reachy first.";
      return;
    }
    enroll.disabled = true;
    message.textContent = `Look at Reachy for about three seconds, ${name}…`;
    try {
      const result = await request("/api/faces/enroll", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ name }) });
      nameInput.value = "";
      await refresh();
      if (!signal.aborted) message.textContent = `Saved ${result.person.name}'s face.`;
    } catch (error) {
      if (!signal.aborted) message.textContent = error.message;
    } finally {
      enroll.disabled = false;
    }
  });
  upload.addEventListener("click", () => {
    if (!nameInput.value.trim()) {
      message.textContent = "Type the name of the person in the photos first.";
      return;
    }
    fileInput.click();
  });
  fileInput.addEventListener("change", async () => {
    const name = nameInput.value.trim();
    const files = Array.from(fileInput.files || []);
    fileInput.value = "";
    if (!files.length) return;
    if (files.length > MAX_FACE_PHOTOS) {
      message.textContent = `Choose at most ${MAX_FACE_PHOTOS} photos at a time.`;
      return;
    }
    upload.disabled = true;
    enroll.disabled = true;
    message.textContent = `Reading ${files.length} photo${files.length > 1 ? "s" : ""}…`;
    try {
      const photos = [];
      for (const [index, file] of files.entries()) {
        try {
          photos.push(await photoAsJpegBase64(file));
        } catch {
          throw new Error(`Photo ${index + 1} could not be read; use JPEG or PNG.`);
        }
      }
      const result = await request("/api/faces/upload", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ name, photos }) });
      nameInput.value = "";
      await refresh();
      if (!signal.aborted) {
        const skipped = result.skipped.length ? ` Skipped: ${result.skipped.join("; ")}.` : "";
        message.textContent = `Saved ${result.person.name}'s face from ${photos.length - result.skipped.length} photo(s).${skipped}`;
      }
    } catch (error) {
      if (!signal.aborted) message.textContent = error.message;
    } finally {
      upload.disabled = false;
      enroll.disabled = false;
    }
  });
  show.addEventListener("click", refresh);
  return h("section", { class: "settings-section" }, h("h2", { class: "settings-section-title" }, "Faces Reachy knows"), message, enrollRow, show, people);
}

function buildMemorySection(signal) {
  const message = h("p", { class: "settings-hint" }, "Show saved details to review or forget one. Relevant memories are used in conversations.");
  const entries = h("div", {});
  const show = h("button", { type: "button", class: "btn" }, "Show memories");
  async function refresh() {
    show.disabled = true;
    try {
      const response = await fetch("/api/memory", { cache: "no-store", signal });
      if (response.status === 404) throw new Error("Memory is not enabled yet.");
      if (response.status === 403) throw new Error("Open these settings on Reachy’s computer to review memory.");
      const result = await response.json();
      if (!response.ok) throw new Error(result.error || "Memory is unavailable.");
      if (signal.aborted) return;
      entries.replaceChildren();
      message.textContent = result.entries.length ? "Only forget details you want removed." : "No saved memories.";
      for (const entry of result.entries) {
        const forget = h("button", { type: "button", class: "btn" }, "Forget");
        const inactive = entry.status !== "current" || (entry.active_until && Date.parse(entry.active_until) <= Date.now());
        const label = `${entry.subject || "Unassigned — not used in conversation"} · ${inactive ? "Older note" : entry.kind}${entry.certainty === "tentative" ? " · tentative" : ""}`;
        entries.append(h("div", { class: "settings-section" }, h("small", {}, label), h("p", {}, entry.text), forget));
        forget.addEventListener("click", async () => {
          forget.disabled = true;
          try {
            const response = await fetch("/api/memory/forget", {
              method: "POST", headers: { "Content-Type": "application/json" },
              body: JSON.stringify({ memory_id: entry.id }), signal,
            });
            if (!response.ok) throw new Error("Could not forget this detail. Please retry.");
            await refresh();
            if (!signal.aborted) message.textContent = "Forgotten. Start a new conversation to continue.";
          } catch (error) {
            if (!signal.aborted) message.textContent = error.message || "Memory is unavailable.";
          } finally {
            forget.disabled = false;
          }
        });
      }
    } catch (error) {
      if (!signal.aborted) {
        entries.replaceChildren();
        message.textContent = error.message || "Memory is unavailable.";
      }
    } finally {
      show.disabled = false;
    }
  }
  show.addEventListener("click", refresh);
  return h("section", { class: "settings-section" }, h("h2", { class: "settings-section-title" }, "What Reachy remembers"), message, show, entries);
}

function buildConnectionSection({ onSaved } = {}) {
  const providerSelect = h(
    "select",
    { class: "settings-select", name: "provider" },
    h("option", { value: REALTIME_PROVIDERS.HUGGINGFACE }, PROVIDER_LABELS[REALTIME_PROVIDERS.HUGGINGFACE]),
    h("option", { value: REALTIME_PROVIDERS.GEMINI }, PROVIDER_LABELS[REALTIME_PROVIDERS.GEMINI]),
    h("option", { value: REALTIME_PROVIDERS.OPENAI_LIVE }, PROVIDER_LABELS[REALTIME_PROVIDERS.OPENAI_LIVE])
  );
  const hfModeSelect = h(
    "select",
    { class: "settings-select", name: "hf_mode" },
    h("option", { value: HF_CONNECTION_MODES.DEPLOYED }, "Hosted"),
    h("option", { value: HF_CONNECTION_MODES.LOCAL }, "Local")
  );
  const hfHostInput = h("input", {
    type: "text",
    name: "hf_host",
    autocomplete: "off",
    placeholder: DEFAULT_HF_HOST,
    value: DEFAULT_HF_HOST,
    class: "settings-input",
  });
  const hfPortInput = h("input", {
    type: "number",
    name: "hf_port",
    min: "1",
    max: "65535",
    step: "1",
    inputmode: "numeric",
    value: String(DEFAULT_HF_PORT),
    class: "settings-input",
  });
  const hfLocalFields = h(
    "div",
    { class: "settings-field-row", "data-role": "hf-local-fields" },
    h(
      "label",
      { class: "settings-field" },
      h("span", { class: "settings-label" }, "Host/IP"),
      hfHostInput
    ),
    h(
      "label",
      { class: "settings-field" },
      h("span", { class: "settings-label" }, "Port"),
      hfPortInput
    )
  );
  const hint = h("p", { class: "settings-hint" }, "");
  const status = h("p", { class: "settings-status", role: "status", "aria-live": "polite" });
  const submitButton = h("button", { type: "submit", class: "btn btn--primary" }, "Save connection");

  const form = h(
    "form",
    { class: "settings-form" },
    h(
      "label",
      { class: "settings-field" },
      h("span", { class: "settings-label" }, "Realtime provider"),
      providerSelect
    ),
    h(
      "label",
      { class: "settings-field" },
      h("span", { class: "settings-label" }, "Hugging Face connection"),
      hfModeSelect
    ),
    hfLocalFields,
    hint,
    h("div", { class: "settings-actions" }, submitButton),
    status
  );

  const element = h(
    "section",
    { class: "settings-section" },
    h("h2", { class: "settings-section-title" }, "Connection"),
    form
  );

  function syncProviderFields() {
    const isHuggingFace = providerSelect.value === REALTIME_PROVIDERS.HUGGINGFACE;
    hfModeSelect.parentElement.style.display = isHuggingFace ? "" : "none";
    hfLocalFields.style.display = isHuggingFace && hfModeSelect.value === HF_CONNECTION_MODES.LOCAL ? "" : "none";
    hfModeSelect.disabled = !isHuggingFace;
    hfHostInput.disabled = !isHuggingFace || hfModeSelect.value !== HF_CONNECTION_MODES.LOCAL;
    hfPortInput.disabled = !isHuggingFace || hfModeSelect.value !== HF_CONNECTION_MODES.LOCAL;
    hfHostInput.required = isHuggingFace && hfModeSelect.value === HF_CONNECTION_MODES.LOCAL;
    hfPortInput.required = isHuggingFace && hfModeSelect.value === HF_CONNECTION_MODES.LOCAL;
    if (isHuggingFace) {
      submitButton.textContent = "Save connection";
      hint.textContent = HF_MODE_HINTS[hfModeSelect.value] || "";
    } else {
      submitButton.textContent = "Save provider";
      const keyName = CLOUD_PROVIDER_INFO[providerSelect.value]?.keyName || "GEMINI_API_KEY";
      hint.textContent = `${PROVIDER_LABELS[providerSelect.value]} is selected. Configure ${keyName} in the app environment; this page never handles credentials.`;
    }
  }

  function syncLocalFields() {
    const isLocal = hfModeSelect.value === HF_CONNECTION_MODES.LOCAL;
    if (providerSelect.value === REALTIME_PROVIDERS.HUGGINGFACE) {
      hfLocalFields.style.display = isLocal ? "" : "none";
      hfHostInput.disabled = !isLocal;
      hfPortInput.disabled = !isLocal;
      hfHostInput.required = isLocal;
      hfPortInput.required = isLocal;
      hint.textContent = HF_MODE_HINTS[hfModeSelect.value] || "";
    }
  }

  hfModeSelect.addEventListener("change", syncLocalFields);
  providerSelect.addEventListener("change", syncProviderFields);

  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    if (submitButton.disabled) return;
    submitButton.disabled = true;
    providerSelect.disabled = true;
    hfModeSelect.disabled = true;
    hfHostInput.disabled = true;
    hfPortInput.disabled = true;
    form.setAttribute("aria-busy", "true");
    status.classList.remove("is-error");
    status.textContent = "Saving…";
    try {
      const payload = { provider: providerSelect.value };
      if (providerSelect.value === REALTIME_PROVIDERS.HUGGINGFACE) {
        payload.hf_mode = hfModeSelect.value;
      }
      if (
        providerSelect.value === REALTIME_PROVIDERS.HUGGINGFACE &&
        hfModeSelect.value === HF_CONNECTION_MODES.LOCAL
      ) {
        payload.hf_host = hfHostInput.value.trim();
        if (hfPortInput.value) {
          payload.hf_port = Number.parseInt(hfPortInput.value, 10);
        }
      }
      const result = await saveBackendConfig(payload);
      status.textContent =
        result?.message || (result?.requires_restart ? "Saved. Restart the app to apply." : "Saved.");
      await onSaved?.();
    } catch (error) {
      status.textContent = `Failed to save: ${describeError(error)}`;
      status.classList.add("is-error");
    } finally {
      submitButton.disabled = false;
      providerSelect.disabled = false;
      hfModeSelect.disabled = false;
      syncProviderFields();
      syncLocalFields();
      form.removeAttribute("aria-busy");
    }
  });

  syncProviderFields();

  return {
    element,
    syncFromStatus(payload) {
      const provider = payload?.realtime_provider || payload?.backend;
      if (Object.values(REALTIME_PROVIDERS).includes(provider)) {
        providerSelect.value = provider;
      }
      if (Object.values(HF_CONNECTION_MODES).includes(payload?.hf_connection_mode)) {
        hfModeSelect.value = payload.hf_connection_mode;
      }
      if (payload?.hf_direct_host) {
        hfHostInput.value = payload.hf_direct_host;
      }
      if (payload?.hf_direct_port != null) {
        hfPortInput.value = String(payload.hf_direct_port);
      }
      syncProviderFields();
      syncLocalFields();
    },
  };
}

function buildVoiceSection() {
  const select = h(
    "select",
    { class: "settings-select", name: "voice", disabled: "disabled" },
    h("option", { value: "" }, "Loading voices…")
  );
  const status = h("p", { class: "settings-status", role: "status", "aria-live": "polite" });
  const submitButton = h(
    "button",
    { type: "submit", class: "btn btn--primary", disabled: "disabled" },
    "Apply voice"
  );
  const form = h(
    "form",
    { class: "settings-form" },
    h("label", { class: "settings-field" }, h("span", { class: "settings-label" }, "Voice"), select),
    h("div", { class: "settings-actions" }, submitButton),
    status
  );

  const element = h(
    "section",
    { class: "settings-section" },
    h("h2", { class: "settings-section-title" }, "Voice"),
    form
  );

  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    if (submitButton.disabled || !select.value) return;
    submitButton.disabled = true;
    select.disabled = true;
    form.setAttribute("aria-busy", "true");
    status.classList.remove("is-error");
    status.textContent = "Applying…";
    try {
      const result = await applyVoice(select.value);
      status.textContent = result?.status || "Voice applied.";
    } catch (error) {
      status.textContent = `Failed to apply: ${describeError(error)}`;
      status.classList.add("is-error");
    } finally {
      submitButton.disabled = !select.value;
      select.disabled = !select.value;
      form.removeAttribute("aria-busy");
    }
  });

  return {
    element,
    setOptions(voices, current) {
      select.replaceChildren();
      if (!voices.length) {
        select.appendChild(h("option", { value: "" }, "No voices available"));
        select.disabled = true;
        submitButton.disabled = true;
        status.textContent = "Voices are unavailable right now.";
        return;
      }
      for (const v of voices) {
        const opt = h("option", { value: v }, v);
        if (v === current) opt.selected = true;
        select.appendChild(opt);
      }
      select.disabled = false;
      submitButton.disabled = false;
      status.textContent = "";
    },
  };
}

function buildStatusSection() {
  const list = h(
    "dl",
    { class: "settings-status-grid" },
    statusRow("Backend", "Loading…")
  );
  const element = h(
    "section",
    { class: "settings-section" },
    h("h2", { class: "settings-section-title" }, "Current state"),
    list
  );

  return {
    element,
    render(payload) {
      list.replaceChildren();
      const provider = payload.realtime_provider || payload.backend;
      list.appendChild(statusRow("Provider", PROVIDER_LABELS[provider] || provider || "-"));
      if (provider === REALTIME_PROVIDERS.HUGGINGFACE) {
        list.appendChild(statusRow("HF connection", formatHfMode(payload.hf_connection_mode)));
        if (payload.hf_connection_mode === HF_CONNECTION_MODES.LOCAL) {
          list.appendChild(statusRow("HF target", formatHfTarget(payload)));
        }
        list.appendChild(
          statusRow(
            "Configuration",
            payload.has_hf_connection ? "Ready" : "Missing",
            payload.has_hf_connection ? "ok" : "warn"
          )
        );
      } else if (CLOUD_PROVIDER_INFO[provider]) {
        const providerInfo = CLOUD_PROVIDER_INFO[provider];
        const ready = payload.has_realtime_target ?? payload[providerInfo.legacyReadyField];
        list.appendChild(
          statusRow(
            providerInfo.configurationLabel,
            ready ? "Configured" : payload.missing_config || `Missing ${providerInfo.keyName}`,
            ready ? "ok" : "warn"
          )
        );
        list.appendChild(
          statusRow(
            "Credentials",
            "Managed in the app environment",
            undefined
          )
        );
      } else {
        list.appendChild(statusRow("Configuration", "Unavailable", "warn"));
      }
      const backendState = payload.backend_connected
        ? "connected"
        : payload.backend_connection_state || "not_started";
      const backendLabels = {
        connected: "Connected",
        connecting: "Connecting…",
        disconnected: "Disconnected",
        not_started: "Not started",
        restart_required: "Restart required",
        waiting_for_config: "Waiting for configuration",
        stopping: "Stopping…",
      };
      const backendLabel =
        backendState === "stopping" && /failed|timeout|incomplete|error/i.test(payload.backend_error || "")
          ? "Cleanup unavailable"
          : backendLabels[backendState] || "Unavailable";
      list.appendChild(
        statusRow(
          "Backend",
          backendLabel,
          backendState === "connected" ? "ok" : backendState === "not_started" ? undefined : "warn"
        )
      );
      if (payload.backend_error) {
        list.appendChild(statusRow("Backend error", payload.backend_error, "warn"));
      }
      if (payload.requires_restart) {
        list.appendChild(statusRow("Restart", "Required to apply changes", "warn"));
      }
    },
    renderUnavailable(error) {
      list.replaceChildren(statusRow("Backend", `Unavailable: ${describeError(error)}`, "warn"));
    },
  };
}

function statusRow(label, value, tone) {
  return h(
    "div",
    { class: ["settings-status-row", tone && `is-${tone}`] },
    h("dt", { class: "settings-status-label" }, label),
    h("dd", { class: "settings-status-value" }, value)
  );
}

function formatHfMode(mode) {
  if (mode === HF_CONNECTION_MODES.LOCAL) return "Local";
  if (mode === HF_CONNECTION_MODES.DEPLOYED) return "Hosted";
  return "-";
}

function formatHfTarget(payload) {
  const host = payload?.hf_direct_host;
  const port = payload?.hf_direct_port;
  if (!host) return "-";
  return `${host}:${port || DEFAULT_HF_PORT}`;
}

async function refreshStatus({ statusSection, connectionSection, signal }) {
  try {
    const payload = await untilReady(getStatus, signal);
    if (signal.aborted) return;
    statusSection.render(payload);
    connectionSection.syncFromStatus(payload);
  } catch (error) {
    if (signal.aborted) return;
    statusSection.renderUnavailable(error);
  }
}

async function refreshVoices({ voiceSection, signal }) {
  let voices = [];
  let current = "";
  try {
    voices = await untilReady(listVoices, signal);
  } catch {
    voices = [];
  }
  if (signal.aborted) return;
  try {
    const data = await getCurrentVoice();
    current = data?.voice || "";
  } catch {
    current = "";
  }
  if (signal.aborted) return;
  voiceSection.setOptions(voices, current);
}
