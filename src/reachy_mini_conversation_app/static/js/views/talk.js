/**
 * Talk view: conversation orb driven by the RPC activity stream.
 * Audio I/O runs entirely in Python; the orb doubles as the mic toggle.
 * Robot stays live, tapping the orb only mutes or unmutes the user's mic.
 */

import {
  activateConversation,
  applyPersonality,
  getStatus,
  getTranscriptPreview,
  getMicState,
  listPersonalities,
  setMicMuted,
  subscribe,
} from "../api.js";
import { ORB_STATES } from "../constants.js";
import { createOrb, mapActivityToState } from "../orb.js";
import { consumePendingApply } from "../pending-apply.js";
import { setPersonality } from "../personality-badge.js";
import { h, prettifyProfileName } from "../ui.js";

const CAPTION_BY_STATE = Object.freeze({
  [ORB_STATES.MUTED]: "Muted",
  [ORB_STATES.IDLE]: "Ready",
  [ORB_STATES.CONNECTING]: "Connecting to the backend...",
  [ORB_STATES.LISTENING]: "Listening",
  [ORB_STATES.THINKING]: "Thinking",
  [ORB_STATES.SPEAKING]: "Speaking",
  [ORB_STATES.ERROR]: "Connection error",
});

export async function mountTalkView({ outlet, signal }) {
  const pending = consumePendingApply();
  const micStatePromise = getMicState().catch((error) => {
    console.warn("Failed to load microphone state", error);
    return null;
  });
  const runtimeStatusPromise = getStatus().catch((error) => {
    console.warn("Failed to load hybrid runtime status", error);
    return null;
  });
  const transcriptPreviewPromise = getTranscriptPreview().catch(() => null);
  let muted = false;
  let micReady = false;
  let togglePending = false;
  let activationPending = false;
  let stopping = false;
  let stoppingReason = "";
  let hybridEnabled = false;
  let activePersonality = null;
  let subscription = null;
  let transcriptPreviewTimer = null;
  let transcriptPreviewRequestPending = false;

  const caption = h(
    "p",
    { class: "talk__caption", role: "status", "aria-live": "polite" },
    CAPTION_BY_STATE[ORB_STATES.CONNECTING]
  );
  const defaultAction = document.querySelector('[data-component="default-personality-action"]');
  if (defaultAction) {
    defaultAction.hidden = true;
    defaultAction.addEventListener("click", onSetDefault);
  }
  const orb = createOrb({
    initialState: ORB_STATES.CONNECTING,
    onStateChange: (state) => {
      caption.textContent = CAPTION_BY_STATE[state] || "";
    },
  });
  orb.root.disabled = true;
  orb.root.addEventListener("click", onMicTap);
  syncMicAria();

  const transcriptPreviewText = h(
    "p",
    { class: "talk__transcript-preview-text", "aria-live": "polite" },
    ""
  );
  const transcriptPreview = h(
    "section",
    { class: "talk__transcript-preview", hidden: true, "aria-label": "Local transcript preview" },
    h("h2", { class: "talk__transcript-preview-title" }, "Local transcript preview"),
    h("p", { class: "talk__transcript-preview-hint" }, "Diagnostic preview; not logged or saved."),
    transcriptPreviewText
  );

  signal.addEventListener("abort", cleanup, { once: true });

  const view = h(
    "section",
    { class: "view view--talk" },
    h("div", { class: "talk__orb-wrap" }, orb.root),
    caption,
    transcriptPreview
  );
  outlet.replaceChildren(view);

  if (pending) {
    caption.textContent = `Applying "${prettifyProfileName(pending.name)}"…`;
    try {
      await pending.promise;
    } catch (error) {
      if (signal.aborted) return;
      orb.setState(ORB_STATES.ERROR);
      caption.textContent = `Failed to apply personality: ${error?.message || error}`;
      return;
    }
    if (signal.aborted) return;
    // The activity subscription will flip the orb to its resting state next tick.
    caption.textContent = CAPTION_BY_STATE[ORB_STATES.CONNECTING];
    void refreshPersonalityState();
  } else {
    // Deep link to /talk with no pending apply: refresh the header badge.
    void refreshPersonalityState();
  }

  const [micState, runtimeStatus, transcriptPreviewState] = await Promise.all([
    micStatePromise,
    runtimeStatusPromise,
    transcriptPreviewPromise,
  ]);
  if (micState) muted = Boolean(micState.muted);
  hybridEnabled = runtimeStatus?.hybrid_runtime_enabled === true;
  if (signal.aborted) return;
  if (hybridEnabled && runtimeStatus?.hybrid_state === "STOPPING") {
    setStoppingState(runtimeStatus.backend_error || "");
  }
  if (renderTranscriptPreview(transcriptPreviewState)) {
    transcriptPreviewTimer = window.setInterval(() => void refreshTranscriptPreview(), 1000);
  }
  micReady = true;
  orb.root.disabled = false;
  syncMicAria();
  if (hybridEnabled) document.addEventListener("keydown", onActivationKey);

  subscription = subscribeConversationEvents({
    // Re-sync mic state after subscribing: another tab may have toggled it.
    onReady: async () => {
      if (!togglePending) {
        try {
          muted = Boolean((await getMicState())?.muted);
        } catch {
          // keep the last known mute state
        }
      }
      if (signal.aborted) return;
      await refreshHybridState();
      if (signal.aborted) return;
      if (stopping) {
        orb.setState(ORB_STATES.CONNECTING);
        caption.textContent = stoppingCaption(stoppingReason);
        syncMicAria();
        return;
      }
      orb.setState(restingState());
      caption.textContent = CAPTION_BY_STATE[restingState()];
      syncMicAria();
    },
    onActivity: (reason) => {
      if (stopping || muted) return;
      const next = mapActivityToState(reason);
      if (next == null) return;
      orb.setState(next);
    },
    onHybridState: (state, reason) => {
      if (stopping && state !== "STOPPING" && state !== "LOCAL_AMBIENT") return;
      if (state === "LOCAL_COMMAND") {
        stopping = false;
        stoppingReason = "";
        activationPending = true;
        caption.textContent = "Activating…";
        orb.setState(ORB_STATES.CONNECTING);
        return;
      }
      if (state === "CLOUD_CONVERSATION") {
        stopping = false;
        stoppingReason = "";
        activationPending = false;
        caption.textContent = "Listening";
        orb.setState(ORB_STATES.LISTENING);
        return;
      }
      if (state === "STOPPING") {
        setStoppingState(reason);
        return;
      }
      if (state === "LOCAL_AMBIENT") {
        stopping = false;
        stoppingReason = "";
        activationPending = false;
        orb.setState(restingState());
        caption.textContent = /failed|cancelled|timeout/i.test(reason || "")
          ? "Activation unavailable"
          : CAPTION_BY_STATE[restingState()];
      }
    },
  });

  function cleanup() {
    subscription?.close();
    orb.dispose();
    if (transcriptPreviewTimer !== null) {
      window.clearInterval(transcriptPreviewTimer);
      transcriptPreviewTimer = null;
    }
    transcriptPreviewText.textContent = "";
    document.removeEventListener("keydown", onActivationKey);
    if (defaultAction) {
      defaultAction.hidden = true;
      defaultAction.removeEventListener("click", onSetDefault);
    }
  }

  function renderTranscriptPreview(result) {
    if (result?.enabled !== true) {
      transcriptPreview.hidden = true;
      transcriptPreviewText.textContent = "";
      return false;
    }
    transcriptPreview.hidden = false;
    if (result.available !== true) {
      transcriptPreviewText.textContent = "Unavailable";
    } else {
      transcriptPreviewText.textContent = result.text || "No local transcript yet.";
    }
    return true;
  }

  async function refreshTranscriptPreview() {
    if (signal.aborted || transcriptPreviewRequestPending) return;
    transcriptPreviewRequestPending = true;
    try {
      const result = await getTranscriptPreview();
      if (signal.aborted) return;
      if (!renderTranscriptPreview(result) && transcriptPreviewTimer !== null) {
        window.clearInterval(transcriptPreviewTimer);
        transcriptPreviewTimer = null;
      }
    } catch {
      if (!signal.aborted) {
        transcriptPreview.hidden = true;
        transcriptPreviewText.textContent = "";
      }
    } finally {
      transcriptPreviewRequestPending = false;
    }
  }

  function restingState() {
    return muted ? ORB_STATES.MUTED : ORB_STATES.IDLE;
  }

  function stoppingCaption(reason) {
    return /failed|timeout|incomplete|error/i.test(reason || "")
      ? "Cleanup unavailable"
      : "Stopping…";
  }

  function setStoppingState(reason) {
    stopping = true;
    stoppingReason = reason || "";
    activationPending = true;
    orb.setState(ORB_STATES.CONNECTING);
    caption.textContent = stoppingCaption(stoppingReason);
  }

  async function refreshHybridState() {
    if (!hybridEnabled || signal.aborted) return;
    try {
      const status = await getStatus();
      if (signal.aborted) return;
      if (status?.hybrid_state === "STOPPING") {
        setStoppingState(status.backend_error || "");
      } else if (stopping && status?.hybrid_state === "LOCAL_AMBIENT") {
        stopping = false;
        stoppingReason = "";
        activationPending = false;
      }
    } catch {
      // Keep the last known lifecycle state until the event stream catches up.
    }
  }

  async function onMicTap() {
    if (!micReady || togglePending) return;
    togglePending = true;
    try {
      const data = await setMicMuted(!muted);
      muted = Boolean(data?.muted);
    } catch (error) {
      if (!signal.aborted && !stopping) {
        caption.textContent = `Failed to toggle the microphone: ${error?.message || error}`;
      }
      return;
    } finally {
      togglePending = false;
    }
    if (signal.aborted) return;
    if (stopping) {
      orb.setState(ORB_STATES.CONNECTING);
      caption.textContent = stoppingCaption(stoppingReason);
      syncMicAria();
      return;
    }
    orb.setState(restingState());
    // setState skips unchanged states, so set the caption explicitly
    caption.textContent = CAPTION_BY_STATE[restingState()];
    syncMicAria();
  }

  async function onActivationKey(event) {
    if (
      signal.aborted ||
      !hybridEnabled ||
      stopping ||
      activationPending ||
      event.repeat ||
      event.defaultPrevented ||
      event.altKey ||
      event.ctrlKey ||
      event.metaKey
    ) {
      return;
    }
    if (event.key !== " " && event.code !== "Space" && event.key !== "Enter") return;
    const target = event.target;
    if (
      target instanceof HTMLElement &&
      (target.isContentEditable || ["INPUT", "TEXTAREA", "SELECT", "BUTTON"].includes(target.tagName))
    ) {
      return;
    }
    if (target instanceof Element && target.closest("[contenteditable='true']")) {
      return;
    }
    event.preventDefault();
    activationPending = true;
    caption.textContent = "Activating…";
    try {
      const result = await activateConversation();
      if (signal.aborted || stopping) return;
      if (!result?.ok) {
        caption.textContent = result?.error || "Activation unavailable";
        return;
      }
      caption.textContent = "Listening";
      orb.setState(ORB_STATES.LISTENING);
    } catch (error) {
      if (!signal.aborted && !stopping) caption.textContent = `Activation failed: ${error?.message || error}`;
    } finally {
      activationPending = stopping;
    }
  }

  async function refreshPersonalityState() {
    const personalityState = await fetchPersonalityState();
    if (signal.aborted || personalityState == null) return;
    activePersonality = personalityState.current;
    setPersonality(personalityState.current);
    const shouldHide = personalityState.locked || personalityState.current === personalityState.startup;
    if (defaultAction) {
      defaultAction.hidden = shouldHide;
    }
  }

  async function onSetDefault() {
    if (!defaultAction || !activePersonality) return;
    defaultAction.disabled = true;
    caption.textContent = `Saving "${prettifyProfileName(activePersonality)}" as default...`;
    try {
      await applyPersonality(activePersonality, { persist: true });
      if (signal.aborted) return;
      defaultAction.hidden = true;
      caption.textContent = `"${prettifyProfileName(activePersonality)}" will be used at startup.`;
    } catch (error) {
      if (!signal.aborted) {
        caption.textContent = `Failed to save default: ${error?.message || error}`;
      }
    } finally {
      defaultAction.disabled = false;
    }
  }

  function syncMicAria() {
    if (!micReady) {
      orb.root.setAttribute("aria-pressed", "false");
      orb.root.setAttribute("aria-label", "Loading microphone state");
      return;
    }
    orb.root.setAttribute("aria-pressed", String(!muted));
    orb.root.setAttribute("aria-label", muted ? "Unmute microphone" : "Mute microphone");
  }
}

async function fetchPersonalityState() {
  try {
    const data = await listPersonalities();
    const current = data?.current;
    if (!current) return null;
    return {
      current,
      startup: data?.startup || "default",
      locked: Boolean(data?.locked),
    };
  } catch {
    return null;
  }
}

function subscribeConversationEvents({ onActivity, onHybridState, onReady } = {}) {
  if (typeof onActivity !== "function") {
    throw new TypeError("subscribeConversationEvents: onActivity is required");
  }

  // Activity reasons now arrive as conversation.activity notifications over the
  // /rpc WebSocket; the shared client (api.js) owns reconnection.
  const unsubscribe = subscribe("conversation.activity", (params) => {
    const reason = (params?.reason || "").trim();
    if (reason) onActivity(reason);
  });
  const unsubscribeHybridState = subscribe("conversation.hybrid_state", (params) => {
    const state = (params?.state || "").trim();
    if (state && typeof onHybridState === "function") {
      onHybridState(state, (params?.reason || "").trim());
    }
  });

  // The socket connects lazily, so schedule the initial mic and orb sync.
  if (typeof onReady === "function") Promise.resolve().then(onReady);

  return {
    close() {
      unsubscribe();
      unsubscribeHybridState();
    },
  };
}
