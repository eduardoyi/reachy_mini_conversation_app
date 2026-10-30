"""Tests for camera frame downscaling before cloud upload."""

import numpy as np
import pytest

from reachy_mini_conversation_app.camera_frames import (
    DIM_FRAME_MEAN,
    MODEL_FRAME_MAX_SIDE,
    fit_size,
    jpeg_size,
    encode_jpeg,
    decode_image,
    boost_dim_jpeg,
    boost_dim_frame,
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


def _night_frame() -> np.ndarray:
    """Return a frame with the camera's night levels (2026-10-30): black floor 28, room ~50, lit wall ~180."""
    frame = np.full((200, 200, 3), 50, dtype=np.uint8)
    frame[:20] = 28  # the camera never goes darker than its grey black level
    frame[20:50] = 180  # the wall under the ceiling spots
    frame[80:120, 80:120] = 34  # the backlit face...
    frame[90:110, 90:110] = 38  # ...and the little detail it still has
    frame[0, :20] = 250  # a lamp
    return frame


def test_a_dim_frame_gets_brighter_with_more_contrast() -> None:
    """At night the frame is lifted and the detail in a dark face is pulled apart."""
    frame = _night_frame()
    boosted = boost_dim_frame(frame)
    assert boosted.mean() > frame.mean() + 10
    assert int(boosted[100, 100, 0]) - int(boosted[82, 82, 0]) > 2 * (38 - 34)
    assert boosted[10, 10, 0] == 0  # the grey black level becomes black


def test_a_well_lit_frame_is_left_alone() -> None:
    """Daytime frames reach the face recognizer and the model as the camera took them."""
    frame = np.full((120, 160, 3), int(DIM_FRAME_MEAN) + 30, dtype=np.uint8)
    frame[:60] = 20
    frame[60:] = 220
    assert boost_dim_frame(frame) is frame


def test_a_dark_room_is_not_stretched_into_noise() -> None:
    """With the lights off there is nothing to stretch: the frame stays dark."""
    rng = np.random.default_rng(0)
    frame = rng.integers(0, 12, size=(120, 160, 3), dtype=np.uint8)
    assert boost_dim_frame(frame) is frame
    assert boost_dim_frame(None) is None


def test_boosting_a_jpeg_keeps_anything_unreadable_or_bright_as_is() -> None:
    """Bytes that are not a JPEG pass through untouched."""
    assert boost_dim_jpeg(b"not a jpeg") == b"not a jpeg"


def test_a_dim_jpeg_comes_back_brighter() -> None:
    """The model's view of a dim room is re-encoded brighter; a bright JPEG is returned as the same bytes."""
    pytest.importorskip("gi")
    dim = encode_jpeg(_night_frame())
    if dim is None:
        pytest.skip("GStreamer JPEG encoder unavailable")
    brighter = decode_image(boost_dim_jpeg(dim))
    assert brighter is not None
    assert brighter.mean() > decode_image(dim).mean() + 10
    bright = encode_jpeg(np.full((120, 160, 3), 200, dtype=np.uint8))
    assert bright is not None
    assert boost_dim_jpeg(bright) is bright
