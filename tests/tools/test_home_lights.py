import logging
from typing import Any
from dataclasses import replace
from unittest.mock import MagicMock

import pytest

from reachy_mini_conversation_app.home_devices import HubScene, HubDevice, LightChange, HomeDeviceError
from reachy_mini_conversation_app.tools.core_tools import ToolDependencies
from reachy_mini_conversation_app.tools.home_lights import HomeLights


class _FakeHome:
    def __init__(self, devices: list[HubDevice], scenes: list[HubScene] | None = None) -> None:
        self._devices = devices
        self._scenes = scenes or []
        self.changes: list[tuple[str, LightChange]] = []
        self.triggered: list[str] = []

    async def devices(self) -> list[HubDevice]:
        return self._devices

    async def scenes(self) -> list[HubScene]:
        return self._scenes

    async def apply(self, device: HubDevice, change: LightChange) -> None:
        self.changes.append((device.id, change))

    async def trigger_scene(self, scene: HubScene) -> None:
        self.triggered.append(scene.id)


LAMP = HubDevice("1", "light", "Lámpara de pie", "Salón", False, True, brightness=50, kelvin_range=(2200, 4000))
CEILING = HubDevice("2", "light", "Techo", "Salón", True, True, brightness=80)
PLUG = HubDevice("3", "outlet", "Floor lamp plug", "Bedroom", False, True, source="tuya")
OFFLINE = HubDevice("4", "light", "Kids lamp", "Kids room", False, False, brightness=10)
STRIP = HubDevice("5", "light", "TV Lights", None, True, True, brightness=40, colors=True, source="tuya")
CEILING_WARM = HubDevice("2", "light", "Techo", "Salón", True, True, brightness=80, kelvin_range=(2200, 4000))


def _deps(home: Any) -> ToolDependencies:
    return ToolDependencies(reachy_mini=MagicMock(), movement_manager=MagicMock(), home_hub=home)


@pytest.mark.asyncio
async def test_a_room_name_switches_every_light_in_it() -> None:
    """A room name without its accent ("salon") turns on both lights in the Salón."""
    home = _FakeHome([LAMP, CEILING, PLUG])
    result = await HomeLights()(_deps(home), action="set", target="salon", on=True)
    assert result == {"ok": True, "changed": ["Lámpara de pie", "Techo"]}
    assert home.changes == [("1", LightChange(on=True)), ("2", LightChange(on=True))]


@pytest.mark.asyncio
async def test_each_device_gets_only_what_it_supports() -> None:
    """Brightness and warmth go to the dimmable CCT lamp; the plug only switches; warmth alone turns it on."""
    home = _FakeHome([LAMP, PLUG])
    result = await HomeLights()(_deps(home), action="set", target="all", brightness=30, warmth="warm")
    assert home.changes == [("1", LightChange(on=True, brightness=30, warmth=0.0))]
    assert result["changed"] == ["Lámpara de pie"] and "Plugs only switch" in result["note"]


@pytest.mark.asyncio
async def test_colors_go_to_color_lights_and_white_means_white_light() -> None:
    """A named or hex color reaches the strip; "white" asks for neutral white light instead of a color."""
    home = _FakeHome([STRIP, CEILING])
    result = await HomeLights()(_deps(home), action="set", target="tv", color="purple")
    assert home.changes == [("5", LightChange(on=True, color=(275.0, 1.0)))]
    assert result == {"ok": True, "changed": ["TV Lights"]}

    home = _FakeHome([STRIP])
    await HomeLights()(_deps(home), action="set", target="TV Lights", color="#00ff00", brightness=60)
    assert home.changes == [("5", LightChange(on=True, brightness=60, color=(120.0, 1.0)))]

    home = _FakeHome([CEILING])
    result = await HomeLights()(_deps(home), action="set", target="techo", color="blue")
    assert result["changed"] == [] and "cannot show colors" in result["note"]
    assert "error" in await HomeLights()(_deps(home), action="set", target="techo", color="mauve-ish")


@pytest.mark.asyncio
async def test_unknown_targets_list_the_names_and_offline_lights_are_reported() -> None:
    """A name that matches nothing returns the choices; unreachable lights are skipped and named."""
    home = _FakeHome([LAMP, OFFLINE])
    missing = await HomeLights()(_deps(home), action="set", target="garage", on=False)
    assert missing["rooms"] == ["Kids room", "Salón"] and "Kids lamp" in missing["names"]

    result = await HomeLights()(_deps(home), action="set", target="kids room", on=True)
    assert (result["ok"], result["changed"], result["unreachable"]) == (False, [], ["Kids lamp"])
    assert "Do not retry" in result["instruction"]
    assert home.changes == []


