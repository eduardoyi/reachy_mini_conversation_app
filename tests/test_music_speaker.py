"""Tests for playing Spotify through a Bluetooth speaker paired with the Mac (blueutil faked)."""

import subprocess
from typing import Any

from reachy_mini_conversation_app.music_speaker import MusicSpeaker, ensure_spotify_app_running


class _FakeMac:
    def __init__(self, *, connected: bool, output: str = "MacBook Pro Speakers", connects: bool = True) -> None:
        self.connected = connected
        self.output = output
        self.connects = connects
        self.commands: list[tuple[str, ...]] = []
        self.spotify_running = False

    def __call__(self, *command: str, timeout: float = 10.0) -> "subprocess.CompletedProcess[str]":
        self.commands.append(command)
        out, code = "", 0
        if command[:2] == ("blueutil", "--is-connected"):
            out = "1" if self.connected else "0"
        elif command[:2] == ("blueutil", "--connect"):
            self.connected = self.connects
        elif command[:3] == ("SwitchAudioSource", "-c", "-t"):
            out = self.output
        elif command[:2] == ("SwitchAudioSource", "-s"):
            self.output = command[2]
        elif command[:2] == ("pgrep", "-x"):
            code = 0 if self.spotify_running else 1
        elif command[:1] == ("open",):
            self.spotify_running = True
        return subprocess.CompletedProcess(list(command), code, out, "")


def _speaker(mac: _FakeMac) -> MusicSpeaker:
    ticks = iter(range(1000))
    return MusicSpeaker("aa-bb", "Living Room Speaker", runner=mac, sleep=lambda _s: None, clock=lambda: next(ticks))


def test_a_disconnected_speaker_is_connected_and_becomes_the_output() -> None:
    """The speaker is reconnected and the Mac's sound sent to it."""
    mac = _FakeMac(connected=False)
    assert _speaker(mac).ensure_ready() is True
    assert ("blueutil", "--connect", "aa-bb") in mac.commands
    assert mac.output == "Living Room Speaker"


def test_a_ready_speaker_needs_no_changes() -> None:
    """Already connected and selected: nothing is switched."""
    mac = _FakeMac(connected=True, output="Living Room Speaker")
    assert _speaker(mac).ensure_ready() is True
    assert not any(command[:2] in {("blueutil", "--connect"), ("SwitchAudioSource", "-s")} for command in mac.commands)


def test_a_speaker_that_never_connects_is_reported() -> None:
    """Out of range or off: not ready, and the output is left alone."""
    mac = _FakeMac(connected=False, connects=False)
    assert _speaker(mac).ensure_ready() is False
    assert mac.output == "MacBook Pro Speakers"


def test_spotify_is_opened_when_it_is_not_running(monkeypatch: Any) -> None:
    """The Mac's Spotify app is the playback device, so it is opened in the background."""
    monkeypatch.setattr("sys.platform", "darwin")
    mac = _FakeMac(connected=True)
    assert ensure_spotify_app_running(runner=mac, sleep=lambda _s: None) is True
    assert ("open", "-g", "-a", "Spotify") in mac.commands
