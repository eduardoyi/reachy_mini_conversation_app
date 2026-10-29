"""Remember which way recent speech came from, using the daemon's microphone-array direction.

The mic array sits in the head next to the camera, so its reading is relative to where the head
faces; each reading is turned into a room direction with the head's yaw at the moment it is taken.
"""

from __future__ import annotations
import json
import math
import time
import logging
import threading
import statistics
import urllib.request
from typing import Any, Callable
from collections import deque


logger = logging.getLogger(__name__)

DOA_PATH = "/api/state/doa"
DOA_POLL_INTERVAL_S = 0.1
DOA_HISTORY_S = 3.0
DOA_REQUEST_TIMEOUT_S = 0.5
# After a failed request, wait this long before trying again.
DOA_ERROR_BACKOFF_S = 2.0
# Speech that started this recently decides where to turn.
SPEECH_WINDOW_S = 1.5
# Readings scatter: on 2026-10-29 a talking room gave -89 to +13 degrees within seconds, and two stray readings
# turned Reachy to -150 degrees, away from the person in front of him. A turn needs several that mostly agree.
MIN_SPEECH_SAMPLES = 3
AGREEMENT_RAD = math.radians(25.0)
MIN_AGREEMENT = 0.6
# Reachy's own voice is not someone speaking: readings while it plays, and briefly after (room echo), are dropped.
OWN_VOICE_TAIL_S = 0.5
# Furthest the robot turns toward a voice (the body can rotate 160 degrees either way).
MAX_SPEAKER_YAW = math.radians(150.0)

DoaFetcher = Callable[[], "dict[str, Any] | None"]


def doa_angle_to_yaw(angle: float) -> float:
    """Convert a DoA angle (0 = left, pi/2 = front, pi = right) to yaw relative to the head (positive = left).

    The array cannot tell front from back, so every direction maps to the front half.
    """
    return max(-math.pi / 2, min(math.pi / 2, math.pi / 2 - angle))


def room_yaw(head_yaw: float, relative_yaw: float) -> float:
    """Return the room direction of a sound heard ``relative_yaw`` from a head facing ``head_yaw``, within reach."""
    # No wrap-around: the body cannot turn past its limit, so a sound beyond it means "as far as possible".
    return max(-MAX_SPEAKER_YAW, min(MAX_SPEAKER_YAW, head_yaw + relative_yaw))


class SpeakerDirection:
    """Poll the daemon's sound direction in the background and report where speech recently came from."""

    # When Reachy's queued speech ends (on ``clock``'s timeline); set once the audio output exists.
    own_voice_until: Callable[[], float] | None = None

    def __init__(
        self,
        daemon_url: str,
        *,
        head_yaw: Callable[[], float] | None = None,
        fetch: DoaFetcher | None = None,
        clock: Callable[[], float] = time.monotonic,
        poll_interval_s: float = DOA_POLL_INTERVAL_S,
    ) -> None:
        """Prepare a poller for the daemon at ``daemon_url`` (e.g. ``http://localhost:8000``).

        ``head_yaw`` returns where the head faces in the room (radians); without it the head is
        assumed to face forward.
        """
        self._url = daemon_url.rstrip("/") + DOA_PATH
        self._head_yaw = head_yaw
        self._fetch = fetch if fetch is not None else self._fetch_http
        self._clock = clock
        self._poll_interval_s = poll_interval_s
        self._lock = threading.Lock()
        self._speech: deque[tuple[float, float]] = deque()
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._failing = False

    def start(self) -> None:
        """Start polling; a no-op when already running."""
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._run, name="speaker-direction", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        """Stop polling and wait for the thread to exit."""
        self._stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout=2.0)
            self._thread = None

    def recent_speech_yaw(self, window_s: float = SPEECH_WINDOW_S) -> float | None:
        """Return the median room yaw of speech heard in the last ``window_s`` seconds, or None.

        None also when the readings disagree: fewer than ``MIN_AGREEMENT`` of them within ``AGREEMENT_RAD``.
        """
        cutoff = self._clock() - window_s
        with self._lock:
            yaws = [yaw for timestamp, yaw in self._speech if timestamp >= cutoff]
        if len(yaws) < MIN_SPEECH_SAMPLES:
            return None
        median = float(statistics.median(yaws))
        agreeing = sum(abs(yaw - median) <= AGREEMENT_RAD for yaw in yaws)
        if agreeing < MIN_AGREEMENT * len(yaws):
            logger.info("Speaker direction unclear: %d of %d readings agree; not turning", agreeing, len(yaws))
            return None
        return median

    def poll_once(self) -> bool:
        """Read the direction once and record it if speech was detected; return whether the read succeeded."""
        try:
            reading = self._fetch()
        except Exception as error:
            if not self._failing:
                logger.warning("Speaker direction unavailable: %s", type(error).__name__)
                self._failing = True
            return False
        if self._failing:
            logger.info("Speaker direction available again")
            self._failing = False
        now = self._clock()
        speech_yaw = None
        if reading is not None and reading.get("speech_detected") and not self._own_voice_playing(now):
            speech_yaw = room_yaw(self._current_head_yaw(), doa_angle_to_yaw(float(reading["angle"])))
        with self._lock:
            if speech_yaw is not None:
                self._speech.append((now, speech_yaw))
            while self._speech and now - self._speech[0][0] > DOA_HISTORY_S:
                self._speech.popleft()
        return True

    def _own_voice_playing(self, now: float) -> bool:
        if self.own_voice_until is None:
            return False
        try:
            return now < float(self.own_voice_until()) + OWN_VOICE_TAIL_S
        except Exception as error:
            logger.debug("Own voice timing unavailable: %s", type(error).__name__)
            return False

    def _current_head_yaw(self) -> float:
        if self._head_yaw is None:
            return 0.0
        try:
            return float(self._head_yaw())
        except Exception as error:
            logger.debug("Head yaw unavailable; assuming forward: %s", type(error).__name__)
            return 0.0

    def _run(self) -> None:
        while not self._stop_event.is_set():
            ok = self.poll_once()
            self._stop_event.wait(self._poll_interval_s if ok else DOA_ERROR_BACKOFF_S)

    def _fetch_http(self) -> dict[str, Any] | None:
        with urllib.request.urlopen(self._url, timeout=DOA_REQUEST_TIMEOUT_S) as response:
            payload = json.loads(response.read())
        return payload if isinstance(payload, dict) else None
