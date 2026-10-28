# Feature: home lights (IKEA DIRIGERA and Tuya)

- Status: `in-progress` (built and unit-tested; live pairing and control pending)
- Last updated: 2026-10-28

## Overview

Reachy switches, dims, warms and colors the household's lights and smart plugs,
and runs saved scenes. It talks to two systems on the local network: the IKEA
DIRIGERA hub (its HTTPS API), and Tuya / Smart Life devices (TinyTuya's local
protocol). Nothing goes through IKEA's or Tuya's cloud at run time. Anyone in the
household may use it, kids included.

There is no Home Assistant in the house yet. Almost every light is on the DIRIGERA,
so talking to the hub directly avoided a new always-on server. The TV LED strip
(a GHome Smart, Tuya-based strip) and the floor-lamp plugs are Tuya devices,
moved into the Smart Life app so TinyTuya's wizard could fetch their local keys.
The Amazon Smart Plug is Alexa-only and was replaced by a Tuya plug. If non-IKEA devices are added later, Home Assistant (with the hub
added through its Matter integration, and Reachy using HA's MCP Server
integration) is the path that scales.

## User-visible behavior

- "Turn off the living room", "dim the floor lamp to 30", "make it warmer",
  "apaga todo", "movie night": the `home_lights` tool takes a room, light or plug
  name, or "all".
- Brightness or warmth on its own also turns the light on. Plugs only switch on
  and off.
- "What's on?" asks the hub for current states.
- Names come from the IKEA app. They match without case or accents. A name
  that matches nothing returns the available rooms and names so Gemini can retry.

## Architecture

- `home_devices.py` holds the shared pieces. `HubDevice` records what a device
  supports (`dims`, `warmth`, `colors`) and its `source`. `LightChange` is a
  provider-neutral change, and `for_device` keeps only what a device supports;
  a brightness, warmth or color alone implies on. `HomeDevices` merges the
  providers and skips one that fails unless all fail.
- `tuya_devices.py`: `TuyaHome` reads TinyTuya's wizard files from
  `~/.config/reachy-tuya`, which is outside the repository because the local keys
  control the devices.
  - It keeps lights (`dj`, `dd`, `dc`, `xdd`) and plugs (`cz`, `pc`, `kg`) that
    have a key, but not IR sub-devices.
  - Addresses come from `snapshot.json`. A background thread then follows Tuya
    UDP broadcasts on ports 6666, 6667 and 7000 (`tinytuya.decrypt_udp`), so a
    DHCP change is picked up.
  - It drives `BulbDevice` and `OutletDevice` with a 2 s connect timeout. Reads and
    on/off steps that get a network error (TinyTuya `901`/`905`) are tried again
    every 0.5 s until 6 s after they began (`REACH_DEADLINE_S`); other errors, such
    as a wrong key (`914`), fail at once:
    - color: `set_hsv`, keeping the current color brightness;
    - warmth: `set_white_percentage`;
    - brightness: `set_brightness_percentage`, which handles color mode.
  - Old-format (type A, data points 1–10) and new-format (type B, data points
    20–24) bulbs are both handled by TinyTuya.

- `dirigera_hub.py`: `DirigeraHub` uses `httpx` against `https://<host>:8443/v1`.
  Bearer token, `verify=False` because the hub's certificate is self-signed,
  5 s timeout.
  - `GET /devices` keeps lights and outlets that accept `isOn` (`HubDevice`).
  - `PATCH /devices/{id}` with `[{"attributes": {...}}]`.
  - `GET /scenes` and `POST /scenes/{id}/trigger`.
  - Routes and fields follow the unofficial `dirigera` client
    (github.com/Leggin/dirigera). A small local client was preferred over adding
    that dependency (requests, pydantic, websocket-client).
- Pairing: an OAuth PKCE exchange. `GET /oauth/authorize` with an S256
  challenge, the hub's action button pressed, then `POST /oauth/token` with
  the verifier.
- `HomeLayout` (`HOME_LAYOUT_FILE`, default `~/.config/reachy-home/layout.json`)
  adds what the systems do not store. It gives a room to devices without one
  (Tuya keeps rooms in its cloud) and defines areas within a room. The household
  has one IKEA "Living room" with two areas, Living and Dining, and the Tuya strip
  and plugs are placed in it.
- `"color_only"` in the layout lists RGB lights whose white mode leaves them dark.
  The household's TV strip (Tuya `dj`) reports a color temperature, so it was
  offered warmth. A warmth or "white" switched it to white mode, where it said
  "on" but showed nothing (2026-10-25, after "movie mode"). A color-only light has
  no warmth: white and warmth become a near-white color (`white_as_color`, a pale
  amber for warm through plain white for cool). Switching it on also puts it back
  in color mode if something left it in white mode, such as its own remote.
- Targets match in this order:
  1. everything: `all`, or plain "the lights" in any of the four languages;
  2. a device name, so "Living lamp" is the lamp and not the Living area
     (until 2026-10-19 areas came first and it switched the whole area);
  3. a room;
  4. an area, where filler words like "the" and "lights" are ignored, so "the
     dining lights" matches Dining;
  5. a partial name;
  6. a device whose every name word was said among others, so "living room
     lamp" and "la lámpara de la sala" find "Living lamp". Quick commands
     (`quick_commands.resolve_light_target`) use the same order.
