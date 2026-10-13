"""Tests for camera frame downscaling before cloud upload."""

import pytest

from reachy_mini_conversation_app.camera_frames import (
    MODEL_FRAME_MAX_SIDE,
    fit_size,
    jpeg_size,
    fit_jpeg_for_model,
)


def _gst_jpeg(width: int, height: int) -> bytes:
    """Encode one GStreamer test-pattern frame, skipping when GStreamer is unavailable."""
    gi = pytest.importorskip("gi")
    gi.require_version("Gst", "1.0")
    from gi.repository import Gst

    Gst.init(None)
    pipeline = Gst.parse_launch(
        f"videotestsrc num-buffers=1 ! video/x-raw,width={width},height={height} ! jpegenc ! appsink name=sink"
    )
    pipeline.set_state(Gst.State.PLAYING)
    sample = pipeline.get_by_name("sink").emit("try-pull-sample", 1_000_000_000)
    pipeline.set_state(Gst.State.NULL)
    assert sample is not None
    buffer = sample.get_buffer()
    return bytes(buffer.extract_dup(0, buffer.get_size()))


def test_fit_size_keeps_aspect_ratio_and_even_dimensions() -> None:
    """A 720p frame fits 768 on its long side; small frames are unchanged."""
    assert fit_size(1280, 720, 768) == (768, 432)
    assert fit_size(720, 1280, 768) == (432, 768)
    assert fit_size(640, 480, 768) == (640, 480)


def test_large_frame_is_downscaled_to_fit_the_model_size() -> None:
    """A 1280x720 camera frame is re-encoded at 768x432 and gets smaller."""
    original = _gst_jpeg(1280, 720)
    assert jpeg_size(original) == (1280, 720)

    scaled = fit_jpeg_for_model(original)

    assert jpeg_size(scaled) == (768, 432)
    assert len(scaled) < len(original)


def test_small_frame_is_sent_unchanged() -> None:
    """Frames already within the model size are not re-encoded."""
    original = _gst_jpeg(640, 360)

    assert fit_jpeg_for_model(original, MODEL_FRAME_MAX_SIDE) is original


def test_unreadable_bytes_are_passed_through() -> None:
    """Data without a JPEG frame header is left for the existing pipeline to handle."""
    assert jpeg_size(b"not a jpeg") is None
    assert fit_jpeg_for_model(b"not a jpeg") == b"not a jpeg"
    assert fit_jpeg_for_model(b"\xff\xd8jpeg\xff\xd9") == b"\xff\xd8jpeg\xff\xd9"


def test_downscale_failure_returns_the_original(monkeypatch: pytest.MonkeyPatch) -> None:
    """If GStreamer cannot re-encode, the original frame is still sent."""
    original = _gst_jpeg(1280, 720)
    from gi.repository import Gst

    def broken_parse_launch(_description: str) -> None:
        raise RuntimeError("no pipeline")

    monkeypatch.setattr(Gst, "parse_launch", broken_parse_launch)

    assert fit_jpeg_for_model(original) is original
