"""The privacy switch (saved across restarts, listeners) and quiet hours."""

import os
import sys
import json
from pathlib import Path
from datetime import time, datetime

import pytest

from reachy_mini_conversation_app import privacy
from reachy_mini_conversation_app.privacy import PrivacySwitch, quiet_now, listening_allowed


def test_privacy_starts_off_and_is_saved_owner_only(tmp_path: Path) -> None:
    """Privacy starts off and is saved owner only."""
    path = tmp_path / "state" / "privacy.json"
    switch = PrivacySwitch(path)
    assert switch.on is False and switch.since is None

    assert switch.set(True) is True
    assert switch.on is True and switch.since is not None
    assert json.loads(path.read_text())["on"] is True
    if sys.platform != "win32":
        assert path.stat().st_mode & 0o777 == 0o600

    restarted = PrivacySwitch(path)
    assert restarted.on is True and restarted.since == switch.since


def test_turning_privacy_off_is_saved_and_clears_since(tmp_path: Path) -> None:
    """Turning privacy off is saved and clears since."""
    path = tmp_path / "privacy.json"
    switch = PrivacySwitch(path)
    switch.set(True)
    assert switch.set(True) is False
    assert switch.set(False) is True
    assert switch.since is None
    assert PrivacySwitch(path).on is False


def test_an_unreadable_state_file_starts_with_privacy_off(tmp_path: Path) -> None:
    """An unreadable state file starts with privacy off."""
    path = tmp_path / "privacy.json"
    path.write_text("not json")
    assert PrivacySwitch(path).on is False


def test_listeners_hear_each_change_and_a_failing_one_does_not_undo_it(tmp_path: Path) -> None:
    """Listeners hear each change and a failing one does not undo it."""
    switch = PrivacySwitch(tmp_path / "privacy.json")
    heard: list[bool] = []

    def broken(on: bool) -> None:
        raise RuntimeError("display gone")

    switch.add_listener(broken)
    switch.add_listener(heard.append)
    switch.set(True)
    switch.set(True)
    switch.set(False)
    assert heard == [True, False]


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX permissions")
def test_a_state_that_cannot_be_saved_still_switches(tmp_path: Path) -> None:
    """A state that cannot be saved still switches."""
    folder = tmp_path / "locked"
    folder.mkdir()
    os.chmod(folder, 0o500)
    try:
        switch = PrivacySwitch(folder / "privacy.json")
        assert switch.set(True) is True and switch.on is True
    finally:
        os.chmod(folder, 0o700)


@pytest.mark.parametrize(
    ("clock", "quiet"),
    [(time(21, 29), False), (time(21, 30), True), (time(23, 59), True), (time(0, 0), True), (time(6, 59), True),
     (time(7, 0), False), (time(12, 0), False)],
)  # fmt: skip
def test_quiet_hours_across_midnight(clock: time, quiet: bool) -> None:
    """Quiet hours across midnight."""
    now = datetime.combine(datetime(2026, 10, 30), clock)
    assert quiet_now(now, (time(21, 30), time(7, 0))) is quiet


def test_quiet_hours_within_a_day() -> None:
    """Quiet hours within a day."""
    hours = (time(13, 0), time(15, 0))
    assert quiet_now(datetime(2026, 10, 30, 14, 0), hours) is True
    assert quiet_now(datetime(2026, 10, 30, 15, 0), hours) is False
    assert quiet_now(datetime(2026, 10, 30, 12, 59), hours) is False


def test_no_quiet_hours_configured_is_never_quiet(monkeypatch: pytest.MonkeyPatch) -> None:
    """No quiet hours configured is never quiet."""
    monkeypatch.setattr(privacy.config, "QUIET_HOURS", None)
    assert quiet_now(datetime(2026, 10, 30, 3, 0)) is False


def test_listening_needs_privacy_off_and_no_quiet_hours(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Listening needs privacy off and no quiet hours."""
    switch = PrivacySwitch(tmp_path / "privacy.json")
    monkeypatch.setattr(privacy, "PRIVACY", switch)
    monkeypatch.setattr(privacy.config, "QUIET_HOURS", None)
    assert listening_allowed() is True
    switch.set(True)
    assert listening_allowed() is False
    switch.set(False)
    monkeypatch.setattr(privacy.config, "QUIET_HOURS", (time(0, 0), time(23, 59)))
    monkeypatch.setattr(privacy, "quiet_now", lambda: True)
    assert listening_allowed() is False
