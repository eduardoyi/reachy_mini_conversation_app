# Feature: music on a Bluetooth speaker

- Status: `in-progress` (built and unit-tested; pairing and live playback pending)
- Last updated: 2026-10-18

## Why

The Living Room Speaker is a Google Home Mini. As a Google Cast device it appears on Spotify's
device list only after a Spotify app has woken it and drops off when idle, so Reachy (which uses
Spotify's Web API) could not start music there. pychromecast 14 removed its Spotify controller, and
the community workaround (spotcast) needs the Spotify web-player cookie, so neither was used.

Instead the Home Mini is paired with Reachy's Mac as a Bluetooth speaker, and Reachy plays on the
Mac's own Spotify app, which is always on the device list. Pause, skip and volume are instant.

## Behavior

Before `spotify_control` plays or resumes (from a quick command or from Gemini), Reachy:
1. opens the Mac's Spotify app in the background if it is not running;
2. connects the speaker over Bluetooth if it is not connected (up to 8 s);
3. sets the Mac's sound output to the speaker.

Reachy's own voice always goes to the "Reachy Mini Audio" card by name, whatever the Mac's output.
Other Mac sounds follow the Mac's output while the speaker is selected.

## Setup (and again after moving Reachy to another Mac)

1. `brew install blueutil switchaudio-osx`
2. Allow Bluetooth for the app that runs Reachy (System Settings > Privacy & Security > Bluetooth;
   for the Claude desktop app's Terminal panel, that is Claude).
3. Install Spotify on the Mac and sign in with the same account as Reachy's Spotify credentials.
4. Pair the speaker: say "Hey Google, Bluetooth pairing", then pick the speaker in the Mac's
   Bluetooth settings.
5. Run, from the project folder:

   ```bash
   .venv/bin/python -m reachy_mini_conversation_app.music_speaker setup "Living Room Speaker"
   ```

   It connects the speaker, finds the Mac's Spotify device (named after the computer) and saves
   `MUSIC_SPEAKER_BLUETOOTH`, `MUSIC_SPEAKER_OUTPUT` and `SPOTIFY_DEVICE_ID` to `.env`.
6. Restart Reachy.

On a new Mac, also copy `~/Documents/Reachy/private-spotify/credentials.json` (Reachy's Spotify
sign-in) or sign in again, and pair the speaker with the new Mac.

## Tests

`tests/test_music_speaker.py` (blueutil and SwitchAudioSource faked).
