import json
import threading
from types import SimpleNamespace
from typing import Any
from pathlib import Path

import numpy as np
import pytest
from numpy.typing import NDArray

from reachy_mini_conversation_app import photographer, photo_archive
from reachy_mini_conversation_app.whatsapp import SendResult
from reachy_mini_conversation_app.photographer import BURST_FRAMES, FaceFinder, sharpness, take_photo


def _sharp(seed: int) -> NDArray[np.uint8]:
    return np.random.default_rng(seed).integers(0, 256, (120, 160, 3), dtype=np.uint8)


def _blurred(seed: int) -> NDArray[np.uint8]:
    frame = _sharp(seed).astype(np.float32)
    for _ in range(4):
        frame = (frame + np.roll(frame, 1, 0) + np.roll(frame, 1, 1) + np.roll(frame, -1, 0)) / 4
    return frame.astype(np.uint8)


def _tagged(frame: NDArray[np.uint8], tag: int) -> NDArray[np.uint8]:
    frame = frame.copy()
    frame[0, 0, 0] = tag
    return frame


class Camera:
    """Hands out ``frames`` in turn, as the SDK media manager's ``get_frame``; None is a missed frame."""

    def __init__(self, frames: list[NDArray[np.uint8] | None]) -> None:
        """Serve these frames, then only missed frames."""
        self.frames = list(frames)

    def get_frame(self) -> NDArray[np.uint8] | None:
        """Return the next frame."""
        return self.frames.pop(0) if self.frames else None


class Detector:
    """Finds as many faces as the frame's top-left pixel says (stands in for YuNet)."""

    def __init__(self, faces: dict[int, int]) -> None:
        """Map a frame's tag (pixel [0, 0, 0]) to its face count."""
        self.faces = faces

    def detect(self, frame: NDArray[np.uint8]) -> list[object]:
        """Return one placeholder per face."""
        return [object()] * self.faces.get(int(frame[0, 0, 0]), 0)


class FakeTime:
    """A clock that only moves when the burst sleeps."""

    def __init__(self) -> None:
        """Start at zero."""
        self.now = 0.0
        self.waits: list[float] = []

    def sleep(self, seconds: float) -> None:
        """Advance the clock."""
        self.waits.append(seconds)
        self.now += seconds

    def clock(self) -> float:
        """Return the current time."""
        return self.now


def _take(camera: Camera, faces: FaceFinder, fake_time: FakeTime | None = None) -> bytes | None:
    fake_time = fake_time or FakeTime()
    return take_photo(camera, faces, sleep=fake_time.sleep, clock=fake_time.clock)


def _no_faces() -> FaceFinder:
    return FaceFinder(lambda: Detector({}))


@pytest.fixture
def encoded(monkeypatch: pytest.MonkeyPatch) -> list[NDArray[np.uint8]]:
    """Record the frame chosen for the JPEG instead of encoding it."""
    chosen: list[NDArray[np.uint8]] = []

    def encode(frame: NDArray[np.uint8], quality: int = 0) -> bytes:
        chosen.append(frame)
        return b"jpeg"

    monkeypatch.setattr(photographer, "encode_jpeg", encode)
    return chosen


def test_a_sharper_frame_scores_higher() -> None:
    """A noisy, detailed frame scores far above the same frame blurred."""
    assert sharpness(_sharp(1)) > 10 * sharpness(_blurred(1))


def test_the_frame_with_the_most_faces_wins_even_if_blurrier(encoded: list[NDArray[np.uint8]]) -> None:
    """Everyone in the shot matters more than sharpness."""
    frames: list[Any] = [_tagged(_sharp(i), 1) for i in range(BURST_FRAMES - 1)] + [_tagged(_blurred(9), 2)]
    faces = FaceFinder(lambda: Detector({1: 1, 2: 2}))

    assert _take(Camera(frames), faces) == b"jpeg"
    assert encoded[0] is frames[-1]


def test_with_as_many_faces_the_sharpest_frame_wins(encoded: list[NDArray[np.uint8]]) -> None:
    """Among frames with as many faces, the least blurred is kept."""
    frames: list[Any] = [_blurred(i) for i in range(BURST_FRAMES)]
    frames[2] = _sharp(2)

    _take(Camera(frames), _no_faces())
    assert encoded[0] is frames[2]


