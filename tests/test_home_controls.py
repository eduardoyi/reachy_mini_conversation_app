"""The admin app's home controls: what the phone sees, and each change going through Reachy's own tools."""

import json
import asyncio
from types import SimpleNamespace
from typing import Any

import pytest

from reachy_mini_conversation_app import home_controls
from reachy_mini_conversation_app.now_playing import Playback
from reachy_mini_conversation_app.home_devices import Routine, HubScene, HubDevice, HomeLayout, HomeDeviceError
from reachy_mini_conversation_app.home_controls import TV_APPS, HomeControls


LAMP = HubDevice("1", "outlet", "Dining lamp", "Living room", True, True, areas=("Dining",))
STRIP = HubDevice("2", "light", "TV Lights", "Living room", False, True, brightness=40, colors=True, color_only=True)
OLD = HubDevice("3", "light", "Dining lamp", None, False, False)  # an old offline device with the same name
LAYOUT = HomeLayout(
    areas={"Dining": ("dining lamp",)},
    routines=(
        Routine(say=("movie mode",), on=("TV Lights",), off=("all",)),
        Routine(say=("good night",), off=("all",), sleep=True, tv_off=True, pause_music=True),
        Routine(say=("quiet",), pause_music=True),
    ),
)


class _Hub:
    def __init__(self, *, fail: bool = False, slow: bool = False) -> None:
        self.layout = LAYOUT
        self.fail = fail
        self.slow = slow

    async def devices(self) -> list[HubDevice]:
        if self.fail:
            raise HomeDeviceError("hub down")
        if self.slow:
            await asyncio.sleep(1)
        return [LAMP, STRIP, OLD]

    async def scenes(self) -> list[HubScene]:
        return [HubScene("s1", "Relax")]


