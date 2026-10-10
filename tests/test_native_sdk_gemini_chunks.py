"""Keep native SDK crash checks isolated from the regular test process."""

import os
import sys
import subprocess
from pathlib import Path

import pytest


def test_optional_sdk_gemini_chunks() -> None:
    """Bound the synthetic SDK playback regression behind an explicit device opt-in."""
    if os.environ.get("REACHY_RUN_SDK_CHUNK_SMOKE") != "1":
        pytest.skip("set REACHY_RUN_SDK_CHUNK_SMOKE=1 with the conversation app stopped")
    environment = os.environ.copy()
    for name in (
        "PYTHONPATH",
        "GI_TYPELIB_PATH",
        "GST_PYTHONPATH_1_0",
        "GST_PLUGIN_PATH_1_0",
        "GST_PLUGIN_SYSTEM_PATH_1_0",
        "GST_PLUGIN_SCANNER_1_0",
        "GST_REGISTRY_1_0",
    ):
        environment.pop(name, None)
    child = Path(__file__).with_name("native_sdk_gemini_chunks.py")
    try:
        result = subprocess.run([sys.executable, str(child)], env=environment, timeout=30, check=False)
    except subprocess.TimeoutExpired:
        pytest.fail("SDK chunk probe timed out after 30 seconds; child terminated")
    assert result.returncode == 0, f"SDK chunk probe failed: exit/signal={result.returncode}"
