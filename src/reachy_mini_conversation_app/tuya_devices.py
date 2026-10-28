"""Control Tuya (Smart Life) lights and plugs over the local network with TinyTuya.

The device list and each device's local key come from TinyTuya's wizard, run once in
``~/.config/reachy-tuya`` (``uvx --from tinytuya python -m tinytuya wizard``). That folder sits
outside the repository because the keys control the devices. Device addresses change with DHCP,
so they come from the wizard's ``snapshot.json`` and are kept current by listening to the
broadcasts every Tuya device sends on UDP 6666/6667/7000. No Tuya cloud call happens at run time.
"""

from __future__ import annotations
import json
import time
import select
import socket
import asyncio
import logging
import colorsys
import threading
from typing import Any, Final, Callable
from pathlib import Path
from dataclasses import replace, dataclass

from reachy_mini_conversation_app.home_devices import HubScene, HubDevice, LightChange, HomeDeviceError


logger = logging.getLogger(__name__)

DEFAULT_DEVICES_FILE: Final[Path] = Path.home() / ".config" / "reachy-tuya" / "devices.json"
# Tuya product categories: dj = light, dd = light strip, dc = string lights, xdd = ceiling light;
# cz = socket, pc = power strip, kg = switch.
LIGHT_CATEGORIES: Final[frozenset[str]] = frozenset({"dj", "dd", "dc", "xdd"})
PLUG_CATEGORIES: Final[frozenset[str]] = frozenset({"cz", "pc", "kg"})
CONNECT_TIMEOUT_S: Final[float] = 2.0
BROADCAST_PORTS: Final[tuple[int, ...]] = (6666, 6667, 7000)
# Wi-Fi plugs saving power miss connections for 5-10 s at a time while still broadcasting (2026-10-28: the
# living and dining lamps were skipped by "lights"). Each step is retried on a network error until this long
# after the read or change began, pausing between tries; other errors (a wrong key) fail at once.
REACH_DEADLINE_S: Final[float] = 6.0
RETRY_PAUSE_S: Final[float] = 0.5
NETWORK_ERRORS: Final[frozenset[str]] = frozenset({"901", "905"})  # TinyTuya: cannot connect, unreachable
# Reading three devices before a change took about 2.8 s of a 4.4 s "lights" (2026-10-28), so a background
# thread reads them every POLL_EVERY_S and a change uses a reading up to STATE_FRESH_S old. A failed read
# keeps the last good one; status may lag a change made elsewhere by up to the poll period.
POLL_EVERY_S: Final[float] = 30.0
STATE_FRESH_S: Final[float] = 60.0
# Nominal white range of Tuya CCT lights; they only take a 0-100 % position.
TUYA_KELVIN_RANGE: Final[tuple[int, int]] = (2700, 6500)


@dataclass(frozen=True)
class TuyaEntry:
    """One device from the wizard's list."""

    id: str
    name: str
    key: str
    kind: str  # "light" or "outlet"


def load_entries(devices_file: Path) -> list[TuyaEntry]:
    """Read the wizard's device list, keeping lights and plugs that have a local key."""
    try:
        raw = json.loads(devices_file.read_text())
    except (OSError, ValueError) as error:
        logger.warning("Tuya device list unreadable: %s", type(error).__name__)
        return []
    entries = []
    for item in raw if isinstance(raw, list) else []:
        category = item.get("category")
        kind = "light" if category in LIGHT_CATEGORIES else "outlet" if category in PLUG_CATEGORIES else None
        if kind and item.get("key") and item.get("id") and not item.get("sub"):
            entries.append(
                TuyaEntry(id=str(item["id"]), name=str(item.get("name") or item["id"]), key=item["key"], kind=kind)
            )
    return entries


def load_addresses(devices_file: Path) -> dict[str, tuple[str, str]]:
    """Read device addresses and protocol versions from the wizard's ``snapshot.json``, if present."""
    try:
        snapshot = json.loads((devices_file.parent / "snapshot.json").read_text())
    except (OSError, ValueError):
        return {}
    addresses = {}
    for item in snapshot.get("devices", []) if isinstance(snapshot, dict) else []:
        if item.get("id") and item.get("ip"):
            addresses[str(item["id"])] = (str(item["ip"]), str(item.get("ver") or "3.3"))
    return addresses


def _percent(raw: Any, low: Any, high: Any) -> int:
    try:
        return max(1, min(100, round((float(raw) - float(low)) / (float(high) - float(low)) * 100)))
    except (TypeError, ValueError, ZeroDivisionError):
        return 100


