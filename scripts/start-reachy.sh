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
WHISPER_PORT="${WHISPER_PORT:-8766}"
WHISPER_URL="http://127.0.0.1:${WHISPER_PORT}/v1/models"
WHISPER_LOG="${WHISPER_LOG:-${TMPDIR:-/tmp}/reachy-whisper.log}"
WHISPER_READY_TIMEOUT_S="${WHISPER_READY_TIMEOUT_S:-180}"

if [[ $# -eq 0 ]]; then
  set -- --media-backend local
fi

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
  transformers serve --host 127.0.0.1 --port "$WHISPER_PORT" "$WHISPER_MODEL" >"$WHISPER_LOG" 2>&1 &
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
