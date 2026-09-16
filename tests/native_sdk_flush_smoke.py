"""Manually audible SDK flush probe; run through the opt-in subprocess test."""

import time
import logging
import argparse
from threading import Lock

import numpy as np

from reachy_mini.media.audio_gstreamer import GStreamerAudio


logger = logging.getLogger(__name__)


def main(*, wobble: bool = False) -> None:
    """Queue a known tone, flush it, and check capture resumes without saving audio."""
    logging.basicConfig(level=logging.INFO)
    callback_count = 0
    callback_lock = Lock()

    def observe_wobble(_offsets: object) -> None:
        nonlocal callback_count
        with callback_lock:
            callback_count += 1

    audio = GStreamerAudio()
    try:
        if wobble:
            audio.enable_wobbling(observe_wobble)
        audio.start_recording()
        audio.start_playing()
        sample_rate = audio.get_output_audio_samplerate()
        tone = (0.025 * np.sin(2 * np.pi * 440 * np.arange(sample_rate // 10) / sample_rate)).astype(np.float32)
        tone = np.column_stack((tone,) * audio.get_output_channels())
        assert tone.nbytes == sample_rate // 10 * audio.get_output_channels() * 4
        for cycle in range(3):
            before = 0
            deadline = time.monotonic() + 1.0
            while time.monotonic() < deadline:
                if audio.get_audio_sample() is not None:
                    before += 1
            assert before > 0, "No microphone frames before flush"
            with callback_lock:
                previous_callbacks = callback_count
            started = time.monotonic()
            for _ in range(20):
                audio.push_audio_sample(tone)
            time.sleep(0.4)
            logger.info(
                "FLUSH NOW cycle=%d submitted_ms=2000 elapsed_ms=%.1f", cycle + 1, (time.monotonic() - started) * 1000
            )
            if wobble:
                with callback_lock:
                    delivered = callback_count - previous_callbacks
                logger.info("Wobble callbacks before flush: %d", delivered)
                assert delivered > 0, "Wobble callback did not run during playback"
            flush_started = time.monotonic()
            audio.clear_player()
            logger.info("Flush returned in %.1f ms", (time.monotonic() - flush_started) * 1000)
            counts = []
            for _ in range(3):
                count = 0
                deadline = time.monotonic() + 0.5
                while time.monotonic() < deadline:
                    if audio.get_audio_sample() is not None:
                        count += 1
                counts.append(count)
            logger.info("Post-flush capture frames per half-second: %s", counts)
            assert all(count > 0 for count in counts), "Microphone delivery stalled after flush"
    finally:
        try:
            audio.stop_playing()
            audio.stop_recording()
        finally:
            audio.cleanup()
    logger.info("SDK flush probe completed; audible stopping requires human confirmation")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--wobble", action="store_true", help="Exercise SDK wobble callbacks without moving a robot")
    main(wobble=parser.parse_args().wobble)
