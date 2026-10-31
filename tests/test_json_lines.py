"""Owner-only JSON-lines logs: the activity log and the admin app's audit log."""

import sys
from pathlib import Path

import pytest

from reachy_mini_conversation_app import json_lines, activity_log
from reachy_mini_conversation_app.json_lines import JsonLines


def test_entries_come_back_newest_first_with_a_time(tmp_path: Path) -> None:
    """Entries come back newest first with a time."""
    log = JsonLines(tmp_path / "folder" / "log.jsonl")
    assert log.recent() == []
    log.append({"n": 1})
    log.append({"n": 2})
    entries = log.recent()
    assert [e["n"] for e in entries] == [2, 1]
    assert all("at" in e for e in entries)
    assert [e["n"] for e in log.recent(1)] == [2]
    if sys.platform != "win32":
        assert log.path.stat().st_mode & 0o777 == 0o600
        assert log.path.parent.stat().st_mode & 0o777 == 0o700


def test_unreadable_lines_are_skipped(tmp_path: Path) -> None:
    """Unreadable lines are skipped."""
    path = tmp_path / "log.jsonl"
    path.write_text('{"n": 1}\nnot json\n[1, 2]\n{"n": 2}\n')
    assert [e["n"] for e in JsonLines(path).recent()] == [2, 1]


def test_a_large_log_keeps_its_newest_lines(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A large log keeps its newest lines."""
    monkeypatch.setattr(json_lines, "MAX_BYTES", 400)
    monkeypatch.setattr(json_lines, "KEPT_LINES", 3)
    log = JsonLines(tmp_path / "log.jsonl")
    for n in range(20):
        log.append({"n": n})
    entries = log.recent(100)
    assert len(entries) <= 10
    assert entries[0]["n"] == 19


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX permissions")
def test_a_failed_write_is_logged_not_raised(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    """A failed write is logged not raised."""
    folder = tmp_path / "locked"
    folder.mkdir(mode=0o500)
    try:
        JsonLines(folder / "log.jsonl").append({"n": 1})
    finally:
        folder.chmod(0o700)
    assert "not written" in caplog.text


def test_activity_entries_hold_names_outcomes_and_whole_seconds(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Activity entries hold names outcomes and whole seconds."""
    log = JsonLines(tmp_path / "activity.jsonl")
    monkeypatch.setattr(activity_log, "ACTIVITY", log)
    activity_log.record("call_out", "Abuela", "hung_up", 42.4)
    activity_log.record("photo", "Reachy photos", "sent")
    photo, call = log.recent()
    assert {k: call[k] for k in ("kind", "who", "outcome", "seconds")} == {
        "kind": "call_out", "who": "Abuela", "outcome": "hung_up", "seconds": 42
    }  # fmt: skip
    assert "seconds" not in photo and photo["who"] == "Reachy photos"