class _Tool:
    """A tool that records its calls and answers from ``answers`` (by action) or ``{"ok": True}``."""

    def __init__(self, answers: dict[str, dict[str, Any]] | None = None) -> None:
        self.calls: list[dict[str, Any]] = []
        self.answers = answers or {}

    async def __call__(self, deps: Any, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(kwargs)
        return dict(self.answers.get(kwargs.get("action", ""), {"ok": True}))


def _controls(*, hub: Any = None, has_tv: bool = True, music: bool = True, **tools: Any) -> HomeControls:
    deps = SimpleNamespace(home_hub=hub, tv=object() if has_tv else None)
    playback = tools.pop("playback", None)
    if music and playback is None:

        async def playback() -> Playback | None:
            return Playback("t1", "Under Pressure", "Queen", "Hot Space", "https://i.scdn.co/a", "al1", True, 9.0, 35)

    return HomeControls(deps, playback=playback if music else None, **tools)  # type: ignore[arg-type]


@pytest.fixture(autouse=True)
def no_spotify(monkeypatch: pytest.MonkeyPatch) -> None:
    """Spotify counts as set up only through the fake playback, whatever this machine's environment says."""
    monkeypatch.delenv("SPOTIFY_CREDENTIALS_FILE", raising=False)
    monkeypatch.delenv("SPOTIFY_DEVICE_ID", raising=False)


@pytest.mark.asyncio
async def test_the_phone_sees_lights_routines_tv_and_music() -> None:
    """Devices without offline duplicates, areas, routines then scenes, the TV with its app buttons, the song."""
    tv = _Tool({"status": {"on": True, "app": "YouTube", "volume": 20, "muted": False}})
    status = await _controls(hub=_Hub(), tv=tv).status()

    lights = status["lights"]
    assert [device["name"] for device in lights["devices"]] == ["Dining lamp", "TV Lights"]
    assert lights["devices"][0] == {
        "name": "Dining lamp",
        "kind": "plug",
        "room": "Living room",
        "areas": ["Dining"],
        "on": True,
        "reachable": True,
        "brightness": None,
        "warmth": False,
        "colors": False,
    }
    assert lights["areas"] == ["Dining"]
    assert lights["routines"] == ["movie mode", "good night", "quiet", "Relax"]
    assert status["tv"] == {"on": True, "app": "YouTube", "volume": 20, "muted": False, "apps": list(TV_APPS)}
    assert status["music"] == {
        "playing": True,
        "title": "Under Pressure",
        "artist": "Queen",
        "art": "https://i.scdn.co/a",
        "volume": 35,
    }


@pytest.mark.asyncio
async def test_parts_not_set_up_are_none_and_parts_that_fail_say_so(monkeypatch: pytest.MonkeyPatch) -> None:
    """No hub, TV or Spotify: nothing to show. A part that fails or is too slow has an error; the rest still show."""
    assert await _controls(has_tv=False, music=False).status() == {"lights": None, "tv": None, "music": None}

    async def broken() -> Playback | None:
        raise OSError("no network")

    tv = _Tool({"status": {"error": "The TV did not respond: timeout"}})
    status = await _controls(hub=_Hub(fail=True), tv=tv, playback=broken).status()
    assert status["lights"] == {"error": "The lights didn't answer."}
    assert status["tv"]["error"] == "The TV didn't answer."
    assert status["music"] == {"error": "Spotify didn't answer."}

    monkeypatch.setattr(home_controls, "LIGHTS_TIMEOUT_S", 0.05)
    slow = await _controls(hub=_Hub(slow=True), tv=_Tool()).status()
    assert slow["lights"] == {"error": "The lights didn't answer."}

    async def nothing() -> Playback | None:
        return None

    assert (await _controls(playback=nothing, has_tv=False).status())["music"] == {"playing": False, "title": None}


@pytest.mark.asyncio
async def test_a_light_change_goes_through_the_lights_tool() -> None:
    """Only known fields of the right type pass; brightness is clamped; nothing to change is refused."""
    lights = _Tool()
    controls = _controls(hub=_Hub(), lights=lights)
    await controls.light({"target": "Dining", "on": False})
    await controls.light({"target": "TV Lights", "brightness": 250, "warmth": "warm", "color": "blue", "extra": 1})
    await controls.light({"target": "TV Lights", "on": "yes", "warmth": "hot", "brightness": True, "color": " "})
    assert (await controls.light({"on": True}))["error"] == "Say which light."
    await controls.all_off()
    assert lights.calls == [
        {"action": "set", "target": "Dining", "on": False},
        {"action": "set", "target": "TV Lights", "brightness": 100, "warmth": "warm", "color": "blue"},
        {"action": "set", "target": "all", "on": False},
    ]


@pytest.mark.asyncio
async def test_a_routine_runs_its_lights_tv_music_and_sleep() -> None:
    """Like the spoken routine: its lights through the tool, the TV off, music paused if playing, sleep asked."""
    lights, tv = _Tool(), _Tool()
    spotify = _Tool({"status": {"status": "playing"}})
    controls = _controls(hub=_Hub(), lights=lights, tv=tv, spotify=spotify)

    answer = await controls.routine("Good night")
    assert answer["sleep"] is True
    assert lights.calls == [{"action": "scene", "scene": "Good night", "lights_only": True}]
    assert tv.calls == [{"action": "power", "on": False}]
    assert spotify.calls == [{"action": "status"}, {"action": "pause"}]

    lights.calls.clear()
    assert await controls.routine("quiet") == {"ok": True, "routine": "quiet", "sleep": False}
    assert lights.calls == [], "a routine with no lights does not touch them"

    assert await controls.routine("Relax") == {"ok": True}, "a hub scene runs as a scene"
    assert (await _controls(hub=None).routine("movie mode"))["error"] == "No such routine."


@pytest.mark.asyncio
async def test_a_paused_song_is_not_paused_again_and_a_failed_routine_stops() -> None:
    """Music already paused is left alone; when the lights fail the TV and music are not touched."""
    tv = _Tool()
    spotify = _Tool({"status": {"status": "paused"}})
    await _controls(hub=_Hub(), lights=_Tool(), tv=tv, spotify=spotify).routine("good night")
    assert spotify.calls == [{"action": "status"}]

    lights = _Tool({"scene": {"error": "A game is on"}})
    tv.calls.clear()
    assert await _controls(hub=_Hub(), lights=lights, tv=tv).routine("good night") == {"error": "A game is on"}
    assert tv.calls == []


@pytest.mark.asyncio
async def test_tv_and_music_commands_are_checked_before_the_tools() -> None:
    """Unknown commands are refused; only the remote's own fields pass; the TV's advice to Gemini is dropped."""
    tv = _Tool({"open": {"ok": False, "error": "not installed", "instruction": "Tell the person."}})
    spotify = _Tool()
    controls = _controls(hub=_Hub(), tv=tv, spotify=spotify)
    assert (await controls.tv({"action": "reboot"}))["error"] == "Unknown TV command."
    assert await controls.tv({"action": "open", "app": "Netflix", "on": "x"}) == {
        "ok": False,
        "error": "not installed",
    }
    await controls.tv({"action": "navigate", "key": "up", "repeat": 2, "steps": False})
    assert tv.calls == [{"action": "open", "app": "Netflix"}, {"action": "navigate", "key": "up", "repeat": 2}]

    assert (await controls.music({"action": "play"}))["error"] == "Unknown music command."
    await controls.music({"action": "volume", "volume": 40})
    await controls.music({"action": "next"})
    assert spotify.calls == [{"action": "volume", "volume_percent": 40}, {"action": "next"}]
    assert (await _controls(music=False).music({"action": "pause"}))["error"] == "Spotify isn't set up."


def _layout_file(tmp_path: Any) -> Any:
    path = tmp_path / "layout.json"
    path.write_text(
        '{"areas": {"Dining": ["Dining lamp"]}, "color_only": ["TV Lights"], "routines": ['
        '{"say": ["movie mode", "modo cine"], "on": ["TV Lights"], "off": ["all"]},'
        '{"say": ["good night"], "off": ["all"], "sleep": true, "tv": "off", "music": "pause"}]}'
    )
    return path


class _LiveHub(_Hub):
    """A hub whose layout can be replaced, as HomeDevices' can."""

    def set_layout(self, layout: HomeLayout) -> None:
        self.layout = layout


@pytest.mark.asyncio
async def test_routines_are_read_from_the_layout_file_with_what_they_can_name(tmp_path: Any) -> None:
    """The phone gets each routine as the file holds it, the areas, rooms and lights to pick from, and a version."""
    path = _layout_file(tmp_path)
    answer = await HomeControls(SimpleNamespace(home_hub=_LiveHub(), tv=None), layout_file=path).routines()  # type: ignore[arg-type]
    assert answer["routines"] == [
        {"say": ["movie mode", "modo cine"], "on": ["TV Lights"], "off": ["all"]},
        {"say": ["good night"], "off": ["all"], "sleep": True, "tv": "off", "music": "pause"},
    ]
    assert answer["targets"] == {
        "areas": ["Dining"],
        "rooms": ["Living room"],
        "devices": ["Dining lamp", "TV Lights"],
    }
    assert answer["scenes"] == ["Relax"] and len(answer["version"]) == 16


@pytest.mark.asyncio
async def test_saving_routines_rewrites_the_file_and_reachy_uses_them_at_once(tmp_path: Any) -> None:
    """The file keeps everything else and a .bak of the previous one; the hub's layout is the new one."""
    path = _layout_file(tmp_path)
    hub = _LiveHub()
    controls = HomeControls(SimpleNamespace(home_hub=hub, tv=None), layout_file=path)  # type: ignore[arg-type]
    version = (await controls.routines())["version"]
    new = [
        {"say": ["  movie   mode ", "Modo cine", "modo cine"], "on": ["TV Lights"], "off": ["all"]},
        {"say": ["bedtime"], "off": ["Dining", "dining lamp"], "tv": "off", "sleep": True, "extra": 1},
    ]
    answer = await controls.save_routines({"version": version, "routines": new})
    assert answer["ok"] is True and answer["count"] == 2 and answer["version"] != version
    saved = json.loads(path.read_text())
    assert saved["routines"] == [
        {"say": ["movie mode", "Modo cine"], "on": ["TV Lights"], "off": ["all"]},
        {"say": ["bedtime"], "off": ["Dining", "dining lamp"], "sleep": True, "tv": "off"},
    ]
    assert saved["color_only"] == ["TV Lights"] and saved["areas"] == {"Dining": ["Dining lamp"]}
    assert "good night" in (tmp_path / "layout.json.bak").read_text()
    assert [routine.name for routine in hub.layout.routines] == ["movie mode", "bedtime"]


@pytest.mark.parametrize(
    ("routines", "error"),
    [
        ([{"say": [], "on": ["all"]}], "Every routine needs a name."),
        ([{"say": ["party"], "on": ["Garage"]}], "party: nothing is called Garage."),
        ([{"say": ["party"], "sleep": True}], "party doesn't do anything yet: pick lights, the TV or the music."),
        (
            [{"say": ["party"], "on": ["all"]}, {"say": ["fiesta", "Party"], "off": ["all"]}],
            "“Party” starts two routines (party and fiesta).",
        ),
        ([{"say": ["x" * 61], "on": ["all"]}], "at most 8 phrases of 60 characters"),
        ("not a list", "Send at most 30 routines."),
    ],
)
@pytest.mark.asyncio
async def test_routines_that_cannot_work_are_not_saved(tmp_path: Any, routines: Any, error: str) -> None:
    """Nothing is written when a routine has no name, names nothing real, does nothing, or shares a phrase."""
    path = _layout_file(tmp_path)
    before = path.read_text()
    controls = HomeControls(SimpleNamespace(home_hub=_LiveHub(), tv=None), layout_file=path)  # type: ignore[arg-type]
    version = (await controls.routines())["version"]
    answer = await controls.save_routines({"version": version, "routines": routines})
    assert error in answer["error"]
    assert path.read_text() == before


@pytest.mark.asyncio
async def test_a_save_over_someone_elses_change_is_refused(tmp_path: Any) -> None:
    """Opened, then the file changed (another phone, or by hand): the save is refused and nothing is lost."""
    path = _layout_file(tmp_path)
    controls = HomeControls(SimpleNamespace(home_hub=_LiveHub(), tv=None), layout_file=path)  # type: ignore[arg-type]
    version = (await controls.routines())["version"]
    path.write_text(path.read_text().replace("modo cine", "modo peli"))
    answer = await controls.save_routines({"version": version, "routines": [{"say": ["lights"], "on": ["all"]}]})
    assert "changed since you opened them" in answer["error"]
    assert "modo peli" in path.read_text()
