"""Control lights and smart plugs on an IKEA DIRIGERA hub over its local HTTPS API.

The hub serves ``https://<host>:8443/v1`` with a self-signed certificate and Bearer-token auth.
Pairing is an OAuth PKCE exchange confirmed by pressing the hub's action button. The routes and
fields follow the unofficial ``dirigera`` client (github.com/Leggin/dirigera); a hub firmware
update could change them. Nothing goes through IKEA's cloud.

Pair once with ``python -m reachy_mini_conversation_app.dirigera_hub pair [host]``: it stores
``DIRIGERA_HOST`` and ``DIRIGERA_TOKEN`` in ``.env`` without printing the token.
"""

from __future__ import annotations
import sys
import base64
import socket
import asyncio
import hashlib
import logging
import secrets
import argparse
from typing import Any, Final
from pathlib import Path

import httpx

from reachy_mini_conversation_app.home_devices import HubScene, HubDevice, LightChange, HomeDeviceError


logger = logging.getLogger(__name__)

PORT: Final[int] = 8443
REQUEST_TIMEOUT_S: Final[float] = 5.0
_VERIFIER_ALPHABET: Final[str] = "_-~." + "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"
_VERIFIER_LENGTH: Final[int] = 128
# Device types Reachy controls; others (remotes, sensors, speakers, blinds) are left alone.
CONTROLLED_TYPES: Final[frozenset[str]] = frozenset({"light", "outlet"})


class DirigeraError(HomeDeviceError):
    """The hub could not be reached or rejected the request."""


def _parse_device(raw: dict[str, Any]) -> HubDevice | None:
    kind = raw.get("type") or raw.get("deviceType")
    if kind not in CONTROLLED_TYPES or raw.get("isHidden"):
        return None
    attributes = raw.get("attributes") or {}
    can_receive = set((raw.get("capabilities") or {}).get("canReceive") or [])
    if "isOn" not in can_receive:
        return None
    kelvin_range = None
    if "colorTemperature" in can_receive:
        bounds = [attributes.get("colorTemperatureMin"), attributes.get("colorTemperatureMax")]
        numbers = [int(value) for value in bounds if isinstance(value, int | float)]
        if len(numbers) == 2:
            low, high = sorted(numbers)  # the hub reports them in either order
            kelvin_range = (low, high)
    level = attributes.get("lightLevel") if "lightLevel" in can_receive else None
    room = raw.get("room") or {}
    return HubDevice(
        id=str(raw["id"]),
        kind=str(kind),
        name=str(attributes.get("customName") or attributes.get("model") or raw["id"]),
        room=str(room["name"]) if isinstance(room, dict) and room.get("name") else None,
        is_on=bool(attributes.get("isOn")),
        reachable=bool(raw.get("isReachable", True)),
        brightness=int(level) if isinstance(level, int | float) else None,
        kelvin_range=kelvin_range,
        colors="colorHue" in can_receive,
    )


def change_attributes(device: HubDevice, change: LightChange) -> dict[str, Any]:
    """Translate a change into DIRIGERA device attributes."""
    attributes: dict[str, Any] = {}
    if change.brightness is not None:
        attributes["lightLevel"] = max(1, min(100, change.brightness))
    if change.warmth is not None and device.kelvin_range is not None:
        low, high = device.kelvin_range
        attributes["colorTemperature"] = round(low + (high - low) * change.warmth)
    if change.color is not None:
        attributes["colorHue"], attributes["colorSaturation"] = change.color
    if change.on is not None:
        attributes["isOn"] = change.on
    return attributes


class DirigeraHub:
    """Async client for one paired hub."""

    def __init__(self, host: str, token: str, *, transport: httpx.AsyncBaseTransport | None = None) -> None:
        """Talk to ``host`` (name or address) with the paired ``token``; ``transport`` replaces the network in tests."""
        self._client = httpx.AsyncClient(
            base_url=f"https://{host}:{PORT}/v1",
            headers={"Authorization": f"Bearer {token}"},
            verify=False,  # the hub's certificate is self-signed
            timeout=REQUEST_TIMEOUT_S,
            transport=transport,
        )

    async def close(self) -> None:
        """Close the connection pool."""
        await self._client.aclose()

    async def devices(self) -> list[HubDevice]:
        """Return the lights and plugs Reachy can switch."""
        raw = await self._request("GET", "/devices")
        if not isinstance(raw, list):
            raise DirigeraError("unexpected device list")
        return [device for device in (_parse_device(item) for item in raw if isinstance(item, dict)) if device]

    async def scenes(self) -> list[HubScene]:
        """Return the scenes saved in the IKEA app."""
        raw = await self._request("GET", "/scenes")
        if not isinstance(raw, list):
            raise DirigeraError("unexpected scene list")
        scenes = []
        for item in raw:
            name = ((item.get("info") or {}).get("name") if isinstance(item, dict) else None) or ""
            if name and item.get("id"):
                scenes.append(HubScene(id=str(item["id"]), name=str(name)))
        return scenes

    async def set_attributes(self, device_id: str, attributes: dict[str, Any]) -> None:
        """Change one device, e.g. ``{"isOn": True}`` or ``{"lightLevel": 40}``."""
        await self._request("PATCH", f"/devices/{device_id}", json=[{"attributes": attributes}])

    async def apply(self, device: HubDevice, change: LightChange) -> None:
        """Apply a provider-neutral change to one device."""
        await self.set_attributes(device.id, change_attributes(device, change))

    async def trigger_scene(self, scene_id: str) -> None:
        """Run a saved scene."""
        await self._request("POST", f"/scenes/{scene_id}/trigger")

    async def _request(self, method: str, path: str, *, json: Any = None) -> Any:
        try:
            response = await self._client.request(method, path, json=json)
        except httpx.HTTPError as error:
            raise DirigeraError(f"hub unreachable ({type(error).__name__})") from error
        if response.status_code == 401:
            raise DirigeraError("the hub rejected the token; pair again")
        if response.status_code >= 400:
            raise DirigeraError(f"hub returned HTTP {response.status_code}")
        if not response.content:
            return None
        try:
            return response.json()
        except ValueError:
            return None


