"""Replay synthetic Gemini-sized PCM through the SDK in an isolated process."""

import time
import logging
import argparse

import numpy as np

from reachy_mini.media.audio_gstreamer import GStreamerAudio
from reachy_mini_conversation_app.streaming import Pcm16ChunkBuffer, resample_audio, audio_to_float32


logger = logging.getLogger(__name__)


def main(*, raw: bool = False) -> None:
    """Exercise tiny leading fragments, turn tails and interruption cleanup without cloud audio."""
    logging.basicConfig(level=logging.INFO)
    audio = GStreamerAudio()
    chunks = Pcm16ChunkBuffer(240)
    wobble_callbacks = 0

    def observe_wobble(_offsets: object) -> None:
        nonlocal wobble_callbacks
        wobble_callbacks += 1

    try:
        audio.enable_wobbling(observe_wobble)
        audio.start_recording()
        audio.start_playing()
        output_rate = audio.get_output_audio_samplerate()
        channels = audio.get_output_channels()
        for turn in range(3):
            emitted_samples = 0
            for size in (1, 4560, 3840, 3840, 18240, 6720, 1):
                frame = np.zeros(size, dtype=np.int16)
                buffered = frame if raw else chunks.push(frame)
                if buffered is None:
                    continue
                converted = resample_audio(audio_to_float32(buffered), 24000, output_rate)
                audio.push_audio_sample(np.column_stack((converted,) * channels))
                emitted_samples += buffered.size
                time.sleep(0.01)
            tail = None if raw else chunks.finish()
            if tail is not None:
                converted = resample_audio(audio_to_float32(tail), 24000, output_rate)
                audio.push_audio_sample(np.column_stack((converted,) * channels))
                emitted_samples += tail.size
            time.sleep(emitted_samples / 24000 + 0.1)
            audio.clear_player()
            chunks.clear()
            logger.info("SDK synthetic chunk turn=%d completed", turn + 1)
        logger.info("SDK wobble callbacks=%d", wobble_callbacks)
        assert wobble_callbacks > 0, "SDK wobble route did not deliver callbacks"
    finally:
        audio.stop_playing()
        audio.stop_recording()
        audio.cleanup()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--raw", action="store_true", help="Reproduce the unsafe pre-fix SDK input")
    main(raw=parser.parse_args().raw)
