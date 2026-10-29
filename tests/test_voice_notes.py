import numpy as np
import pytest

from reachy_mini_conversation_app.voice_notes import encode_voice_note


def test_a_recording_becomes_an_ogg_opus_voice_note() -> None:
    """Two seconds of 16 kHz speech-like audio encode to OGG Opus, the format WhatsApp voice notes need."""
    gi = pytest.importorskip("gi")
    gi.require_version("Gst", "1.0")
    rate = 16_000
    time_s = np.arange(rate * 2) / rate
    pcm = (np.sin(2 * np.pi * 220 * time_s) * 6_000).astype(np.int16)

    ogg = encode_voice_note(rate, pcm)

    assert ogg is not None and ogg.startswith(b"OggS")
    assert b"OpusHead" in ogg
    assert 2_000 < len(ogg) < 40_000  # about 32 kbit/s


def test_nothing_to_encode_is_no_voice_note() -> None:
    """An empty recording is never sent."""
    assert encode_voice_note(16_000, np.zeros(0, dtype=np.int16)) is None
