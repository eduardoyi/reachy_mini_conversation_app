"""Play Reachy's Spotify on a Bluetooth speaker paired with the Mac he runs on.

The Living Room Speaker is a Google Home Mini. As a Google Cast device it is on Spotify's device
list only after a Spotify app has woken it, and it drops off when idle, so Reachy could not start
music there (2026-10-18). Instead the Home Mini is paired with the Mac as a Bluetooth speaker and
Reachy plays on the Mac's own Spotify app, which is always listed. Before playing he makes sure the
Spotify app is running, the speaker is connected and the Mac's sound output points to it. Reachy's
own voice is unaffected: it goes to the "Reachy Mini Audio" card by name.

Uses ``blueutil`` and ``SwitchAudioSource`` (``brew install blueutil switchaudio-osx``). The app that
runs Reachy needs Bluetooth access (System Settings > Privacy & Security > Bluetooth).

Set up on a Mac (again after moving Reachy to another one):

    .venv/bin/python -m reachy_mini_conversation_app.music_speaker setup "Living Room Speaker"
"""

from __future__ import annotations
import os
import sys
import json
import time
import shutil
import logging
import argparse
import subprocess
from typing import Any, Final, Callable
from pathlib import Path


logger = logging.getLogger(__name__)

CONNECT_WAIT_S: Final[float] = 8.0
SPOTIFY_LAUNCH_WAIT_S: Final[float] = 10.0
POLL_S: Final[float] = 0.5

Runner = Callable[..., "subprocess.CompletedProcess[str]"]


def _run(*command: str, timeout: float = 10.0) -> subprocess.CompletedProcess[str]:
    return subprocess.run(list(command), capture_output=True, text=True, timeout=timeout, check=False)


class MusicSpeaker:
    """A Bluetooth speaker the Mac plays Spotify through."""

    def __init__(
        self,
        address: str,
        output_name: str | None = None,
        *,
        runner: Runner = _run,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        """Use the speaker at Bluetooth ``address``, shown to macOS as output ``output_name``."""
        self.address = address
        self.output_name = output_name
        self._run = runner
        self._sleep = sleep
        self._clock = clock

    def connected(self) -> bool:
        """Whether the speaker is connected now."""
        result = self._run("blueutil", "--is-connected", self.address)
        return result.returncode == 0 and result.stdout.strip() == "1"

    def ensure_ready(self) -> bool:
        """Connect the speaker if needed and send the Mac's sound to it; returns whether it is ready."""
        try:
            if not self.connected():
                logger.info("Connecting the music speaker over Bluetooth")
                self._run("blueutil", "--connect", self.address, timeout=CONNECT_WAIT_S + 2)
                deadline = self._clock() + CONNECT_WAIT_S
                while not self.connected():
                    if self._clock() >= deadline:
                        logger.warning("Music speaker did not connect over Bluetooth")
                        return False
                    self._sleep(POLL_S)
            if self.output_name:
                current = self._run("SwitchAudioSource", "-c", "-t", "output").stdout.strip()
                if current != self.output_name:
                    result = self._run("SwitchAudioSource", "-s", self.output_name, "-t", "output")
                    if result.returncode != 0:
                        logger.warning("Could not send the Mac's sound to the music speaker")
                        return False
            return True
        except (OSError, subprocess.SubprocessError) as error:
            logger.warning("Music speaker unavailable: %s", type(error).__name__)
            return False


def ensure_spotify_app_running(runner: Runner = _run, sleep: Callable[[float], None] = time.sleep) -> bool:
    """Open the Mac's Spotify app in the background if it is not running (it is the playback device)."""
    if sys.platform != "darwin":
        return True
    try:
        if runner("pgrep", "-x", "Spotify").returncode == 0:
            return True
        logger.info("Opening the Spotify app")
        runner("open", "-g", "-a", "Spotify")
        waited = 0.0
        while waited < SPOTIFY_LAUNCH_WAIT_S:
            sleep(POLL_S)
            waited += POLL_S
            if runner("pgrep", "-x", "Spotify").returncode == 0:
                sleep(2.0)  # it registers with Spotify a moment after starting
                return True
        return False
    except (OSError, subprocess.SubprocessError) as error:
        logger.warning("Could not open Spotify: %s", type(error).__name__)
        return False


def configured_speaker() -> MusicSpeaker | None:
    """Return the speaker from ``MUSIC_SPEAKER_BLUETOOTH`` / ``MUSIC_SPEAKER_OUTPUT``, or None."""
    address = (os.getenv("MUSIC_SPEAKER_BLUETOOTH") or "").strip()
    if not address:
        return None
    return MusicSpeaker(address, (os.getenv("MUSIC_SPEAKER_OUTPUT") or "").strip() or None)


def prepare_for_playback() -> None:
    """Get the Mac ready to play Spotify music (blocking; run it in a thread)."""
    speaker = configured_speaker()
    if speaker is None:
        return
    ensure_spotify_app_running()
    speaker.ensure_ready()


# --------------------------------------------------------------------------------------------
# Setup


def paired_devices(runner: Runner = _run) -> list[dict[str, Any]]:
    """Return the Mac's paired Bluetooth devices (name, address, connected)."""
    result = runner("blueutil", "--paired", "--format", "json")
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or "blueutil failed; does this app have Bluetooth access?")
    devices = json.loads(result.stdout or "[]")
    return [device for device in devices if isinstance(device, dict)]


