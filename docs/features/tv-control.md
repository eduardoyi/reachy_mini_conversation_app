# Feature: TV control (Android TV)

- Status: `in-progress` (paired and checked live on the Mi TV P1; not yet tried in a conversation)
- Last updated: 2026-10-18

## Overview

Reachy controls the Xiaomi Mi TV P1 43" (Android TV, model `MiTV-MOOQ0`, named "TV" on the
network) through the Android TV Remote protocol v2, the protocol the Google TV phone app uses as
a remote. The `androidtvremote2` library (the one Home Assistant uses) does the protocol. Nothing
goes through Google's or Xiaomi's cloud. Anyone in the household may use it, kids included.

The network also has a Chromecast with Google TV named "Living Room TV"; it speaks the same
protocol and could be paired the same way.

## User-visible behavior

- "Turn on the TV", "turn it off", "volume down", "set the volume to 20", "mute", "pause".
- "Open Netflix", "put YouTube on": opens the app; Reachy cannot pick a show inside an app.
- "Put Bluey on YouTube": opens YouTube's search results for the query on the TV; the person
  picks with the remote or asks Reachy to move (down, right, select).
- "Is the TV on?", "what's on?": on/off, the open app and the volume.
- An app that does not open within 4 s is reported as probably not installed, not as done.

## Setup

```bash
.venv/bin/python -m reachy_mini_conversation_app.android_tv pair Android.local
```

The TV shows a six-character code; type it at the prompt. The client certificate and key go to
`~/.config/reachy-tv` (key mode 600) and `ANDROID_TV_HOST` to `.env`. The `tv` tool is added to
the profile's tools once both exist, unless the profile's tools were customized. Restart the app
after pairing.

## Implementation

- `android_tv.py`: `AndroidTV` connects on first use and keeps the connection (the library
  reconnects); it is recreated if the event loop changes. A command on a closed connection
  reconnects once. Volume is reported as a percentage of the TV's maximum and set by pressing
  volume keys (at most 60 per call). Discovery uses `dns-sd` for `_androidtvremote2._tcp`.
- `tools/tv.py`: actions status, power, volume, mute, media, navigate, open, youtube. Opening an
  app or a search on a TV in standby turns it on first. Errors tell Gemini not to retry.

## Checked live — 2026-10-18

- The TV stays reachable in standby; state arrives about a second after connecting.
- WAKEUP and SLEEP do nothing on this TV. POWER toggles it in about 3 s, so `power()` sends POWER
  only when the TV reports the other state, and waits up to 6 s for the change. With no reported
  state it falls back to WAKEUP/SLEEP, which cannot toggle the wrong way.
- 2026-10-18 20:56: after a long standby the TV took more than 6 s to report it was on, so
  Reachy said turning it on had failed while the TV was coming on. An unconfirmed change is
  now reported as "turning on/off" (the quick path stays silent, as it does on success), and the
  tool says not to press power again. The change keeps being watched for up to 30 s; the log
  says "TV reported on N s after the power key" (or that it never did), to tune the wait.
- `market://launch?id=<package>` (the library's default for package names) is refused for every
  app, YouTube included. Web links open the apps: `https://www.youtube.com`,
  `https://www.netflix.com/browse`, `https://app.primevideo.com`, and YouTube searches
  (`https://www.youtube.com/results?search_query=…`). Disney+, Spotify, Max, Apple TV, Twitch and
  YouTube Kids did not open from their links; they are probably not installed. `open_app` tries
  the web link, then the package link, and confirms by the reported current app.
- Volume 13 → 10 → 13 by key presses matched the reported level.

## Tests

`tests/test_android_tv.py` (fake remote: power toggling and confirmation, volume steps, mute,
app confirmation and link order, reconnect, connection errors) and `tests/tools/test_tv.py`.
