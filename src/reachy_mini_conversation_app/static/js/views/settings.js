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
});

const PROVIDER_LABELS = Object.freeze({
  [REALTIME_PROVIDERS.HUGGINGFACE]: "Hugging Face",
  [REALTIME_PROVIDERS.GEMINI]: "Gemini Live",
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
    statusSection.element
  );
  outlet.replaceChildren(view);

  await Promise.all([
    refreshStatus({ statusSection, connectionSection, signal }),
    refreshVoices({ voiceSection, signal }),
  ]);
}

function buildConnectionSection({ onSaved } = {}) {
  const providerSelect = h(
    "select",
    { class: "settings-select", name: "provider" },
    h("option", { value: REALTIME_PROVIDERS.HUGGINGFACE }, PROVIDER_LABELS[REALTIME_PROVIDERS.HUGGINGFACE]),
    h("option", { value: REALTIME_PROVIDERS.GEMINI }, PROVIDER_LABELS[REALTIME_PROVIDERS.GEMINI])
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
      hint.textContent = "Gemini Live is selected. Configure GEMINI_API_KEY in the app environment; this page never handles credentials.";
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
      } else if (provider === REALTIME_PROVIDERS.GEMINI) {
        const ready = payload.has_realtime_target ?? payload.can_proceed_with_gemini;
        list.appendChild(
          statusRow(
            "Gemini configuration",
            ready ? "Configured" : payload.missing_config || "Missing GEMINI_API_KEY",
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
      };
      list.appendChild(
        statusRow(
          "Backend",
          backendLabels[backendState] || "Unavailable",
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
