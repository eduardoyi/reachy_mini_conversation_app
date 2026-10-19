"""Tests for the tv tool."""

from typing import Any
from unittest.mock import MagicMock

import pytest

from reachy_mini_conversation_app.tools import tv as tv_module
from reachy_mini_conversation_app.tools.tv import TV
from reachy_mini_conversation_app.android_tv import TVError, TVState
from reachy_mini_conversation_app.tools.core_tools import ToolDependencies


class _FakeTV:
    def __init__(self, is_on: bool | None = True, error: TVError | None = None) -> None:
        self.calls: list[tuple[Any, ...]] = []
        self.is_on = is_on
        self.error = error

    async def state(self) -> TVState:
        if self.error:
            raise self.error
        return TVState(is_on=self.is_on, current_app="com.google.android.youtube.tv", volume=25, muted=False)

    async def power(self, on: bool) -> bool:
        self.calls.append(("power", on))
        return True

    async def key(self, key: str, repeat: int = 1) -> None:
        self.calls.append(("key", key, repeat))

    async def set_volume(self, percent: int) -> None:
        self.calls.append(("volume", percent))

    async def set_muted(self, muted: bool) -> None:
        self.calls.append(("mute", muted))

    async def open(self, link: str) -> None:
        self.calls.append(("open", link))

    async def open_app(self, package: str) -> bool:
        self.calls.append(("open", package))
        return package != "com.google.android.youtube.tvkids"


def _deps(tv: Any) -> ToolDependencies:
    return ToolDependencies(reachy_mini=MagicMock(), movement_manager=MagicMock(), tv=tv)


@pytest.mark.asyncio
async def test_status_names_the_open_app() -> None:
    """The package of the open app is reported by its spoken name."""
    result = await TV()(_deps(_FakeTV()), action="status")
    assert result == {"on": True, "app": "youtube", "volume": 25, "muted": False}


@pytest.mark.asyncio
async def test_volume_by_level_and_by_steps() -> None:
    """A level sets the volume; "down" without steps lowers it three presses."""
    tv = _FakeTV()
    await TV()(_deps(tv), action="volume", level=15)
    await TV()(_deps(tv), action="volume", change="down")
    assert tv.calls == [("volume", 15), ("key", "VOLUME_DOWN", 3)]


@pytest.mark.asyncio
async def test_open_wakes_a_sleeping_tv_first(monkeypatch: pytest.MonkeyPatch) -> None:
    """Opening an app on a TV in standby wakes it, waits, then opens the app."""
    monkeypatch.setattr(tv_module, "WAKE_SETTLE_S", 0)
    tv = _FakeTV(is_on=False)
    result = await TV()(_deps(tv), action="open", app="Disney+")
    assert result == {"ok": True, "app": "Disney+"}
    assert tv.calls == [("power", True), ("open", "com.disney.disneyplus")]


@pytest.mark.asyncio
async def test_an_app_that_does_not_open_is_reported_as_not_installed() -> None:
    """When the app never becomes the current app, Reachy says so instead of claiming success."""
    result = await TV()(_deps(_FakeTV()), action="open", app="YouTube Kids")
    assert result["ok"] is False and "not installed" in result["error"]


@pytest.mark.asyncio
async def test_unknown_app_lists_the_known_ones() -> None:
    """An app Reachy does not know is not guessed."""
    tv = _FakeTV()
    result = await TV()(_deps(tv), action="open", app="Solitaire")
    assert "Unknown app" in result["error"] and "Netflix" in result["apps"]
    assert tv.calls == []


@pytest.mark.asyncio
async def test_youtube_search_opens_the_results_link() -> None:
    """The query is URL-encoded into a YouTube search link."""
    tv = _FakeTV()
    await TV()(_deps(tv), action="youtube", query="peppa pig & george")
    assert tv.calls == [("open", "https://www.youtube.com/results?search_query=peppa+pig+%26+george")]


@pytest.mark.asyncio
async def test_navigation_and_media_keys() -> None:
    """Spoken key names map to remote key codes."""
    tv = _FakeTV()
    await TV()(_deps(tv), action="navigate", key="down", repeat=2)
    await TV()(_deps(tv), action="media", key="pause")
    assert tv.calls == [("key", "DPAD_DOWN", 2), ("key", "MEDIA_PAUSE", 1)]


@pytest.mark.asyncio
async def test_an_unreachable_tv_is_reported_without_retry() -> None:
    """A TV error tells Reachy not to retry."""
    result = await TV()(_deps(_FakeTV(error=TVError("the TV is not reachable"))), action="status")
    assert "not reachable" in result["error"] and "Do not retry" in result["instruction"]


@pytest.mark.asyncio
async def test_no_tv_configured() -> None:
    """Without a paired TV the tool says so."""
    assert await TV()(_deps(None), action="status") == {"error": "No TV is set up."}


@pytest.mark.asyncio
async def test_a_slow_tv_is_reported_as_turning_on_not_as_a_failure() -> None:
    """The key went out but the TV has not reported yet: it is waking, and pressing power again would undo it."""
    tv = _FakeTV(is_on=False)

    async def slow_power(on: bool) -> bool:
        tv.calls.append(("power", on))
        return False

    tv.power = slow_power  # type: ignore[method-assign]
    result = await TV()(_deps(tv), action="power", on=True)
    assert result["ok"] is True and result["confirmed"] is False
    assert "Do not retry" in result["note"]
