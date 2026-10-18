"""Tests for the Android TV client (androidtvremote2 replaced by a fake remote)."""

from typing import Any
from pathlib import Path

import pytest
from androidtvremote2 import InvalidAuth, CannotConnect, ConnectionClosed

from reachy_mini_conversation_app.android_tv import (
    TVError,
    AndroidTV,
    is_paired,
    cert_paths,
    resolve_app,
    volume_steps,
)


class _FakeRemote:
    def __init__(self, connect_error: Exception | None = None, **_: Any) -> None:
        self.connect_error = connect_error
        self.keys: list[str] = []
        self.launched: list[str] = []
        self.is_on: bool | None = True
        self.current_app: str | None = "com.netflix.ninja"
        self.volume_info: dict[str, Any] | None = {"level": 20, "max": 100, "muted": False}
        self.connects = 0
        self.disconnected = False
        self.fail_next_send = False

    async def async_connect(self) -> None:
        self.connects += 1
        if self.connect_error is not None:
            raise self.connect_error

    def keep_reconnecting(self) -> None:
        pass

    def disconnect(self) -> None:
        self.disconnected = True

    def send_key_command(self, key: str) -> None:
        if self.fail_next_send:
            self.fail_next_send = False
            raise ConnectionClosed("gone")
        self.keys.append(key)

    def send_launch_app_command(self, link: str) -> None:
        self.launched.append(link)


