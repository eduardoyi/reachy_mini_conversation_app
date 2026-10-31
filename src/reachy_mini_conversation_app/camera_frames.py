"""Downscale camera JPEGs before they are sent to a cloud model, and brighten dim frames Reachy looks at."""

import struct
import logging
import threading
from typing import Any

from reachy_mini_conversation_app.privacy import PRIVACY


logger = logging.getLogger(__name__)

# Gemini Live recommends 768x768 frames; larger frames cost more tokens and time without helping.
MODEL_FRAME_MAX_SIDE = 768
MODEL_FRAME_JPEG_QUALITY = 85
_PULL_TIMEOUT_NS = 1_000_000_000
# The SDK's one-shot JPEG encoder is shared, so the camera tool and the live stream take turns.
_CAPTURE_LOCK = threading.Lock()
_SOF_MARKERS = frozenset({0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF})
# The camera exposes for the ceiling spots at night, leaving faces near black and the frame grey
# (2026-10-30: mean 65, a face at 32; no face found until the contrast was stretched). Frames darker
# on average than this get their contrast stretched and their shadows lifted before Reachy looks at them.
DIM_FRAME_MEAN = 90.0
# Below this spread between the dark and bright ends the room is simply dark (lights off): stretching
# it would only blow up sensor noise.
_MIN_BOOST_SPREAD = 32.0
_BOOST_GAMMA = 0.7


def capture_jpeg(media: Any) -> bytes | None:
    """Return the current camera frame as JPEG bytes (blocking), serialized across callers; None while private."""
    if PRIVACY.on:
        return None
    with _CAPTURE_LOCK:
        jpeg: bytes | None = media.get_frame_jpeg()
    return jpeg


def capture_frame(media: Any) -> Any:
    """Return the current camera frame as a BGR array (blocking), serialized with ``capture_jpeg``; None while private."""
    if PRIVACY.on:
        return None
    with _CAPTURE_LOCK:
        return media.get_frame()


def boost_dim_frame(frame_bgr: Any) -> Any:
    """Return a dim BGR frame with its contrast stretched and shadows lifted; any other frame unchanged.

    For what Reachy looks at himself (faces, the model's view), never for photos people see: it
    makes a dim picture grainy.
    """
    import numpy as np

    if frame_bgr is None:
        return None
    sample = frame_bgr[::4, ::4]
    if float(sample.mean()) >= DIM_FRAME_MEAN:
        return frame_bgr
    low, high = (float(v) for v in np.percentile(sample, (1, 99)))
    if high - low < _MIN_BOOST_SPREAD:
        return frame_bgr
    levels = np.clip((np.arange(256, dtype=np.float32) - low) / (high - low), 0.0, 1.0)
    lut = np.round(levels**_BOOST_GAMMA * 255.0).astype(np.uint8)
    return lut[frame_bgr]


def boost_dim_jpeg(jpeg: bytes) -> bytes:
    """Return a dim JPEG re-encoded with :func:`boost_dim_frame`; any other (or unreadable) JPEG unchanged."""
    if not jpeg.startswith(b"\xff\xd8"):
        return jpeg
    frame = decode_image(jpeg)
    if frame is None:
        return jpeg
    boosted = boost_dim_frame(frame)
    if boosted is frame:
        return jpeg
    return encode_jpeg(boosted) or jpeg


def decode_image(data: bytes) -> Any:
    """Decode JPEG or PNG bytes into a BGR ``uint8`` array (EXIF orientation applied), or None on failure."""
    import numpy as np

    # A fixed decoder chosen from the file's magic bytes: decodebin plugs its decoder after data flows,
    # and under load end-of-stream sometimes won that race (about 1 decode in 10 returned nothing).
    if data.startswith(b"\xff\xd8"):
        decoder = "jpegparse ! jpegdec"
    elif data.startswith(b"\x89PNG\r\n\x1a\n"):
        decoder = "pngdec"
    else:
        logger.warning("Image decode failed: not a JPEG or PNG")
        return None
    try:
        import gi

        gi.require_version("Gst", "1.0")
        from gi.repository import Gst

        Gst.init(None)
        pipeline = Gst.parse_launch(
            f"appsrc name=src ! {decoder} ! videoflip video-direction=auto ! videoconvert ! "
            "video/x-raw,format=BGR ! appsink name=sink sync=false"
        )
        try:
            pipeline.set_state(Gst.State.PLAYING)
            source = pipeline.get_by_name("src")
            source.emit("push-buffer", Gst.Buffer.new_wrapped(bytes(data)))
            source.emit("end-of-stream")
            sample = pipeline.get_by_name("sink").emit("try-pull-sample", 5 * _PULL_TIMEOUT_NS)
            if sample is None:
                raise RuntimeError("no decoded frame")
            structure = sample.get_caps().get_structure(0)
            width, height = structure.get_value("width"), structure.get_value("height")
            buffer = sample.get_buffer()
            raw = buffer.extract_dup(0, buffer.get_size())
        finally:
            pipeline.set_state(Gst.State.NULL)
        stride = (width * 3 + 3) // 4 * 4
        rows = np.frombuffer(raw, dtype=np.uint8)[: stride * height].reshape(height, stride)
        return np.ascontiguousarray(rows[:, : width * 3].reshape(height, width, 3))
    except Exception as error:
        logger.warning("Image decode failed: %s", type(error).__name__)
        return None


