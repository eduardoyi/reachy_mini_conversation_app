"""Isolate experimental native SDK flushing from the conversation process."""

import os
import sys
import runpy
import subprocess
from types import SimpleNamespace
from pathlib import Path
from unittest.mock import Mock

import pytest


def test_optional_sdk_flush() -> None:
    """Require explicit audio-device opt-in and bound native hangs or crashes."""
    if os.environ.get("REACHY_RUN_SDK_FLUSH_SMOKE") != "1":
        pytest.skip("set REACHY_RUN_SDK_FLUSH_SMOKE=1 with the conversation app stopped")
    child = Path(__file__).with_name("native_sdk_flush_smoke.py")
    environment = os.environ.copy()
    for variable in (
        "PYTHONPATH",
        "GI_TYPELIB_PATH",
        "GST_PYTHONPATH_1_0",
        "GST_PLUGIN_PATH_1_0",
        "GST_PLUGIN_SYSTEM_PATH_1_0",
        "GST_PLUGIN_SCANNER_1_0",
        "GST_REGISTRY_1_0",
    ):
        environment.pop(variable, None)
    try:
        result = subprocess.run([sys.executable, str(child)], env=environment, timeout=30, check=False)
    except subprocess.TimeoutExpired:
        pytest.fail("SDK flush probe timed out after 30 seconds; child terminated")
    assert result.returncode == 0, f"SDK flush probe failed: exit/signal={result.returncode}"


@pytest.mark.parametrize("wobble", [False, True])
def test_probe_matches_sdk_channel_count(monkeypatch: pytest.MonkeyPatch, wobble: bool) -> None:
    """Every probe buffer represents 100 ms at the SDK's advertised channel count."""
    audio = Mock()
    audio.get_output_audio_samplerate.return_value = 16000
    audio.get_output_channels.return_value = 2
    audio.get_audio_sample.return_value = object()
    module = SimpleNamespace(GStreamerAudio=lambda: audio)
    monkeypatch.setitem(sys.modules, "reachy_mini.media.audio_gstreamer", module)
    probe = runpy.run_path(str(Path(__file__).with_name("native_sdk_flush_smoke.py")))
    clock = iter(index * 0.05 for index in range(10000))
    probe["main"].__globals__["time"] = SimpleNamespace(monotonic=lambda: next(clock), sleep=lambda _: None)

    def push(_frame: object) -> None:
        if wobble:
            audio.enable_wobbling.call_args.args[0](object())

    audio.push_audio_sample.side_effect = push
    probe["main"](wobble=wobble)
    assert audio.enable_wobbling.call_count == int(wobble)
    assert audio.push_audio_sample.call_count == 60
    for call in audio.push_audio_sample.call_args_list:
        frame = call.args[0]
        assert frame.shape == (1600, 2)
        assert frame.nbytes == 1600 * 2 * 4