def _code_verifier() -> str:
    return "".join(secrets.choice(_VERIFIER_ALPHABET) for _ in range(_VERIFIER_LENGTH))


def _code_challenge(verifier: str) -> str:
    digest = hashlib.sha256(verifier.encode()).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode()


async def request_pairing_code(host: str, *, transport: httpx.AsyncBaseTransport | None = None) -> tuple[str, str]:
    """Start pairing; return (authorization code, verifier). The hub's button must be pressed next."""
    verifier = _code_verifier()
    async with httpx.AsyncClient(verify=False, timeout=REQUEST_TIMEOUT_S, transport=transport) as client:
        response = await client.get(
            f"https://{host}:{PORT}/v1/oauth/authorize",
            params={
                "audience": "homesmart.local",
                "response_type": "code",
                "code_challenge": _code_challenge(verifier),
                "code_challenge_method": "S256",
            },
        )
    if response.status_code >= 400:
        raise DirigeraError(f"pairing refused (HTTP {response.status_code})")
    return str(response.json()["code"]), verifier


async def exchange_pairing_code(
    host: str, code: str, verifier: str, *, transport: httpx.AsyncBaseTransport | None = None
) -> str:
    """Finish pairing after the button press and return the access token."""
    async with httpx.AsyncClient(verify=False, timeout=REQUEST_TIMEOUT_S, transport=transport) as client:
        response = await client.post(
            f"https://{host}:{PORT}/v1/oauth/token",
            data={
                "code": code,
                "name": socket.gethostname(),
                "grant_type": "authorization_code",
                "code_verifier": verifier,
            },
        )
    if response.status_code >= 400:
        raise DirigeraError(f"token refused (HTTP {response.status_code}); was the button pressed?")
    return str(response.json()["access_token"])


def _pair_command(host: str, env_path: Path) -> int:
    from dotenv import set_key

    code, verifier = asyncio.run(request_pairing_code(host))
    input("Press the action button on the back of the DIRIGERA hub, then press Enter here... ")
    token = asyncio.run(exchange_pairing_code(host, code, verifier))
    env_path.touch(mode=0o600, exist_ok=True)
    set_key(str(env_path), "DIRIGERA_HOST", host)
    set_key(str(env_path), "DIRIGERA_TOKEN", token)
    devices = asyncio.run(_count_devices(host, token))
    print(f"Paired with {host}; saved DIRIGERA_HOST and DIRIGERA_TOKEN to {env_path}. {devices} lights/plugs found.")
    return 0


async def _count_devices(host: str, token: str) -> int:
    hub = DirigeraHub(host, token)
    try:
        return len(await hub.devices())
    finally:
        await hub.close()


def main(argv: list[str] | None = None) -> int:
    """Pair with a hub: ``python -m reachy_mini_conversation_app.dirigera_hub pair [host]``."""
    parser = argparse.ArgumentParser(description="Pair Reachy with an IKEA DIRIGERA hub.")
    sub = parser.add_subparsers(dest="command", required=True)
    pair = sub.add_parser("pair", help="pair once and save the token to .env")
    pair.add_argument("host", nargs="?", default=None, help="hub hostname or IP (default: find it on the network)")
    pair.add_argument("--env", default=".env", help="the .env file to update (default: ./.env)")
    args = parser.parse_args(argv)
    host = args.host or discover_host()
    if not host:
        print("No DIRIGERA found on the network; pass its address, e.g. 192.168.1.69", file=sys.stderr)
        return 1
    try:
        return _pair_command(host, Path(args.env))
    except (DirigeraError, httpx.HTTPError, KeyError) as error:
        print(f"Pairing failed: {error}", file=sys.stderr)
        return 1


def discover_host(timeout_s: float = 4.0) -> str | None:
    """Find a hub through Bonjour (macOS ``dns-sd``); return its ``.local`` hostname, or None."""
    import re
    import subprocess

    try:
        browse = subprocess.run(
            ["perl", "-e", f"alarm {int(timeout_s)}; exec @ARGV", "dns-sd", "-B", "_ihsp._tcp", "local."],
            capture_output=True,
            text=True,
            timeout=timeout_s + 2,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    match = re.search(r"_ihsp\._tcp\.\s+(\S+)\s*$", browse.stdout, re.MULTILINE)
    return f"{match.group(1)}.local" if match else None


if __name__ == "__main__":
    raise SystemExit(main())