class TuyaHome:
    """Tuya lights and plugs from the wizard's list."""

    def __init__(
        self,
        devices_file: Path = DEFAULT_DEVICES_FILE,
        *,
        device_factory: Callable[[TuyaEntry, str, str], Any] | None = None,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        """Load the wizard's files; ``device_factory``, ``clock`` and ``sleep`` replace TinyTuya and time in tests."""
        self._entries = {entry.id: entry for entry in load_entries(devices_file)}
        self._addresses = load_addresses(devices_file)
        self._lock = threading.Lock()
        self._device_factory = device_factory or _tinytuya_device
        self._clock = clock
        self._sleep = sleep
        self._stop = threading.Event()
        self._listener: threading.Thread | None = None
        self._poller: threading.Thread | None = None
        # Last good reading per device id, with the clock time it was taken.
        self._states: dict[str, tuple[float, HubDevice]] = {}

    @property
    def configured(self) -> bool:
        """Whether any light or plug has a local key."""
        return bool(self._entries)

    def start_listening(self) -> None:
        """Follow address changes from device broadcasts, and keep readings fresh, in background threads."""
        if self._listener is None:
            self._listener = threading.Thread(target=self._listen, name="tuya-broadcasts", daemon=True)
            self._listener.start()
        if self._poller is None:
            self._poller = threading.Thread(target=self._poll, name="tuya-readings", daemon=True)
            self._poller.start()

    async def close(self) -> None:
        """Stop the broadcast listener."""
        self._stop.set()

    async def devices(self) -> list[HubDevice]:
        """Every device's state: a fresh reading when there is one, else read now (in parallel)."""
        return list(
            await asyncio.gather(*(asyncio.to_thread(self._fresh_or_read, entry) for entry in self._entries.values()))
        )

    async def scenes(self) -> list[HubScene]:
        """Tuya scenes live in the cloud; none are offered locally."""
        return []

    async def trigger_scene(self, scene_id: str) -> None:
        """Not supported locally."""
        raise HomeDeviceError("Tuya scenes are not available")

    async def apply(self, device: HubDevice, change: LightChange) -> None:
        """Change one device, and remember whether it is now on."""
        entry = self._entries[device.id]
        await asyncio.to_thread(self._apply, entry, change, device.color_only)
        self._remember(replace(device, is_on=change.on is not False, reachable=True))

    def _remember(self, device: HubDevice) -> None:
        with self._lock:
            self._states[device.id] = (self._clock(), device)

    def _fresh_or_read(self, entry: TuyaEntry) -> HubDevice:
        with self._lock:
            cached = self._states.get(entry.id)
        if cached is not None and self._clock() - cached[0] < STATE_FRESH_S:
            return cached[1]
        return self._read(entry)

    def _poll(self) -> None:
        while not self._stop.wait(POLL_EVERY_S):
            for entry in list(self._entries.values()):
                self._read(entry)

    def _address(self, device_id: str) -> tuple[str, str] | None:
        with self._lock:
            return self._addresses.get(device_id)

    def _patiently(self, call: Callable[[], Any], deadline: float) -> Any:
        """Run one TinyTuya call, again after a short pause while it reports a network error and time remains."""
        while True:
            result = call()
            unreachable = isinstance(result, dict) and str(result.get("Err")) in NETWORK_ERRORS
            if not unreachable or self._clock() + RETRY_PAUSE_S >= deadline:
                return result
            self._sleep(RETRY_PAUSE_S)

    def _connect(self, entry: TuyaEntry) -> Any | None:
        address = self._address(entry.id)
        if address is None:
            return None
        return self._device_factory(entry, *address)

    def _read(self, entry: TuyaEntry) -> HubDevice:
        """Read one device now; a good reading is remembered, a failed one keeps the last good reading."""
        device = self._read_now(entry)
        if device.reachable:
            self._remember(device)
        return device

    def _read_now(self, entry: TuyaEntry) -> HubDevice:
        unreachable = HubDevice(
            id=entry.id,
            kind=entry.kind,
            name=entry.name,
            room=None,
            is_on=False,
            reachable=False,
            brightness=50 if entry.kind == "light" else None,
            colors=entry.kind == "light",
            source="tuya",
        )
        device = self._connect(entry)
        if device is None:
            return unreachable
        deadline = self._clock() + REACH_DEADLINE_S
        try:
            if entry.kind == "outlet":
                dps = (self._patiently(device.status, deadline) or {}).get("dps")
                if not isinstance(dps, dict):
                    return unreachable
                return HubDevice(
                    id=entry.id,
                    kind="outlet",
                    name=entry.name,
                    room=None,
                    is_on=bool(dps.get("1")),
                    reachable=True,
                    source="tuya",
                )
            state = self._patiently(device.state, deadline)
            if not isinstance(state, dict) or "Error" in state:
                return unreachable
            dpset = getattr(device, "dpset", {}) or {}
            if state.get("mode") == "colour":
                _hue, _saturation, value = device.colour_hsv(state=state)
                brightness = max(1, round(float(value) * 100))
            else:
                brightness = _percent(state.get("brightness"), dpset.get("value_min"), dpset.get("value_max"))
            return HubDevice(
                id=entry.id,
                kind="light",
                name=entry.name,
                room=None,
                is_on=bool(state.get("is_on")),
                reachable=True,
                brightness=brightness,
                kelvin_range=TUYA_KELVIN_RANGE if state.get("colourtemp") is not None else None,
                colors=state.get("colour") is not None,
                source="tuya",
            )
        except Exception as error:  # TinyTuya raises a range of socket and decode errors
            logger.debug("Tuya read failed for %s: %s", entry.name, type(error).__name__)
            return unreachable

    def _apply(self, entry: TuyaEntry, change: LightChange, color_only: bool = False) -> None:
        device = self._connect(entry)
        if device is None:
            raise HomeDeviceError(f"{entry.name} is not on the network")
        deadline = self._clock() + REACH_DEADLINE_S
        results = []
        if change.on is False:
            results.append(self._patiently(device.turn_off, deadline))
        else:
            if change.on:
                plain_on = change.brightness is None and change.warmth is None and change.color is None
                if entry.kind == "light" and plain_on and _reports_on(device):
                    # A strip switched off with its own remote can keep reporting "on" while dark, so a plain
                    # "on" changes nothing; switching it off first makes "on" light it (2026-10-19, TV Lights).
                    results.append(device.turn_off())
                results.append(self._patiently(device.turn_on, deadline))
            if entry.kind == "light":
                in_colour = change.warmth is None and device.get_mode() == "colour"
                if color_only and not in_colour and change.color is None:
                    # An RGB strip left in white mode (by a warmth or its remote) reports on but stays dark
                    # (2026-10-25, TV Lights); its color mode brings back the last color.
                    results.append(device.set_mode("colour"))
                    in_colour = True
                if change.color is not None or (change.brightness is not None and in_colour):
                    current_hue, current_saturation, current_value = (
                        device.colour_hsv() if in_colour else (0.0, 0.0, 1.0)
                    )
                    hue, saturation = change.color or (current_hue * 360, current_saturation)
                    value = change.brightness / 100 if change.brightness is not None else current_value
                    results.append(_set_colour(device, hue, saturation, value))
                elif change.warmth is not None:
                    brightness = change.brightness if change.brightness is not None else 100
                    results.append(device.set_white_percentage(brightness, round(change.warmth * 100)))
                elif change.brightness is not None:
                    results.append(device.set_brightness_percentage(change.brightness))
        errors = [result for result in results if isinstance(result, dict) and "Error" in result]
        if errors:
            raise HomeDeviceError(f"{entry.name}: {errors[0].get('Error')}")

    def _listen(self) -> None:
        import tinytuya

        sockets = []
        for port in BROADCAST_PORTS:
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            if hasattr(socket, "SO_REUSEPORT"):
                sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
            try:
                sock.bind(("", port))
            except OSError as error:
                logger.warning("Tuya broadcasts on %d unavailable: %s", port, type(error).__name__)
                sock.close()
                continue
            sockets.append(sock)
        try:
            while not self._stop.is_set() and sockets:
                readable, _, _ = select.select(sockets, [], [], 1.0)
                for sock in readable:
                    data, (ip, _port) = sock.recvfrom(4096)
                    try:
                        message = json.loads(tinytuya.decrypt_udp(data))
                    except Exception:
                        continue
                    device_id = message.get("gwId")
                    if device_id in self._entries:
                        with self._lock:
                            if self._addresses.get(device_id, ("", ""))[0] != ip:
                                logger.info("Tuya device %s is at a new address", self._entries[device_id].name)
                            self._addresses[device_id] = (ip, str(message.get("version") or "3.3"))
        finally:
            for sock in sockets:
                sock.close()


def _reports_on(device: Any) -> bool:
    """Whether a bulb says it is on; unknown counts as off."""
    try:
        state = device.state()
    except Exception:  # TinyTuya raises a range of socket and decode errors
        return False
    return isinstance(state, dict) and "Error" not in state and bool(state.get("is_on"))


def _set_colour(device: Any, hue: float, saturation: float, value: float) -> Any:
    """Set a color as integer RGB.

    TinyTuya 1.20's ``set_hsv`` (also used by ``set_brightness_percentage`` in color mode) passes
    floats to ``%02x`` for old-format bulbs and raises ``TypeError`` on Python 3.12, so colors and
    color-mode brightness go through ``set_colour`` with integers instead.
    """
    red, green, blue = (round(c * 255) for c in colorsys.hsv_to_rgb(hue / 360, saturation, max(0.01, value)))
    return device.set_colour(red, green, blue)


def _tinytuya_device(entry: TuyaEntry, ip: str, version: str) -> Any:
    import tinytuya

    cls = tinytuya.BulbDevice if entry.kind == "light" else tinytuya.OutletDevice
    device = cls(
        entry.id,
        ip,
        entry.key,
        version=float(version),
        connection_timeout=CONNECT_TIMEOUT_S,
        connection_retry_limit=1,
        connection_retry_delay=0,
    )
    device.set_socketRetryLimit(1)
    return device
