import json
import base64
import hashlib
from typing import Any

import httpx
import pytest

from reachy_mini_conversation_app.dirigera_hub import (
    DirigeraHub,
    DirigeraError,
    change_attributes,
    request_pairing_code,
    exchange_pairing_code,
)
from reachy_mini_conversation_app.home_devices import HubDevice, LightChange, normalize_name


def _light(device_id: str, name: str, room: str | None, **attributes: Any) -> dict[str, Any]:
    return {
        "id": device_id,
        "type": "light",
        "deviceType": "light",
        "isReachable": True,
        "room": {"id": "r", "name": room} if room else None,
        "attributes": {"customName": name, "isOn": False, **attributes},
        "capabilities": {"canReceive": ["isOn", "lightLevel", "colorTemperature"]},
    }


DEVICES = [
    _light("1", "Lámpara", "Salón", lightLevel=40, colorTemperatureMin=4000, colorTemperatureMax=2202),
    {
        "id": "2",
        "type": "outlet",
        "deviceType": "outlet",
        "isReachable": False,
        "attributes": {"customName": "Floor lamp", "isOn": True},
        "capabilities": {"canReceive": ["isOn"]},
    },
    {"id": "3", "type": "controller", "deviceType": "lightController", "attributes": {"customName": "Remote"}},
]


def _hub(handler: Any) -> DirigeraHub:
    return DirigeraHub("hub.local", "secret-token", transport=httpx.MockTransport(handler))


@pytest.mark.asyncio
async def test_devices_keep_lights_and_plugs_with_their_rooms_and_ranges() -> None:
    """Remotes are skipped; color-temperature bounds come back warmest first whatever order the hub uses."""
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json=DEVICES)

    hub = _hub(handler)
    lamp, plug = await hub.devices()
    await hub.close()

    assert seen[0].url == "https://hub.local:8443/v1/devices"
    assert seen[0].headers["Authorization"] == "Bearer secret-token"
    assert (lamp.name, lamp.room, lamp.brightness, lamp.kelvin_range) == ("Lámpara", "Salón", 40, (2202, 4000))
    assert (plug.kind, plug.room, plug.dims, plug.reachable, plug.is_on) == ("outlet", None, False, False, True)


@pytest.mark.asyncio
async def test_changes_and_scenes_use_the_hub_routes() -> None:
    """A change is a PATCH with an attribute list; a scene is a POST to its trigger route."""
    seen: list[tuple[str, str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content) if request.content else None
        seen.append((request.method, request.url.path, body))
        if request.url.path == "/v1/scenes":
            return httpx.Response(200, json=[{"id": "s1", "info": {"name": "Movie night"}}, {"id": "x"}])
        return httpx.Response(202)

    hub = _hub(handler)
    await hub.set_attributes("1", {"isOn": True, "lightLevel": 30})
    [scene] = await hub.scenes()
    await hub.trigger_scene(scene.id)
    await hub.close()

    assert seen == [
        ("PATCH", "/v1/devices/1", [{"attributes": {"isOn": True, "lightLevel": 30}}]),
        ("GET", "/v1/scenes", None),
        ("POST", "/v1/scenes/s1/trigger", None),
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize(("response", "message"), [(httpx.Response(401), "pair again"), (httpx.Response(500), "500")])
async def test_hub_errors_become_dirigera_errors(response: httpx.Response, message: str) -> None:
    """A rejected token or a hub error is reported without raising transport details."""
    hub = _hub(lambda _request: response)
    with pytest.raises(DirigeraError, match=message):
        await hub.devices()
    await hub.close()


@pytest.mark.asyncio
async def test_unreachable_hub_is_a_dirigera_error() -> None:
    """A network failure is reported as an unreachable hub."""

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("no route", request=request)

    hub = _hub(handler)
    with pytest.raises(DirigeraError, match="unreachable"):
        await hub.devices()
    await hub.close()


@pytest.mark.asyncio
async def test_pairing_sends_a_pkce_challenge_and_exchanges_the_code_for_a_token() -> None:
    """The authorize request carries the S256 challenge of the verifier later sent with the code."""
    seen: dict[str, httpx.Request] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen[request.url.path] = request
        if request.url.path == "/v1/oauth/authorize":
            return httpx.Response(200, json={"code": "abc"})
        return httpx.Response(200, json={"access_token": "new-token"})

    transport = httpx.MockTransport(handler)
    code, verifier = await request_pairing_code("hub.local", transport=transport)
    token = await exchange_pairing_code("hub.local", code, verifier, transport=transport)

    params = seen["/v1/oauth/authorize"].url.params
    expected = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    assert (params["audience"], params["code_challenge_method"], params["code_challenge"]) == (
        "homesmart.local",
        "S256",
        expected,
    )
    form = dict(httpx.QueryParams(seen["/v1/oauth/token"].content.decode()))
    assert (form["code"], form["code_verifier"], form["grant_type"]) == ("abc", verifier, "authorization_code")
    assert (code, token, len(verifier)) == ("abc", "new-token", 128)


def test_names_match_without_case_or_accents() -> None:
    """Spoken names match the app's names regardless of accents and spacing."""
    assert normalize_name("  Salón   Grande ") == "salon grande"


def test_changes_become_dirigera_attributes() -> None:
    """Brightness, warmth across the light's kelvin range, color and on/off map onto hub attributes."""
    lamp = HubDevice("1", "light", "Lamp", None, False, True, brightness=50, kelvin_range=(2200, 4000), colors=True)
    change = LightChange(on=True, brightness=30, warmth=0.5, color=(275.0, 1.0))
    assert change_attributes(lamp, change) == {
        "lightLevel": 30,
        "colorTemperature": 3100,
        "colorHue": 275.0,
        "colorSaturation": 1.0,
        "isOn": True,
    }