@pytest.mark.asyncio
async def test_a_change_logs_the_devices_it_matched_and_changed_but_not_the_words(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The log names the matched devices and the results; the target as said never appears."""
    home = _FakeHome([LAMP, CEILING, OFFLINE])
    with caplog.at_level(logging.INFO):
        await HomeLights()(_deps(home), action="set", target="lampara de pie", on=False)
        await HomeLights()(_deps(home), action="set", target="the garage thing", on=False)
    assert "change=on on=False matched=['Lámpara de pie'] changed=['Lámpara de pie'] failed=[]" in caplog.text
    assert "home_lights set matched nothing change=on" in caplog.text
    assert "lampara de pie" not in caplog.text and "garage" not in caplog.text


@pytest.mark.asyncio
async def test_an_offline_device_with_the_same_name_as_an_online_one_is_ignored() -> None:
    """An old bulb left in another app under the same name neither matches nor shows in status."""
    old = HubDevice("9", "light", "Techo", None, False, False, brightness=50, source="tuya")
    home = _FakeHome([CEILING, old])
    assert await HomeLights()(_deps(home), action="set", target="techo", on=False) == {
        "ok": True,
        "changed": ["Techo"],
    }
    assert [device["name"] for device in (await HomeLights()(_deps(home), action="status"))["devices"]] == ["Techo"]


@pytest.mark.asyncio
async def test_status_and_scenes() -> None:
    """Status lists rooms, states and scenes; a scene runs by (partial) name."""
    home = _FakeHome([CEILING, PLUG], [HubScene("s1", "Movie night"), HubScene("s2", "Bedtime")])
    status = await HomeLights()(_deps(home), action="status")
    assert status["rooms"] == ["Bedroom", "Salón"]
    assert status["devices"][0] == {"name": "Techo", "on": True, "room": "Salón", "brightness": 80}
    assert status["scenes"] == ["Movie night", "Bedtime"]

    assert await HomeLights()(_deps(home), action="scene", scene="movie") == {"ok": True, "scene": "Movie night"}
    assert home.triggered == ["s1"]


@pytest.mark.asyncio
async def test_missing_setup_bad_requests_and_device_errors_are_reported() -> None:
    """Every failure comes back as an error for the model, never an exception."""

    class _DownHome(_FakeHome):
        async def devices(self) -> list[HubDevice]:
            raise HomeDeviceError("hub unreachable (ConnectError)")

    assert "error" in await HomeLights()(_deps(None), action="status")
    assert "error" in await HomeLights()(_deps(_FakeHome([LAMP])), action="set", target="salon")
    assert "unreachable" in (await HomeLights()(_deps(_DownHome([])), action="status"))["error"]


@pytest.mark.parametrize(("token", "expected"), [("t", True), (None, False)])
def test_home_lights_loads_once_lights_are_set_up(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Any, token: str | None, expected: bool
) -> None:
    """The lights tool appears only with a paired hub or a Tuya device list."""
    from reachy_mini_conversation_app.tools import core_tools
    from reachy_mini_conversation_app.config import config

    monkeypatch.setattr(config, "DIRIGERA_HOST", "hub.local")
    monkeypatch.setattr(config, "DIRIGERA_TOKEN", token)
    monkeypatch.setattr(config, "TUYA_DEVICES_FILE", str(tmp_path / "missing.json"))
    monkeypatch.setattr(config, "AUTOLOAD_EXTERNAL_TOOLS", False)
    monkeypatch.setattr(core_tools, "read_profile_tool_names", lambda *_args: ["camera", "home_lights"])
    monkeypatch.setattr(core_tools, "read_profile_tool_override", lambda *_args: None)

    assert ("home_lights" in core_tools._read_profile_tool_names(None)) is expected
    if not expected:
        (tmp_path / "missing.json").write_text("[]")
        assert "home_lights" in core_tools._read_profile_tool_names(None)


@pytest.mark.asyncio
@pytest.mark.parametrize("target", ["the lights", "Las luces", "todas as luzes", "le luci", "all"])
async def test_plain_the_lights_means_every_light_and_plug(target: str) -> None:
    """Plain "the lights", in any household language, reaches lamps on plugs too."""
    home = _FakeHome([LAMP, PLUG, STRIP])
    result = await HomeLights()(_deps(home), action="set", target=target, on=True)
    assert result["changed"] == ["Lámpara de pie", "Floor lamp plug", "TV Lights"]


@pytest.mark.asyncio
async def test_set_without_a_target_means_every_light() -> None:
    """A set with no target ("turn the light off") switches every light, as plain "the lights" does."""
    home = _FakeHome([LAMP, PLUG, STRIP])
    result = await HomeLights()(_deps(home), action="set", on=True)
    assert result["changed"] == ["Lámpara de pie", "Floor lamp plug", "TV Lights"]


@pytest.mark.asyncio
async def test_areas_split_a_room_and_the_room_still_means_both() -> None:
    """With Living and Dining areas in one living room, each area switches alone; the room switches both."""
    from reachy_mini_conversation_app.home_devices import HomeLayout

    layout = HomeLayout(
        rooms={"tv lights": "Living room"},
        areas={"Living": ("sofa light", "tv lights"), "Dining": ("dining light 1",)},
    )
    sofa = HubDevice("1", "light", "Sofa light", "Living room", False, True, brightness=50)
    dining = HubDevice("2", "light", "Dining light 1", "Living room", False, True, brightness=50)
    strip = HubDevice("3", "light", "TV Lights", None, False, True, brightness=50, colors=True, source="tuya")
    devices = [layout.apply(device) for device in (sofa, dining, strip)]
    assert devices[2].room == "Living room" and devices[2].areas == ("Living",)

    for target, expected in [
        ("the dining lights", ["Dining light 1"]),
        ("comedor", None),
        ("dining area", ["Dining light 1"]),
        ("living", ["Sofa light", "TV Lights"]),
        ("living room", ["Sofa light", "Dining light 1", "TV Lights"]),
    ]:
        home = _FakeHome(devices)
        result = await HomeLights()(_deps(home), action="set", target=target, on=True)
        assert result.get("changed") == expected, target

    status = await HomeLights()(_deps(_FakeHome(devices)), action="status")
    assert status["areas"] == ["Dining", "Living"] and status["devices"][2]["area"] == "Living"


@pytest.mark.asyncio
async def test_a_device_name_wins_over_the_area_it_shares_a_word_with() -> None:
    """A target of "Living lamp" switched the whole Living area (2026-10-19); now it is just the lamp."""
    from reachy_mini_conversation_app.home_devices import HomeLayout

    layout = HomeLayout(areas={"Living": ("sofa light", "living lamp")})
    sofa = HubDevice("1", "light", "Sofa light", "Living room", False, True, brightness=50)
    lamp = HubDevice("2", "outlet", "Living lamp", "Living room", False, True, source="tuya")
    devices = [layout.apply(device) for device in (sofa, lamp)]

    for target in ("Living lamp", "living room lamp"):
        result = await HomeLights()(_deps(_FakeHome(devices)), action="set", target=target, on=True)
        assert result.get("changed") == ["Living lamp"], target
    result = await HomeLights()(_deps(_FakeHome(devices)), action="set", target="living", on=True)
    assert result.get("changed") == ["Sofa light", "Living lamp"]


def test_home_layout_loads_and_tolerates_a_missing_file(tmp_path: Any) -> None:
    """The layout file is optional and matches device names without case or accents."""
    from reachy_mini_conversation_app.home_devices import HomeLayout

    assert HomeLayout.load(tmp_path / "absent.json") == HomeLayout()
    path = tmp_path / "layout.json"
    path.write_text('{"rooms": {"Lámpara": "Salón"}, "areas": {"Dining": ["Dining Lamp"]}}')
    layout = HomeLayout.load(path)
    lamp = layout.apply(HubDevice("1", "outlet", "lampara", None, False, True))
    assert lamp.room == "Salón"
    assert layout.apply(HubDevice("2", "outlet", "dining lamp", "Kitchen", False, True)).areas == ("Dining",)


def test_home_layout_marks_color_only_lights_and_drops_their_white_mode(tmp_path: Any) -> None:
    """A color-only light loses the white range its system claims; a light without colors is left alone."""
    from reachy_mini_conversation_app.home_devices import HomeLayout

    path = tmp_path / "layout.json"
    path.write_text('{"color_only": ["tv lights", "Floor lamp plug"]}')
    layout = HomeLayout.load(path)
    strip = layout.apply(replace(STRIP, kelvin_range=(2700, 6500)))
    assert strip.color_only and not strip.warmth and strip.colors
    assert layout.apply(PLUG) == PLUG


@pytest.mark.asyncio
async def test_white_and_warmth_on_a_color_only_light_become_a_color() -> None:
    """The RGB strip went dark in white mode, so white is sent to it as a color instead."""
    from reachy_mini_conversation_app.home_devices import white_as_color

    strip = replace(STRIP, color_only=True)
    home = _FakeHome([strip, CEILING_WARM])
    result = await HomeLights()(_deps(home), action="set", target="all", color="white")
    changes = dict(home.changes)
    assert changes["5"] == LightChange(on=True, color=white_as_color(0.5))
    assert changes["2"] == LightChange(on=True, warmth=0.5)
    assert "note" not in result

    home.changes.clear()
    await HomeLights()(_deps(home), action="set", target="TV Lights", warmth="warm")
    assert home.changes == [("5", LightChange(on=True, color=(30.0, 0.5)))]
    assert white_as_color(1.0) == (30.0, 0.0)


@pytest.mark.asyncio
async def test_a_failing_light_is_reported_once_and_not_retried(caplog: pytest.LogCaptureFixture) -> None:
    """A device that raises is logged with the cause, and the reply tells Reachy not to retry."""

    class _BrokenStrip(_FakeHome):
        async def apply(self, device: HubDevice, change: LightChange) -> None:
            if device.id == "5":
                raise TypeError("%x format: an integer is required, not float")
            await super().apply(device, change)

    home = _BrokenStrip([STRIP, CEILING])
    with caplog.at_level("WARNING"):
        result = await HomeLights()(_deps(home), action="set", target="all", on=True)
    assert result["failed"] == ["TV Lights"] and result["changed"] == ["Techo"]
    assert "Do not retry" in result["instruction"]
    assert any("TV Lights failed: TypeError" in record.message for record in caplog.records)


def _household(tmp_path: Any) -> Any:
    from reachy_mini_conversation_app.home_devices import HomeLayout

    path = tmp_path / "layout.json"
    path.write_text(
        '{"left_out_of_all_on": ["TV Lights"], "routines": ['
        '{"say": ["movie mode", "modo cine"], "on": ["TV Lights"], "off": ["all"]},'
        '{"say": ["lights"], "on": ["all"]},'
        '{"say": ["good night"], "off": ["all"], "sleep": true},'
        '{"say": ["broken"]}]}'
    )
    return HomeLayout.load(path)


@pytest.mark.asyncio
async def test_turning_on_all_the_lights_leaves_out_the_tv_backlight(tmp_path: Any) -> None:
    """Turning on the lights skips TV Lights unless they are named; turning everything off includes them."""
    home = _FakeHome([LAMP, CEILING, STRIP])
    home.layout = _household(tmp_path)  # type: ignore[attr-defined]
    assert (await HomeLights()(_deps(home), action="set", target="the lights", on=True))["changed"] == [
        "Lámpara de pie",
        "Techo",
    ]
    assert (await HomeLights()(_deps(home), action="set", target="all", on=False))["changed"] == [
        "Lámpara de pie",
        "Techo",
        "TV Lights",
    ]
    assert (await HomeLights()(_deps(home), action="set", target="tv lights", on=True))["changed"] == ["TV Lights"]


@pytest.mark.asyncio
async def test_a_routine_runs_as_a_scene_and_is_listed(tmp_path: Any) -> None:
    """Movie mode turns the TV backlight on and every other light off; it shows among the scenes."""
    layout = _household(tmp_path)
    assert [routine.name for routine in layout.routines] == ["movie mode", "lights", "good night"]  # "broken" dropped
    home = _FakeHome([LAMP, CEILING, STRIP, OFFLINE])
    home.layout = layout  # type: ignore[attr-defined]
    result = await HomeLights()(_deps(home), action="scene", scene="Modo Cine")
    assert result["routine"] == "movie mode" and result["ok"] is True
    assert home.changes == [
        ("5", LightChange(on=True)),
        ("1", LightChange(on=False)),
        ("2", LightChange(on=False)),
    ]  # the offline lamp is skipped
    status = await HomeLights()(_deps(home), action="status")
    assert status["scenes"] == ["movie mode", "lights", "good night"]


@pytest.mark.asyncio
async def test_lights_left_out_are_named_even_when_others_changed() -> None:
    """One light switched and one unreachable: the reply still tells the person which one did not respond."""
    home = _FakeHome([LAMP, OFFLINE])
    result = await HomeLights()(_deps(home), action="set", target="all", on=True)
    assert (result["ok"], result["changed"], result["unreachable"]) == (True, ["Lámpara de pie"], ["Kids lamp"])
    assert "which lights did not respond" in result["instruction"]
