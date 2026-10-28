import json
from typing import Any
from pathlib import Path
from dataclasses import replace

import pytest

from reachy_mini_conversation_app.home_devices import HubDevice, HomeDevices, LightChange, HomeDeviceError
from reachy_mini_conversation_app.tuya_devices import TuyaHome, TuyaEntry, load_entries


def _write_wizard_files(folder: Path) -> Path:
    devices = [
        {"id": "strip", "name": "TV Lights", "key": "k1", "category": "dj"},
        {"id": "plug", "name": "Living lamp", "key": "k2", "category": "cz"},
        {"id": "ir", "name": "Air Conditioning", "key": "k3", "category": "infrared_ac", "sub": True},
        {"id": "nokey", "name": "Old", "key": "", "category": "dj"},
        {"id": "offline", "name": "Study light", "key": "k4", "category": "dj"},
    ]
    (folder / "devices.json").write_text(json.dumps(devices))
    snapshot = {"devices": [{"id": "strip", "ip": "10.0.0.5", "ver": "3.3"}, {"id": "plug", "ip": "10.0.0.6"}]}
    (folder / "snapshot.json").write_text(json.dumps(snapshot))
    return folder / "devices.json"


class _FakeBulb:
    dpset = {"value_min": 25, "value_max": 255}

    def __init__(self, mode: str = "white", fail: bool = False) -> None:
        self.mode = mode
        self.fail = fail
        self.calls: list[tuple[Any, ...]] = []

    def state(self) -> dict[str, Any]:
        if self.fail:
            return {"Error": "Network Error: Device Unreachable"}
        return {"is_on": True, "mode": self.mode, "brightness": 140, "colourtemp": 56, "colour": "0f332f00aeb445"}

    def colour_hsv(self, state: Any = None) -> tuple[float, float, float]:
        return (0.5, 0.8, 0.4)

    def get_mode(self) -> str:
        return self.mode

    def turn_on(self) -> dict[str, Any]:
        self.calls.append(("on",))
        return {}

    def turn_off(self) -> dict[str, Any]:
        self.calls.append(("off",))
        return {}

    def set_hsv(self, h: float, s: float, v: float) -> dict[str, Any]:
        raise TypeError("%x format: an integer is required, not float")  # TinyTuya 1.20, old-format bulbs

    def set_colour(self, r: int, g: int, b: int) -> dict[str, Any]:
        assert all(isinstance(c, int) for c in (r, g, b))
        self.calls.append(("rgb", r, g, b))
        return {}

    def set_white_percentage(self, brightness: int, colourtemp: int) -> dict[str, Any]:
        self.calls.append(("white", brightness, colourtemp))
        return {}

    def set_brightness_percentage(self, brightness: int) -> dict[str, Any]:
        self.calls.append(("brightness", brightness))
        return {"Error": "Device Unreachable"} if self.fail else {}

    def set_mode(self, mode: str) -> dict[str, Any]:
        self.calls.append(("mode", mode))
        self.mode = mode
        return {}


class _FakePlug:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def status(self) -> dict[str, Any]:
        return {"dps": {"1": True, "11": 0}}

    def turn_on(self) -> dict[str, Any]:
        self.calls.append("on")
        return {}

    def turn_off(self) -> dict[str, Any]:
        self.calls.append("off")
        return {}


def _home(tmp_path: Path, bulb: _FakeBulb, plug: _FakePlug) -> tuple[TuyaHome, list[tuple[str, str, str]]]:
    connections: list[tuple[str, str, str]] = []

    def factory(entry: TuyaEntry, ip: str, version: str) -> Any:
        connections.append((entry.id, ip, version))
        return bulb if entry.kind == "light" else plug

    return TuyaHome(_write_wizard_files(tmp_path), device_factory=factory), connections


def test_only_keyed_lights_and_plugs_load(tmp_path: Path) -> None:
    """IR sub-devices, other categories and devices without a key are left out."""
    entries = load_entries(_write_wizard_files(tmp_path))
    assert [(entry.id, entry.kind) for entry in entries] == [
        ("strip", "light"),
        ("plug", "outlet"),
        ("offline", "light"),
    ]
    assert load_entries(tmp_path / "absent.json") == []