def output_devices(runner: Runner = _run) -> list[str]:
    """Return the Mac's sound output names."""
    result = runner("SwitchAudioSource", "-a", "-t", "output")
    return [line.strip() for line in result.stdout.splitlines() if line.strip()]


async def mac_spotify_device_id() -> tuple[str, str] | None:
    """Return (id, name) of this Mac's Spotify app in Spotify's device list."""
    import httpx

    from reachy_mini_conversation_app.tools.spotify_control import spotify_access_token

    credentials = os.getenv("SPOTIFY_CREDENTIALS_FILE", "").strip()
    if not credentials:
        raise RuntimeError("SPOTIFY_CREDENTIALS_FILE is not set")
    computer = _run("scutil", "--get", "ComputerName").stdout.strip()
    async with httpx.AsyncClient(timeout=10) as client:
        headers = {"Authorization": "Bearer " + await spotify_access_token(client, credentials)}
        response = await client.get("https://api.spotify.com/v1/me/player/devices", headers=headers)
        response.raise_for_status()
        devices = response.json().get("devices", [])
    for device in devices:
        if device.get("type") == "Computer" and device.get("name") == computer:
            return str(device["id"]), str(device["name"])
    return None


def _setup(name: str, env_path: Path) -> int:
    import asyncio

    from dotenv import set_key

    missing = [tool for tool in ("blueutil", "SwitchAudioSource") if shutil.which(tool) is None]
    if missing:
        print("Install first: brew install blueutil switchaudio-osx", file=sys.stderr)
        return 1
    wanted = name.casefold()
    matches = [device for device in paired_devices() if wanted in str(device.get("name", "")).casefold()]
    if len(matches) != 1:
        names = ", ".join(str(device.get("name")) for device in paired_devices()) or "none"
        print(f"Pair the speaker with this Mac first (paired: {names}).", file=sys.stderr)
        return 1
    device = matches[0]
    speaker = MusicSpeaker(str(device["address"]))
    if not speaker.ensure_ready():
        print("The speaker is paired but did not connect; is it on and nearby?", file=sys.stderr)
        return 1
    time.sleep(1.0)
    outputs = [output for output in output_devices() if output.casefold() == str(device["name"]).casefold()]
    output = outputs[0] if outputs else str(device["name"])
    ensure_spotify_app_running()
    spotify = asyncio.run(mac_spotify_device_id())
    if spotify is None:
        print("This Mac's Spotify app is not on Spotify's device list; sign in to it and try again.", file=sys.stderr)
        return 1
    env_path.touch(mode=0o600, exist_ok=True)
    set_key(str(env_path), "MUSIC_SPEAKER_BLUETOOTH", str(device["address"]), quote_mode="never")
    set_key(str(env_path), "MUSIC_SPEAKER_OUTPUT", output, quote_mode="always")
    set_key(str(env_path), "SPOTIFY_DEVICE_ID", spotify[0], quote_mode="never")
    print(f"Music plays on {device['name']} through this Mac's Spotify ({spotify[1]}). Restart Reachy.")
    return 0


def main(argv: list[str] | None = None) -> int:
    """``python -m reachy_mini_conversation_app.music_speaker setup "Living Room Speaker"``."""
    parser = argparse.ArgumentParser(description="Play Reachy's Spotify on a Bluetooth speaker paired with this Mac.")
    sub = parser.add_subparsers(dest="command", required=True)
    setup = sub.add_parser("setup", help="choose the paired speaker and this Mac's Spotify app; saves to .env")
    setup.add_argument("name", help="part of the speaker's Bluetooth name, e.g. 'Living Room Speaker'")
    setup.add_argument("--env", default=".env", help="the .env file to update (default: ./.env)")
    args = parser.parse_args(argv)
    from dotenv import load_dotenv

    load_dotenv(args.env)
    try:
        return _setup(args.name, Path(args.env))
    except Exception as error:  # blueutil, Spotify and network errors all end setup the same way
        print(f"Setup failed: {type(error).__name__}: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
