"""Turn a recorded message into a WhatsApp voice note: OGG Opus, mono, encoded with GStreamer.

WhatsApp plays voice notes only as OGG Opus; GOWA sends such a file as is, so no ``ffmpeg`` is needed.
"""

import logging
from typing import Final

import numpy as np
from numpy.typing import NDArray


logger = logging.getLogger(__name__)

OPUS_BITRATE: Final[int] = 32_000
_PULL_TIMEOUT_NS: Final[int] = 2_000_000_000


def encode_voice_note(sample_rate: int, pcm: NDArray[np.int16]) -> bytes | None:
    """Return mono 16-bit ``pcm`` at ``sample_rate`` as OGG Opus bytes, or None on failure."""
    if pcm.size == 0:
        return None
    try:
        import gi

        gi.require_version("Gst", "1.0")
        from gi.repository import Gst

        Gst.init(None)
        pipeline = Gst.parse_launch(
            f'appsrc name=src format=time caps="audio/x-raw,format=S16LE,channels=1,rate={sample_rate},'
            'layout=interleaved" ! audioconvert ! audioresample ! audio/x-raw,rate=48000 ! '
            f"opusenc bitrate={OPUS_BITRATE} ! oggmux ! appsink name=sink sync=false"
        )
        try:
            pipeline.set_state(Gst.State.PLAYING)
            source = pipeline.get_by_name("src")
            buffer = Gst.Buffer.new_wrapped(np.ascontiguousarray(pcm, dtype="<i2").tobytes())
            buffer.pts = 0
            buffer.duration = Gst.util_uint64_scale(pcm.size, Gst.SECOND, sample_rate)
            source.emit("push-buffer", buffer)
            source.emit("end-of-stream")
            sink = pipeline.get_by_name("sink")
            pages = bytearray()
            while True:
                sample = sink.emit("try-pull-sample", _PULL_TIMEOUT_NS)
                if sample is None:
                    break
                page = sample.get_buffer()
                pages += page.extract_dup(0, page.get_size())
        finally:
            pipeline.set_state(Gst.State.NULL)
    except Exception as error:
        logger.warning("Voice note encode failed: %s", type(error).__name__)
        return None
    if not pages.startswith(b"OggS"):
        logger.warning("Voice note encode gave no OGG data")
        return None
    return bytes(pages)