def _padded_rows(frame_bgr: Any) -> bytes:
    """Return BGR pixel bytes with each row padded to 4 bytes, the stride GStreamer expects."""
    import numpy as np

    height, width = frame_bgr.shape[:2]
    stride = (width * 3 + 3) // 4 * 4
    if stride == width * 3:
        return bytes(np.ascontiguousarray(frame_bgr, dtype=np.uint8).tobytes())
    rows = np.zeros((height, stride), dtype=np.uint8)
    rows[:, : width * 3] = np.asarray(frame_bgr, dtype=np.uint8).reshape(height, width * 3)
    return rows.tobytes()


def encode_jpeg(frame_bgr: Any, quality: int = MODEL_FRAME_JPEG_QUALITY) -> bytes | None:
    """Encode a BGR ``uint8`` array as JPEG bytes, or return None on failure."""
    height, width = frame_bgr.shape[:2]
    try:
        import gi

        gi.require_version("Gst", "1.0")
        from gi.repository import Gst

        Gst.init(None)
        pipeline = Gst.parse_launch(
            f'appsrc name=src caps="video/x-raw,format=BGR,width={width},height={height},framerate=0/1" ! '
            # jpegenc also takes BGR, but then writes an RGB-coded JPEG, which GStreamer's own decoder
            # (decode_image) misreads as green; standard YCbCr reads back correctly everywhere.
            f"videoconvert ! video/x-raw,format=I420 ! jpegenc quality={quality} ! appsink name=sink sync=false"
        )
        try:
            pipeline.set_state(Gst.State.PLAYING)
            source = pipeline.get_by_name("src")
            source.emit("push-buffer", Gst.Buffer.new_wrapped(_padded_rows(frame_bgr)))
            source.emit("end-of-stream")
            sample = pipeline.get_by_name("sink").emit("try-pull-sample", _PULL_TIMEOUT_NS)
            if sample is None:
                raise RuntimeError("no encoded frame")
            buffer = sample.get_buffer()
            jpeg: bytes = buffer.extract_dup(0, buffer.get_size())
        finally:
            pipeline.set_state(Gst.State.NULL)
    except Exception as error:
        logger.warning("JPEG encode failed: %s", type(error).__name__)
        return None
    return jpeg or None


def jpeg_size(jpeg: bytes) -> tuple[int, int] | None:
    """Return ``(width, height)`` from a JPEG's start-of-frame header, or None."""
    if not jpeg.startswith(b"\xff\xd8"):
        return None
    index = 2
    while index + 4 <= len(jpeg):
        if jpeg[index] != 0xFF:
            return None
        marker = jpeg[index + 1]
        if marker == 0xFF:
            index += 1
            continue
        if marker in (0xD8, 0x01) or 0xD0 <= marker <= 0xD7:
            index += 2
            continue
        (length,) = struct.unpack(">H", jpeg[index + 2 : index + 4])
        if marker in _SOF_MARKERS:
            if index + 9 > len(jpeg):
                return None
            height, width = struct.unpack(">HH", jpeg[index + 5 : index + 9])
            return (width, height) if width and height else None
        index += 2 + length
    return None


def fit_size(width: int, height: int, max_side: int) -> tuple[int, int]:
    """Scale ``(width, height)`` to fit within ``max_side``, keeping the aspect ratio and even sizes."""
    scale = min(1.0, max_side / max(width, height))
    return max(2, int(width * scale) // 2 * 2), max(2, int(height * scale) // 2 * 2)


def fit_jpeg_for_model(jpeg: bytes, max_side: int = MODEL_FRAME_MAX_SIDE) -> bytes:
    """Return the JPEG downscaled to fit ``max_side``, or unchanged if small, unreadable, or on failure."""
    size = jpeg_size(jpeg)
    if size is None or max(size) <= max_side:
        return jpeg
    target_width, target_height = fit_size(*size, max_side)
    try:
        import gi

        gi.require_version("Gst", "1.0")
        from gi.repository import Gst

        Gst.init(None)
        pipeline = Gst.parse_launch(
            "appsrc name=src caps=image/jpeg ! jpegdec ! videoconvert ! videoscale ! "
            f"video/x-raw,width={target_width},height={target_height} ! "
            f"jpegenc quality={MODEL_FRAME_JPEG_QUALITY} ! appsink name=sink sync=false"
        )
        try:
            pipeline.set_state(Gst.State.PLAYING)
            source = pipeline.get_by_name("src")
            source.emit("push-buffer", Gst.Buffer.new_wrapped(jpeg))
            source.emit("end-of-stream")
            sample = pipeline.get_by_name("sink").emit("try-pull-sample", _PULL_TIMEOUT_NS)
            if sample is None:
                raise RuntimeError("no encoded frame")
            buffer = sample.get_buffer()
            scaled: bytes = buffer.extract_dup(0, buffer.get_size())
        finally:
            pipeline.set_state(Gst.State.NULL)
    except Exception as error:
        logger.warning("Camera frame downscale failed; sending the original: %s", type(error).__name__)
        return jpeg
    return scaled if scaled else jpeg