def test_without_a_detector_the_sharpest_frame_wins(encoded: list[NDArray[np.uint8]]) -> None:
    """A detector that cannot load is tried once; sharpness then decides."""
    created: list[int] = []

    def broken() -> Any:
        created.append(1)
        raise OSError("model download failed")

    frames: list[Any] = [_blurred(i) for i in range(BURST_FRAMES)]
    frames[3] = _sharp(3)
    faces = FaceFinder(broken)
    faces.warm_up()

    _take(Camera(frames), faces)
    assert encoded[0] is frames[3]
    assert created == [1]  # the failure is remembered, not retried on every frame


def test_a_detector_error_on_a_frame_falls_back_to_sharpness(encoded: list[NDArray[np.uint8]]) -> None:
    """A detection error on a frame does not lose the photo."""

    class Failing:
        def detect(self, frame: NDArray[np.uint8]) -> list[object]:
            raise RuntimeError("onnx")

    frames: list[Any] = [_blurred(i) for i in range(BURST_FRAMES)]
    frames[1] = _sharp(1)
    _take(Camera(frames), FaceFinder(Failing))
    assert encoded[0] is frames[1]


def test_a_photo_does_not_wait_for_the_detector_warm_up(encoded: list[NDArray[np.uint8]]) -> None:
    """While the detector is still loading, the photo is chosen by sharpness at once."""
    loading = threading.Event()
    release = threading.Event()

    def slow_detector() -> Any:
        loading.set()
        release.wait(5)
        return Detector({})

    faces = FaceFinder(slow_detector)
    warm_up = threading.Thread(target=faces.warm_up)
    warm_up.start()
    try:
        assert loading.wait(5)
        frames: list[Any] = [_blurred(i) for i in range(BURST_FRAMES)]
        frames[0] = _sharp(0)
        assert _take(Camera(frames), faces) == b"jpeg"
        assert encoded[0] is frames[0]
    finally:
        release.set()
        warm_up.join(5)


def test_missed_frames_are_tried_again_until_the_burst_is_full(encoded: list[NDArray[np.uint8]]) -> None:
    """Missed camera frames are retried, so the burst still has five frames."""
    sharp = _sharp(4)
    camera = Camera([None, None, sharp] + [_blurred(i) for i in range(BURST_FRAMES - 1)])

    assert _take(camera, _no_faces()) == b"jpeg"
    assert encoded[0] is sharp
    assert camera.frames == []


def test_no_frame_in_the_whole_window_is_no_photo(encoded: list[NDArray[np.uint8]]) -> None:
    """A camera that gives nothing for the whole window gives no photo."""
    fake_time = FakeTime()
    assert _take(Camera([]), _no_faces(), fake_time) is None
    assert encoded == []
    assert fake_time.now >= photographer.BURST_WINDOW_S


def test_the_burst_waits_between_frames(encoded: list[NDArray[np.uint8]]) -> None:
    """Frames are spread over the burst, not taken back to back."""
    fake_time = FakeTime()
    _take(Camera([_sharp(i) for i in range(BURST_FRAMES)]), _no_faces(), fake_time)
    assert fake_time.waits == [photographer.BURST_INTERVAL_S] * (BURST_FRAMES - 1)


def test_a_failed_encode_is_tried_once_more(monkeypatch: pytest.MonkeyPatch) -> None:
    """A JPEG encode that times out is tried once more, then given up."""
    results: list[bytes | None] = [None, b"jpeg"]
    monkeypatch.setattr(photographer, "encode_jpeg", lambda frame, quality=0: results.pop(0))
    assert _take(Camera([_sharp(i) for i in range(BURST_FRAMES)]), _no_faces()) == b"jpeg"

    results[:] = [None, None]
    assert _take(Camera([_sharp(i) for i in range(BURST_FRAMES)]), _no_faces()) is None


@pytest.mark.asyncio
async def test_a_shared_photo_is_kept_for_the_gallery(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """After sending, the photo is kept with who it was for and how the send went."""
    monkeypatch.setattr(photo_archive.config, "PHOTO_ARCHIVE_DIR", str(tmp_path))

    class WhatsApp:
        async def send_image(self, chat: str, jpeg: bytes) -> SendResult:
            return SendResult.SENT

    deps = SimpleNamespace(reachy_mini=SimpleNamespace(media=None), display=None, whatsapp=WhatsApp())
    result = await photographer.take_and_share(deps, "1@g.us", "Tío Sergio", take=lambda media, faces: b"jpeg")
    assert result is SendResult.SENT
    [kept] = tmp_path.glob("*.jpg")
    assert kept.read_bytes() == b"jpeg"
    assert json.loads(kept.with_suffix(".json").read_text())["who"] == "Tío Sergio"
