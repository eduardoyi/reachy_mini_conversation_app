"""Tests for bounded, metadata-only audio timing reports."""

import json
import logging
from concurrent.futures import ThreadPoolExecutor

from reachy_mini_conversation_app.audio.diagnostics import AudioDiagnostics


def test_reports_aggregate_durations_and_reset_window(caplog) -> None:
    """Report interval totals and start a fresh window."""
    diagnostics = AudioDiagnostics()
    diagnostics.observe("input_forward", 0.01)
    diagnostics.observe("input_forward", 0.03)
    with caplog.at_level(logging.INFO):
        diagnostics.report(captured=10, dropped=2)
        diagnostics.report(captured=12, dropped=2)
    first, second = caplog.records
    assert first.args[:2] == (10, 2)
    assert json.loads(first.args[2]) == {"input_forward": {"count": 2, "mean_ms": 20.0, "max_ms": 30.0}}
    assert json.loads(second.args[2]) == {}


def test_capture_and_event_loop_can_update_same_summary(caplog) -> None:
    """Concurrent observations preserve every count."""
    diagnostics = AudioDiagnostics()
    with ThreadPoolExecutor(max_workers=2) as workers:
        list(workers.map(lambda _: diagnostics.observe("capture_pull", 0.001), range(100)))
    with caplog.at_level(logging.INFO):
        diagnostics.report(captured=100, dropped=0)
    assert json.loads(caplog.records[-1].args[2])["capture_pull"]["count"] == 100


def test_reports_forwarded_input_levels_and_server_idle_time(caplog) -> None:
    """Report mic level counts and backend silence so a mute mic and a silent server can be told apart."""
    diagnostics = AudioDiagnostics(loud_rms_threshold=400.0)
    for rms in (50.0, 399.0, 400.0, 1200.4):
        diagnostics.observe_input_level(rms)
    with caplog.at_level(logging.INFO):
        diagnostics.report(captured=4, dropped=0, server_idle_s=12.34)
        diagnostics.report(captured=4, dropped=0)
    first, second = caplog.records
    assert json.loads(first.args[3]) == {"frames": 4, "loud": 2, "max_rms": 1200}
    assert first.args[4] == "12.3"
    assert json.loads(second.args[3]) == {"frames": 0, "loud": 0, "max_rms": 0}
    assert second.args[4] == "n/a"
