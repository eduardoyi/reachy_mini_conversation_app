"""Take a photo for people: a short burst, keeping the frame with the most faces, then the sharpest.

Children rarely hold still for a countdown; of a few frames a tenth of a second apart, one usually has
everyone in it and less blur. Nothing is written to disk.
"""

import time
import logging
import threading
from typing import Any, Final, Callable

import numpy as np
from numpy.typing import NDArray

from reachy_mini_conversation_app.camera_frames import encode_jpeg, capture_frame


logger = logging.getLogger(__name__)

BURST_FRAMES: Final[int] = 5
BURST_INTERVAL_S: Final[float] = 0.12
# The SDK pulls a frame with a 20 ms timeout, so a missed frame is normal: try again soon, within the window.
MISSED_FRAME_RETRY_S: Final[float] = 0.03
BURST_WINDOW_S: Final[float] = 1.5
PHOTO_JPEG_QUALITY: Final[int] = 92
# Faces are looked for at half size, as face recognition does; sharpness at a quarter.
DETECT_DOWNSCALE: Final[int] = 2
SHARPNESS_DOWNSCALE: Final[int] = 4


def _sdk_detector() -> Any:
    from reachy_mini.vision.face_detector import FaceDetector

    return FaceDetector()


class FaceFinder:
    """Count faces in frames with the SDK's detector, created once; thread-safe."""

    def __init__(self, detector_factory: Callable[[], Any] = _sdk_detector) -> None:
        """Use the SDK detector unless ``detector_factory`` gives another (tests)."""
        self._detector_factory = detector_factory
        self._detector: Any = None
        self._broken = False
        self._lock = threading.Lock()

    def warm_up(self) -> None:
        """Load (and on first run download) the detector, so the first photo does not wait for it."""
        with self._lock:
            self._load_locked()

    def count(self, frame: NDArray[np.uint8]) -> int | None:
        """Return the number of faces in ``frame``, or None when the detector is unavailable or still loading."""
        # A photo never waits for the warm-up (a model download can take a while).
        if not self._lock.acquire(blocking=False):
            return None
        try:
            detector = self._load_locked()
            if detector is None:
                return None
            return len(detector.detect(np.ascontiguousarray(frame[::DETECT_DOWNSCALE, ::DETECT_DOWNSCALE])))
        except Exception as error:
            logger.warning("Face detection failed on a photo frame; choosing by sharpness: %s", error)
            return None
        finally:
            self._lock.release()

    def _load_locked(self) -> Any:
        if self._detector is None and not self._broken:
            try:
                self._detector = self._detector_factory()
            except Exception as error:
                self._broken = True
                logger.warning("Face detector unavailable; photos are chosen by sharpness only: %s", error)
        return self._detector


def sharpness(frame: NDArray[np.uint8]) -> float:
    """Return the variance of the Laplacian of the downscaled gray image: higher is sharper."""
    gray = frame[::SHARPNESS_DOWNSCALE, ::SHARPNESS_DOWNSCALE].astype(np.float32).mean(axis=2)
    laplacian = 4 * gray[1:-1, 1:-1] - gray[:-2, 1:-1] - gray[2:, 1:-1] - gray[1:-1, :-2] - gray[1:-1, 2:]
    return float(laplacian.var())


def take_photo(
    media: Any,
    faces: FaceFinder,
    *,
    sleep: Callable[[float], None] = time.sleep,
    clock: Callable[[], float] = time.monotonic,
) -> bytes | None:
    """Take a burst from the camera and return the best frame as a JPEG, or None if the camera gave nothing."""
    started_at = time.perf_counter()
    window_ends_at = clock() + BURST_WINDOW_S
    frames: list[NDArray[np.uint8]] = []
    while len(frames) < BURST_FRAMES and clock() < window_ends_at:
        frame = capture_frame(media)
        if frame is None:
            sleep(MISSED_FRAME_RETRY_S)
            continue
        frames.append(frame)
        if len(frames) < BURST_FRAMES:
            sleep(BURST_INTERVAL_S)
    if not frames:
        logger.warning("Photo: the camera gave no frame")
        return None
    scored = [((faces.count(frame) or 0), sharpness(frame), index) for index, frame in enumerate(frames)]
    face_count, _, best = max(scored)
    # The encoder's pull can time out on a busy computer; once more before giving up.
    jpeg = encode_jpeg(frames[best], quality=PHOTO_JPEG_QUALITY) or encode_jpeg(
        frames[best], quality=PHOTO_JPEG_QUALITY
    )
    if jpeg is None:
        logger.warning("Photo: the frame could not be encoded")
        return None
    logger.info(
        "Photo taken frames=%d best=%d faces=%d bytes=%d ms=%.0f",
        len(frames),
        best,
        face_count,
        len(jpeg),
        (time.perf_counter() - started_at) * 1000,
    )
    return jpeg