@pytest.mark.asyncio
async def test_devices_read_state_from_the_snapshot_addresses(tmp_path: Path) -> None:
    """Brightness comes from the bulb's range (or the color value in color mode); no address means unreachable."""
    home, connections = _home(tmp_path, _FakeBulb(), _FakePlug())
    strip, plug, offline = await home.devices()

    assert ("strip", "10.0.0.5", "3.3") in connections and ("plug", "10.0.0.6", "3.3") in connections
    assert (strip.is_on, strip.brightness, strip.colors, strip.warmth, strip.source) == (True, 50, True, True, "tuya")
    assert (plug.kind, plug.is_on, plug.dims) == ("outlet", True, False)
    assert (offline.reachable, offline.name) == (False, "Study light")

    home, _ = _home(tmp_path, _FakeBulb(mode="colour"), _FakePlug())
    strip, *_ = await home.devices()
    assert strip.brightness == 40


@pytest.mark.asyncio
async def test_changes_drive_the_bulb_and_plug(tmp_path: Path) -> None:
    """Color keeps the current brightness, warmth uses white mode, off is just off; errors are raised."""
    bulb, plug = _FakeBulb(mode="colour"), _FakePlug()
    home, _ = _home(tmp_path, bulb, plug)
    strip, plug_device, offline = await home.devices()

    await home.apply(strip, LightChange(on=True, color=(275.0, 1.0)))
    await home.apply(strip, LightChange(on=True, brightness=80))
    await home.apply(strip, LightChange(on=True, warmth=0.0, brightness=30))
    await home.apply(strip, LightChange(on=False))
    await home.apply(plug_device, LightChange(on=False))
    assert bulb.calls == [
        ("on",),
        ("rgb", 59, 0, 102),  # purple at the strip's current color brightness (40 %)
        ("on",),
        ("rgb", 41, 204, 204),  # brighter, keeping the current color (hue 180, saturation 0.8)
        ("on",),
        ("white", 30, 0),
        ("off",),
    ]
    assert plug.calls == ["off"]

    with pytest.raises(HomeDeviceError, match="not on the network"):
        await home.apply(offline, LightChange(on=True))
    bulb.fail, bulb.mode = True, "white"
    with pytest.raises(HomeDeviceError, match="Unreachable"):
        await home.apply(strip, LightChange(brightness=20))


@pytest.mark.asyncio
async def test_a_plain_on_relights_a_strip_that_wrongly_reports_on(tmp_path: Path) -> None:
    """Switched off with its remote, the TV strip still said "on"; a plain on now goes off then on."""
    bulb, plug = _FakeBulb(), _FakePlug()
    home, _ = _home(tmp_path, bulb, plug)
    strip, plug_device, _offline = await home.devices()

    await home.apply(strip, LightChange(on=True))
    await home.apply(plug_device, LightChange(on=True))

    assert bulb.calls == [("off",), ("on",)]
    assert plug.calls == ["on"]


@pytest.mark.asyncio
async def test_a_color_only_strip_leaves_white_mode_when_switched_on(tmp_path: Path) -> None:
    """In white mode the RGB TV strip said "on" but stayed dark; switching it on now brings back its color."""
    bulb, plug = _FakeBulb(mode="white"), _FakePlug()
    home, _ = _home(tmp_path, bulb, plug)
    strip, _plug, _offline = await home.devices()
    strip = replace(strip, color_only=True, kelvin_range=None)

    await home.apply(strip, LightChange(on=True))
    assert bulb.calls[-2:] == [("on",), ("mode", "colour")]

    bulb.calls.clear()
    await home.apply(strip, LightChange(on=True))  # already in color: nothing more
    assert ("mode", "colour") not in bulb.calls
    bulb.mode = "white"
    await home.apply(strip, LightChange(on=False))
    assert bulb.calls[-1] == ("off",) and bulb.mode == "white"


def test_light_change_keeps_only_what_a_device_supports() -> None:
    """A plug drops brightness and color; a change with nothing left is None."""
    plug = HubDevice("p", "outlet", "Plug", None, False, True)
    assert LightChange(brightness=40).for_device(plug) is None
    assert LightChange(on=True, color=(0.0, 1.0)).for_device(plug) == LightChange(on=True)


