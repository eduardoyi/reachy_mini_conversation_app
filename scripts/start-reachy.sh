#!/usr/bin/env bash
# Start the local Whisper STT server (for hybrid ambient context) if it is not already
# running, then the conversation app. Whisper is stopped on exit only if this script
# started it. Arguments are passed to the app (default: --media-backend local).
#
#   scripts/start-reachy.sh
#   WHISPER_MODEL=openai/whisper-large-v3-turbo scripts/start-reachy.sh --media-backend local
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# Multilingual by default; use an ".en" model for an English-only household.
WHISPER_MODEL="${WHISPER_MODEL:-openai/whisper-small}"
# More accurate model for what is said to Reachy right after his name (Reachy's own server only); set
# LOCAL_STT_REQUEST_MODEL to the same name in .env. Empty disables it.
WHISPER_REQUEST_MODEL="${WHISPER_REQUEST_MODEL-openai/whisper-large-v3-turbo}"
WHISPER_PORT="${WHISPER_PORT:-8766}"
WHISPER_URL="http://127.0.0.1:${WHISPER_PORT}/v1/models"
# Appended to, so a restart keeps the earlier timings.
WHISPER_LOG="${WHISPER_LOG:-${TMPDIR:-/tmp}/reachy-whisper.log}"
WHISPER_READY_TIMEOUT_S="${WHISPER_READY_TIMEOUT_S:-180}"
# Reachy's own Whisper server (scripts/whisper_server.py) bounds the work per clip so background
# chatter cannot jam it; it runs with the Python of the `transformers` uv tool. Set
# WHISPER_SERVER=transformers to use plain `transformers serve` instead.
WHISPER_SERVER="${WHISPER_SERVER:-reachy}"
WHISPER_PYTHON="${WHISPER_PYTHON:-$(uv tool dir 2>/dev/null)/transformers/bin/python}"
# The camera's factory image is dark (brightness 0) and flat. A small brightness lift plus
# higher gamma brightens mid-tones without washing out shadows; contrast and sharpness get a
# nudge. Ranges: brightness -64..64, gamma 80..160, contrast 0..95, sharpness 0..7. Set one
# empty to leave it alone, or REACHY_CAMERA_TUNING=0 to leave the camera unchanged.
REACHY_CAMERA_TUNING="${REACHY_CAMERA_TUNING:-1}"
REACHY_CAMERA_BRIGHTNESS="${REACHY_CAMERA_BRIGHTNESS-12}"
REACHY_CAMERA_GAMMA="${REACHY_CAMERA_GAMMA-130}"
REACHY_CAMERA_CONTRAST="${REACHY_CAMERA_CONTRAST-1}"
REACHY_CAMERA_SHARPNESS="${REACHY_CAMERA_SHARPNESS-3}"
UVC_CTL_DIR="${XDG_CACHE_HOME:-$HOME/.cache}/reachy-mini-conversation-app"

if [[ $# -eq 0 ]]; then
  set -- --media-backend local
fi

tune_camera() {
  [[ "$(uname -s)" == "Darwin" && "$REACHY_CAMERA_TUNING" != "0" ]] || return 0
  local src="$REPO_DIR/scripts/uvc-ctl.c" bin="$UVC_CTL_DIR/uvc-ctl"
  if [[ ! -x "$bin" || "$src" -nt "$bin" ]]; then
    mkdir -p "$UVC_CTL_DIR"
    if ! clang -O2 -o "$bin" "$src" -framework IOKit -framework CoreFoundation 2>/dev/null; then
      echo "Could not build $src (needs Xcode Command Line Tools); camera unchanged" >&2
      return 0
    fi
  fi
  # Reachy Mini camera 38fb:1002, processing unit 2; UVC selectors per control.
  local name selector value applied=()
  for name in brightness:2 contrast:3 sharpness:8 gamma:9; do
    selector="${name#*:}"
    name="${name%%:*}"
    case "$name" in
      brightness) value="$REACHY_CAMERA_BRIGHTNESS" ;;
      contrast) value="$REACHY_CAMERA_CONTRAST" ;;
      sharpness) value="$REACHY_CAMERA_SHARPNESS" ;;
      gamma) value="$REACHY_CAMERA_GAMMA" ;;
    esac
    [[ -n "$value" ]] || continue
    if ! "$bin" 0x38fb 0x1002 2 "$selector" set "$value"; then
      echo "Could not set camera $name; is the robot connected?" >&2
      return 0
    fi
    applied+=("$name=$value")
  done
  echo "Camera tuned: ${applied[*]}"
}
tune_camera

whisper_pid=""
cleanup() {
  if [[ -n "$whisper_pid" ]] && kill -0 "$whisper_pid" 2>/dev/null; then
    echo "Stopping Whisper (pid $whisper_pid)"
    kill "$whisper_pid" 2>/dev/null || true
    wait "$whisper_pid" 2>/dev/null || true
  fi
}
trap cleanup EXIT

if curl -fsS -m 2 "$WHISPER_URL" >/dev/null 2>&1; then
  echo "Whisper already running on port $WHISPER_PORT; reusing it"
elif ! command -v transformers >/dev/null 2>&1; then
  echo "Whisper not running and the 'transformers' CLI is not installed; starting without local STT context" >&2
  echo "Install it with: uv tool install --python 3.12 'transformers[serving]' --with librosa --with python-multipart" >&2
else
  echo "Starting Whisper ($WHISPER_MODEL) on port $WHISPER_PORT; log: $WHISPER_LOG"
  if [[ "$WHISPER_SERVER" != "transformers" && -x "$WHISPER_PYTHON" ]]; then
    request_args=()
    [[ -n "$WHISPER_REQUEST_MODEL" ]] && request_args=(--request-model "$WHISPER_REQUEST_MODEL")
    "$WHISPER_PYTHON" "$REPO_DIR/scripts/whisper_server.py" --host 127.0.0.1 --port "$WHISPER_PORT" \
      "${request_args[@]}" "$WHISPER_MODEL" >>"$WHISPER_LOG" 2>&1 &
  else
    transformers serve --host 127.0.0.1 --port "$WHISPER_PORT" "$WHISPER_MODEL" >>"$WHISPER_LOG" 2>&1 &
  fi
  whisper_pid=$!
  waited=0
  until curl -fsS -m 2 "$WHISPER_URL" >/dev/null 2>&1; do
    if ! kill -0 "$whisper_pid" 2>/dev/null; then
      echo "Whisper exited during startup; see $WHISPER_LOG" >&2
      exit 1
    fi
    if (( waited >= WHISPER_READY_TIMEOUT_S )); then
      echo "Whisper not ready after ${WHISPER_READY_TIMEOUT_S}s; see $WHISPER_LOG" >&2
      exit 1
    fi
    sleep 1
    waited=$((waited + 1))
  done
  echo "Whisper ready after ${waited}s"
fi

cd "$REPO_DIR"
.venv/bin/reachy-mini-conversation-app "$@"