- So "living room" switches both areas, and "the dining" switches one.
- `tools/home_lights.py`: the actions are `status`, `set` and `scene`. Colors are
  names (red to pink) or `#rrggbb`; "white" means neutral white light. An offline
  device that shares a name with an online one, such as an old bulb left in an
  app, is ignored. `set`
  patches the matched devices concurrently, skips unreachable ones and reports
  them, with an instruction to tell the person which lights did not respond, even
  when others changed. Warmth maps onto each light's kelvin range: warm is the warmest end,
  neutral the middle, cool the coolest. The hub reports `colorTemperatureMin`
  and `colorTemperatureMax` in either order, so the range is sorted.
- Wiring: `main._build_home_devices` puts a `HomeDevices` on
  `ToolDependencies.home_hub`. It includes the `DirigeraHub` when
  `DIRIGERA_HOST` and `DIRIGERA_TOKEN` are set, and `TuyaHome` when
  `TUYA_DEVICES_FILE` (default `~/.config/reachy-tuya/devices.json`) lists a
  device. `home_lights` (`LIGHT_TOOLS`)
  then loads unless the profile's tools were customized.

## Setup

```bash
.venv/bin/python -m reachy_mini_conversation_app.dirigera_hub pair
```

The command finds the hub with Bonjour (`_ihsp._tcp`) or takes a hostname or IP.
It asks you to press the action button, then saves `DIRIGERA_HOST` and
`DIRIGERA_TOKEN` to `.env`, creating the file with 0600 permissions, without
printing the token. On 2026-10-17 the household hub was found as
`gw2-a99f629a8510.local` (192.168.1.69), DIRIGERA firmware 2.975.3. Restart the
app afterwards.

For Tuya devices, put them in the Smart Life app, link that account to a Tuya IoT
cloud project (Central Europe data center, IoT Core and Authorization services),
then run once:

```bash
mkdir -p ~/.config/reachy-tuya && cd ~/.config/reachy-tuya && uvx --from tinytuya python -m tinytuya wizard
```

Rerun the wizard (`-yes` reuses the saved project credentials) whenever a device
is added or reset, because resetting changes its key. IoT Core is a one-month free
trial that is only needed while running the wizard. On 2026-10-17 it found:
- TV Lights, an old-format RGB strip, at 192.168.1.81;
- Living lamp and Dining lamp, plugs at .178 and .171;
- offline old bulbs, an IR hub, and IR remotes, which are ignored.

## Constraints and failure modes

- The API is unofficial; a hub firmware update can change it. Errors come back to
  Gemini as `{"error": ...}`. A 401 means the token was revoked, so pair again.
- The token grants full control of the hub. It lives only in `.env` and is never
  logged. Tuya local keys stay in `~/.config/reachy-tuya` and are never logged.
- Tuya scenes live in the cloud, so none are offered. A Tuya device that is
  switched off at the wall reports as unreachable.
- The Living and Dining lamp plugs drop off the network for 5-10 s at a time while
  still broadcasting (2026-10-28 on the Mac mini: no ping reply, connections timing
  out, then fine seconds later). With one 2 s try, "lights" skipped both and
  Reachy said it was done. The 6 s retry covers those gaps; a plug that is really
  off now makes a lights command about 6 s slower before it is reported.
- Reading the Tuya devices before each change took about 2.8 s of a 4.4 s "lights"
  (2026-10-28). A background thread (`tuya-readings`) now reads them every 30 s and a
  change uses a reading up to 60 s old; a failed read keeps the last good one, and a
  change updates the remembered on/off. Status can lag a change made in the Smart
  Life app or at the plug by up to about 30 s. A Tuya light switched off with
  its own remote can keep reporting "on" while dark (the TV strip, 2026-10-19), so
  a plain "on" for a light that already reports on switches it off and on again.
- New dependency: `tinytuya` (MIT; pulls in `cryptography`, `requests` and
  `colorama`). Locking it needed pkg-config shims for zlib, bzip2, expat and
  libffi, because uv rebuilds the `pygobject` and `pycairo` source distributions
  for metadata.
- Blinds, sensors, remotes and speakers on the hub are ignored.
- Each `set` logs the kind of change (`change=on,brightness`), `on`, and the device names it
  matched, changed, failed and found unreachable, or that it matched nothing. The target as said
  is never logged. On 2026-10-28 the Living lamp stayed on after a spoken "off" and the log could
  not show what the call had targeted; the plug itself switched off at once when sent the command.

## Tests

- `tests/test_dirigera_hub.py`: device parsing, routes, errors, PKCE pairing.
- `tests/tools/test_home_lights.py`: room, partial and accent-free matching;
  brightness, warmth and colors; plugs; offline duplicates; unreachable lights;
  status; scenes; errors; the tool loading only once something is set up; the
  log naming matched and changed devices but never the spoken target.
- `tests/test_tuya_devices.py`: wizard-file filtering, state reading, bulb and
  plug commands, relighting a strip that wrongly reports on, retrying a plug that
  misses connections (and stopping at the deadline or on a key error), reusing fresh
  readings and keeping the last good one when a reading fails,
  `LightChange.for_device`, and merging with one system down.