@pytest.mark.asyncio
async def test_home_devices_merges_systems_and_survives_one_failing() -> None:
    """Devices from both systems are listed; a system that fails is skipped; changes go to the owner."""

    class _System:
        def __init__(self, devices: list[HubDevice] | None) -> None:
            self._devices = devices
            self.applied: list[str] = []

        async def devices(self) -> list[HubDevice]:
            if self._devices is None:
                raise HomeDeviceError("down")
            return self._devices

        async def scenes(self) -> list[Any]:
            return []

        async def apply(self, device: HubDevice, change: LightChange) -> None:
            self.applied.append(device.id)

        async def trigger_scene(self, scene_id: str) -> None: ...

        async def close(self) -> None: ...

    ikea = _System([HubDevice("1", "light", "Lamp", None, False, True)])
    tuya = _System(None)
    home = HomeDevices({"dirigera": ikea, "tuya": tuya})
    [lamp] = await home.devices()
    await home.apply(lamp, LightChange(on=True))
    assert ikea.applied == ["1"]

    with pytest.raises(HomeDeviceError):
        await HomeDevices({"tuya": tuya}).devices()


class _NappingPlug(_FakePlug):
    """A Wi-Fi plug in power saving: the first ``misses`` calls get ``error`` (TinyTuya's reply), then it answers."""

    def __init__(self, misses: int, error: str = "905") -> None:
        super().__init__()
        self.misses = misses
        self.error = error
        self.tries = 0

    def _miss(self) -> dict[str, Any] | None:
        self.tries += 1
        if self.tries <= self.misses:
            return {"Error": "Network Error: Device Unreachable", "Err": self.error, "Payload": None}
        return None

    def status(self) -> dict[str, Any]:
        return self._miss() or super().status()

    def turn_off(self) -> dict[str, Any]:
        return self._miss() or super().turn_off()


def _napping_home(tmp_path: Path, plug: _NappingPlug) -> tuple[TuyaHome, list[float]]:
    """Return a home whose clock only moves when it sleeps, so the retry deadline is exact."""
    now = [0.0]
    pauses: list[float] = []

    def sleep(seconds: float) -> None:
        pauses.append(seconds)
        now[0] += seconds

    home = TuyaHome(
        _write_wizard_files(tmp_path),
        device_factory=lambda entry, _ip, _version: _FakeBulb() if entry.kind == "light" else plug,
        clock=lambda: now[0],
        sleep=sleep,
    )
    return home, pauses


@pytest.mark.asyncio
async def test_a_plug_that_misses_a_few_connections_is_still_read_and_switched(tmp_path: Path) -> None:
    """2026-10-28: the lamp plugs missed connections for a few seconds; a short retry reaches them."""
    plug = _NappingPlug(misses=2)
    home, pauses = _napping_home(tmp_path, plug)
    _strip, lamp, _offline = await home.devices()
    assert (lamp.reachable, lamp.is_on) == (True, True)
    assert pauses == [0.5, 0.5]

    plug.tries, plug.misses = 0, 3
    await home.apply(lamp, LightChange(on=False))
    assert plug.calls == ["off"]


@pytest.mark.asyncio
async def test_a_plug_still_missing_at_the_deadline_is_unreachable(tmp_path: Path) -> None:
    """Retries stop about 6 s after the read began; the plug is then reported unreachable."""
    plug = _NappingPlug(misses=100)
    home, pauses = _napping_home(tmp_path, plug)
    _strip, lamp, _offline = await home.devices()
    assert not lamp.reachable
    assert sum(pauses) < 6.0 and plug.tries == len(pauses) + 1


@pytest.mark.asyncio
async def test_a_wrong_key_is_not_retried(tmp_path: Path) -> None:
    """Only network errors are worth waiting for; a key or version error fails at once."""
    plug = _NappingPlug(misses=100, error="914")
    home, pauses = _napping_home(tmp_path, plug)
    _strip, lamp, _offline = await home.devices()
    assert not lamp.reachable and pauses == [] and plug.tries == 1
