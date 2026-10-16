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


def test_jpeg_encode_and_decode_keep_colours_and_size() -> None:
    """Saved photos are standard JPEGs: encoding then decoding keeps the colours (RGB-coded ones read back green)."""
    pytest.importorskip("gi")
    import numpy as np

    from reachy_mini_conversation_app.camera_frames import encode_jpeg, decode_image

    for height, width in [(48, 64), (121, 99)]:
        image = np.zeros((height, width, 3), np.uint8)
        image[:, : width // 2] = (255, 0, 0)  # blue left half, BGR
        jpeg = encode_jpeg(image, quality=95)
        assert jpeg is not None and jpeg_size(jpeg) == (width, height)
        decoded = decode_image(jpeg)
        assert decoded is not None and decoded.shape == (height, width, 3)
        assert np.allclose(decoded[height // 2, width // 4], (255, 0, 0), atol=40)
        assert np.allclose(decoded[height // 2, 3 * width // 4], (0, 0, 0), atol=40)


def test_decode_image_reads_png_and_rejects_garbage() -> None:
    """PNG uploads decode too; bytes that are not an image return None."""
    gi = pytest.importorskip("gi")
    gi.require_version("Gst", "1.0")
    from gi.repository import Gst

    from reachy_mini_conversation_app.camera_frames import decode_image

    Gst.init(None)
    pipeline = Gst.parse_launch(
        "videotestsrc num-buffers=1 ! video/x-raw,width=64,height=48 ! pngenc ! appsink name=sink"
    )
    pipeline.set_state(Gst.State.PLAYING)
    sample = pipeline.get_by_name("sink").emit("try-pull-sample", 1_000_000_000)
    pipeline.set_state(Gst.State.NULL)
    buffer = sample.get_buffer()

    decoded = decode_image(bytes(buffer.extract_dup(0, buffer.get_size())))

    assert decoded is not None and decoded.shape == (48, 64, 3)
    assert decode_image(b"not an image") is None