@pytest.fixture(autouse=True)
def _no_first_state_wait(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("reachy_mini_conversation_app.android_tv.FIRST_STATE_WAIT_S", 0)


def _tv(remote: _FakeRemote) -> AndroidTV:
    return AndroidTV("tv.local", Path("/nonexistent"), remote_factory=lambda **_: remote)


def test_spoken_app_names_resolve_to_packages() -> None:
    """Names match without case, "+" reads as "plus", and a package name passes through."""
    assert resolve_app("Netflix") == "com.netflix.ninja"
    assert resolve_app("Disney+") == "com.disney.disneyplus"
    assert resolve_app("YouTube Kids") == "com.google.android.youtube.tvkids"
    assert resolve_app("com.example.app") == "com.example.app"
    assert resolve_app("prime") == "com.amazon.amazonvideo.livingroom"
    assert resolve_app("you") is None  # YouTube or YouTube Kids: ambiguous
    assert resolve_app("solitaire") is None


def test_volume_steps_go_to_the_target_and_are_capped() -> None:
    """Steps are counted on the TV's own scale; one call moves at most 60 steps."""
    assert volume_steps(20, 100, 30) == 10
    assert volume_steps(20, 100, 5) == -15
    assert volume_steps(5, 15, 100) == 10
    assert volume_steps(0, 100, 100) == 60
    assert volume_steps(10, 0, 50) == 0


def test_paired_means_both_certificate_files_exist(tmp_path: Path) -> None:
    """Pairing leaves a certificate and a key."""
    certfile, keyfile = cert_paths(tmp_path)
    certfile.write_text("cert")
    assert not is_paired(tmp_path)
    keyfile.write_text("key")
    assert is_paired(tmp_path)


@pytest.mark.asyncio
async def test_state_reports_app_and_volume_percent() -> None:
    """The TV's volume is reported as a percentage of its maximum."""
    remote = _FakeRemote()
    remote.volume_info = {"level": 6, "max": 15, "muted": True}
    state = await _tv(remote).state()
    assert (state.is_on, state.app_name, state.volume, state.muted) == (True, "netflix", 40, True)


@pytest.mark.asyncio
async def test_power_toggles_only_when_the_tv_is_in_the_other_state(monkeypatch: pytest.MonkeyPatch) -> None:
    """An on TV asked to turn on gets no key; asked to turn off it gets POWER and confirms."""
    monkeypatch.setattr("reachy_mini_conversation_app.android_tv.POWER_POLL_S", 0)
    remote = _FakeRemote()
    tv = _tv(remote)
    assert await tv.power(True) is True
    assert remote.keys == []

    def press(key: str) -> None:
        remote.keys.append(key)
        remote.is_on = False

    remote.send_key_command = press  # type: ignore[method-assign]
    assert await tv.power(False) is True
    assert remote.keys == ["POWER"]
    assert remote.connects == 1  # the connection is kept


@pytest.mark.asyncio
async def test_power_reports_a_tv_that_never_changed(monkeypatch: pytest.MonkeyPatch) -> None:
    """When the TV does not report the new state, power returns False."""
    monkeypatch.setattr("reachy_mini_conversation_app.android_tv.POWER_POLL_S", 0)
    monkeypatch.setattr("reachy_mini_conversation_app.android_tv.POWER_CONFIRM_S", 0.01)
    remote = _FakeRemote()
    assert await _tv(remote).power(False) is False
    assert remote.keys == ["POWER"]


@pytest.mark.asyncio
async def test_power_falls_back_to_wakeup_when_the_state_is_unknown(monkeypatch: pytest.MonkeyPatch) -> None:
    """Without a reported state POWER could toggle the wrong way, so WAKEUP is sent."""
    monkeypatch.setattr("reachy_mini_conversation_app.android_tv.POWER_POLL_S", 0)
    monkeypatch.setattr("reachy_mini_conversation_app.android_tv.POWER_CONFIRM_S", 0.01)
    remote = _FakeRemote()
    remote.is_on = None
    await _tv(remote).power(True)
    assert remote.keys == ["WAKEUP"]


@pytest.mark.asyncio
async def test_set_volume_presses_volume_keys(monkeypatch: pytest.MonkeyPatch) -> None:
    """From 20 to 23 % on a 0-100 TV is three presses up."""
    monkeypatch.setattr("reachy_mini_conversation_app.android_tv.VOLUME_KEY_GAP_S", 0)
    remote = _FakeRemote()
    await _tv(remote).set_volume(23)
    assert remote.keys == ["VOLUME_UP"] * 3


@pytest.mark.asyncio
async def test_mute_only_toggles_when_needed() -> None:
    """Asking to mute an already muted TV sends nothing."""
    remote = _FakeRemote()
    tv = _tv(remote)
    await tv.set_muted(False)
    assert remote.keys == []
    await tv.set_muted(True)
    assert remote.keys == ["VOLUME_MUTE"]


@pytest.mark.asyncio
async def test_open_app_confirms_the_app_came_up(monkeypatch: pytest.MonkeyPatch) -> None:
    """An installed app becomes the current app; a missing one never does, after both links."""
    monkeypatch.setattr("reachy_mini_conversation_app.android_tv.POWER_POLL_S", 0)
    monkeypatch.setattr("reachy_mini_conversation_app.android_tv.APP_CONFIRM_S", 0.01)
    remote = _FakeRemote()

    def launch(link: str) -> None:
        remote.launched.append(link)
        if link == "https://www.netflix.com/browse":
            remote.current_app = "com.netflix.ninja"

    remote.current_app = "com.google.android.tvlauncher"
    remote.send_launch_app_command = launch  # type: ignore[method-assign]
    tv = _tv(remote)
    assert await tv.open_app("com.google.android.youtube.tvkids") is False
    # The web link first, then the package (Play Store launch link) as a fallback.
    assert remote.launched == ["https://www.youtubekids.com", "com.google.android.youtube.tvkids"]
    assert await tv.open_app("com.netflix.ninja") is True
    assert remote.launched[-1] == "https://www.netflix.com/browse"


@pytest.mark.asyncio
async def test_a_dropped_connection_is_reopened_once() -> None:
    """A command on a dead connection reconnects and is sent again."""
    remote = _FakeRemote()
    tv = _tv(remote)
    await tv.state()
    remote.fail_next_send = True
    await tv.key("HOME")
    assert remote.keys == ["HOME"] and remote.connects == 2


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("error", "message"),
    [(InvalidAuth("x"), "not paired"), (CannotConnect("x"), "not reachable"), (OSError("x"), "not reachable")],
)
async def test_connection_failures_become_tv_errors(error: Exception, message: str) -> None:
    """Pairing problems and an unreachable TV are told apart."""
    with pytest.raises(TVError, match=message):
        await _tv(_FakeRemote(connect_error=error)).state()
