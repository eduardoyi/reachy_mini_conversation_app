"""Opt-in official33 integration with a running headless MuJoCo daemon."""

import os
import wave
import asyncio
from pathlib import Path

import numpy as np
import pytest
from test_hybrid_runtime import FakeHandler

from reachy_mini import ReachyMini
from reachy_mini_conversation_app.moves import MovementManager
from reachy_mini_conversation_app.hybrid_runtime import RuntimeMode, HybridRuntime
from reachy_mini_conversation_app.acoustic_wake_word import OpenWakeWordDetector


@pytest.mark.asyncio
@pytest.mark.skipif(
    os.getenv("REACHY_WAKE_SIMULATOR_TEST") != "1", reason="Opt-in native simulator/model assets required"
)
async def test_official33_activates_simulator_without_ambient_cloud_audio() -> None:
    """Real inference queues the simulator acknowledgment through the existing runtime."""
    directory = Path(os.environ["LOCAL_ACOUSTIC_WAKE_MODEL_DIR"])
    fixture = Path(os.environ["REACHY_WAKE_TEST_POSITIVE"])
    detector = OpenWakeWordDetector.from_directory(directory)
    with wave.open(str(fixture)) as stream:
        assert (stream.getframerate(), stream.getnchannels(), stream.getsampwidth()) == (16000, 1, 2)
        pcm = np.frombuffer(stream.readframes(stream.getnframes()), dtype="<i2")
    assert pcm.size > 0
    pcm = np.pad(pcm, (16000, 32000))
    pcm = np.pad(pcm, (0, (-len(pcm)) % 1280))
    acknowledgements = []
    handler = FakeHandler()
    with ReachyMini(connection_mode="localhost_only", media_backend="no_media") as robot:
        movement = MovementManager(robot)

        def acknowledge(reason: str) -> None:
            acknowledgements.append(reason)
            movement.acknowledge_activation()

        runtime = HybridRuntime(lambda: handler, acoustic_wake_detector=detector, acknowledgement_callback=acknowledge)
        movement.start()
        try:
            for start in range(0, len(pcm), 1280):
                await runtime.handle_frame((16000, pcm[start : start + 1280]))
                if runtime.mode is RuntimeMode.CLOUD_CONVERSATION:
                    break
            assert runtime.mode is RuntimeMode.CLOUD_CONVERSATION
            assert acknowledgements == ["acoustic_wake"]
            assert not handler.receive_frames
            assert runtime.metrics.ambient_audio_bytes_sent_to_cloud == 0
            await asyncio.sleep(0.25)
            assert np.isfinite(robot.get_current_head_pose()).all()
            await runtime.deactivate_cloud()
            assert runtime.mode is RuntimeMode.LOCAL_AMBIENT
        finally:
            await runtime.close()
            movement.stop(reset_to_neutral=False)
    assert handler.shutdown_calls == 1
