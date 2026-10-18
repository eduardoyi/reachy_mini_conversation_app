"""Control an Android / Google TV over the local network with the Android TV Remote protocol v2.

The same protocol the Google TV phone app uses as a remote (``androidtvremote2``, the library Home
Assistant uses): power, volume, media and navigation keys, opening apps and links, and the TV's
reported state (on, current app, volume). Pairing happens once: the TV shows a code on screen and
the client certificate it accepts is kept in ``~/.config/reachy-tv``, outside the repository,
because it controls the TV.

    python -m reachy_mini_conversation_app.android_tv pair [host]
"""

import sys
import asyncio
import logging
import argparse
from typing import Any, Final, Callable
from pathlib import Path
from dataclasses import dataclass


logger = logging.getLogger(__name__)

DEFAULT_CERT_DIR: Final[Path] = Path.home() / ".config" / "reachy-tv"
CLIENT_NAME: Final[str] = "Reachy Mini"
CONNECT_TIMEOUT_S: Final[float] = 5.0
# Volume keys are sent one step at a time; a short gap lets the TV register each one.
VOLUME_KEY_GAP_S: Final[float] = 0.05
MAX_VOLUME_STEPS: Final[int] = 60
# The Mi TV P1 reports a power change about 3 s after the key.
POWER_CONFIRM_S: Final[float] = 6.0
POWER_POLL_S: Final[float] = 0.25
# The TV reports its state (on, app, volume) shortly after connecting.
FIRST_STATE_WAIT_S: Final[float] = 1.5
# An app that opens shows up as the current app within a few seconds; one that is not installed
# never does (the TV only logs an error).
APP_CONFIRM_S: Final[float] = 4.0
# Spoken app names -> Android TV package names (also used to name the current app).
APPS: Final[dict[str, str]] = {
    "youtube": "com.google.android.youtube.tv",
    "youtube kids": "com.google.android.youtube.tvkids",
    "netflix": "com.netflix.ninja",
    "disney": "com.disney.disneyplus",
    "disney plus": "com.disney.disneyplus",
    "prime video": "com.amazon.amazonvideo.livingroom",
    "amazon prime": "com.amazon.amazonvideo.livingroom",
    "spotify": "com.spotify.tv.android",
    "max": "com.wbd.stream",
    "hbo max": "com.wbd.stream",
    "apple tv": "com.apple.atve.androidtv.appletv",
    "twitch": "tv.twitch.android.app",
    "plex": "com.plexapp.android",
}
# How to open each app. The Mi TV P1 refuses "market://launch?id=<package>" links even for YouTube
# but opens an app from its own web link (checked 2026-10-18: YouTube, Netflix, Prime Video).
APP_LINKS: Final[dict[str, str]] = {
    "com.google.android.youtube.tv": "https://www.youtube.com",
    "com.google.android.youtube.tvkids": "https://www.youtubekids.com",
    "com.netflix.ninja": "https://www.netflix.com/browse",
    "com.disney.disneyplus": "https://www.disneyplus.com",
    "com.amazon.amazonvideo.livingroom": "https://app.primevideo.com",
    "com.spotify.tv.android": "spotify://",
    "com.wbd.stream": "https://play.max.com",
    "com.apple.atve.androidtv.appletv": "https://tv.apple.com",
    "tv.twitch.android.app": "https://www.twitch.tv",
    "com.plexapp.android": "plex://",
}
MEDIA_KEYS: Final[dict[str, str]] = {
    "play": "MEDIA_PLAY",
    "pause": "MEDIA_PAUSE",
    "play_pause": "MEDIA_PLAY_PAUSE",
    "stop": "MEDIA_STOP",
    "next": "MEDIA_NEXT",
    "previous": "MEDIA_PREVIOUS",
    "rewind": "MEDIA_REWIND",
    "fast_forward": "MEDIA_FAST_FORWARD",
}
NAVIGATION_KEYS: Final[dict[str, str]] = {
    "up": "DPAD_UP",
    "down": "DPAD_DOWN",
    "left": "DPAD_LEFT",
    "right": "DPAD_RIGHT",
    "select": "DPAD_CENTER",
    "back": "BACK",
    "home": "HOME",
}


class TVError(RuntimeError):
    """The TV could not be reached or is not paired."""


@dataclass(frozen=True)
class TVState:
    """What the TV reports."""

    is_on: bool | None
    current_app: str | None
    volume: int | None  # 0-100 % of the TV's maximum
    muted: bool | None

    @property
    def app_name(self) -> str | None:
        """The spoken name of the current app, or its package name."""
        if not self.current_app:
            return None
        names = {package: name for name, package in reversed(APPS.items())}
        return names.get(self.current_app, self.current_app)


def cert_paths(cert_dir: Path) -> tuple[Path, Path]:
    """Return the client certificate and key paths."""
    return cert_dir / "cert.pem", cert_dir / "key.pem"


def is_paired(cert_dir: Path) -> bool:
    """Whether a client certificate exists (pairing has been run)."""
    return all(path.is_file() for path in cert_paths(cert_dir))


def resolve_app(name: str) -> str | None:
    """Return the package for a spoken app name, a package name as given, or None."""
    wanted = " ".join(name.casefold().replace("+", " plus").replace("-", " ").split())
    if wanted in APPS:
        return APPS[wanted]
    if "." in wanted and " " not in wanted:
        return name.strip()  # already a package name
    matches = {package for spoken, package in APPS.items() if wanted and wanted in spoken}
    return matches.pop() if len(matches) == 1 else None


def volume_steps(current: int, maximum: int, target_percent: int) -> int:
    """Return the key presses from ``current`` to ``target_percent`` of ``maximum`` (+ up, - down)."""
    if maximum <= 0:
        return 0
    target = round(max(0, min(100, target_percent)) / 100 * maximum)
    return max(-MAX_VOLUME_STEPS, min(MAX_VOLUME_STEPS, target - current))


class AndroidTV:
    """One paired TV, connected on first use and kept connected."""

    def __init__(
        self,
        host: str,
        cert_dir: Path = DEFAULT_CERT_DIR,
        *,
        remote_factory: Callable[..., Any] | None = None,
    ) -> None:
        """Control the TV at ``host``; ``remote_factory`` replaces androidtvremote2 in tests."""
        self.host = host
        self._certfile, self._keyfile = cert_paths(cert_dir)
        self._remote_factory = remote_factory or _androidtvremote
        self._remote: Any = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._lock: asyncio.Lock | None = None

    async def _connected(self) -> Any:
        loop = asyncio.get_running_loop()
        if self._loop is not loop:
            # Connections and locks belong to one event loop.
            self._drop()
            self._loop, self._lock = loop, asyncio.Lock()
        assert self._lock is not None
        async with self._lock:
            if self._remote is not None:
                return self._remote
            remote = self._remote_factory(
                client_name=CLIENT_NAME,
                certfile=str(self._certfile),
                keyfile=str(self._keyfile),
                host=self.host,
            )
            from androidtvremote2 import InvalidAuth, CannotConnect, ConnectionClosed

            try:
                await asyncio.wait_for(remote.async_connect(), CONNECT_TIMEOUT_S)
            except InvalidAuth as error:
                raise TVError("the TV is not paired with Reachy; run the pairing command again") from error
            except (CannotConnect, ConnectionClosed, asyncio.TimeoutError, OSError) as error:
                raise TVError("the TV is not reachable on the network (is it unplugged?)") from error
            remote.keep_reconnecting()
            self._remote = remote
            await self._wait_for(
                lambda: remote.is_on is not None and remote.current_app is not None, FIRST_STATE_WAIT_S
            )
            return remote

    @staticmethod
    async def _wait_for(condition: Callable[[], bool], timeout_s: float) -> bool:
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout_s
        while not condition():
            if loop.time() >= deadline:
                return False
            await asyncio.sleep(POWER_POLL_S)
        return True

    def _drop(self) -> None:
        if self._remote is not None:
            try:
                self._remote.disconnect()
            except Exception:
                pass
        self._remote = None

    async def close(self) -> None:
        """Disconnect."""
        self._drop()

    async def _send(self, action: Callable[[Any], None]) -> Any:
        """Run one command; on a dropped connection, reconnect once and retry."""
        from androidtvremote2 import ConnectionClosed

        remote = await self._connected()
        try:
            action(remote)
        except ConnectionClosed:
            self._drop()
            remote = await self._connected()
            action(remote)
        return remote

    async def state(self) -> TVState:
        """Return what the TV reports."""
        remote = await self._connected()
        volume_info = remote.volume_info or {}
        maximum = int(volume_info.get("max") or 0)
        level = volume_info.get("level")
        return TVState(
            is_on=remote.is_on,
            current_app=remote.current_app,
            volume=round(int(level) / maximum * 100) if maximum and level is not None else None,
            muted=volume_info.get("muted"),
        )

    async def power(self, on: bool) -> bool:
        """Turn the TV on or off; returns whether it reported the new state within a few seconds.

        The Mi TV P1 ignores WAKEUP and SLEEP (tried 2026-10-18), so this toggles POWER, and only
        when the TV reports the other state. WAKEUP/SLEEP are the fallback when it reports none.
        """
        remote = await self._connected()
        if remote.is_on is on:
            return True
        key = "POWER" if remote.is_on is not None else ("WAKEUP" if on else "SLEEP")
        await self._send(lambda remote: remote.send_key_command(key))
        return await self._wait_for(lambda: self._remote is not None and self._remote.is_on is on, POWER_CONFIRM_S)

    async def key(self, key_code: str, repeat: int = 1) -> None:
        """Press one remote key ``repeat`` times."""
        for index in range(max(1, repeat)):
            if index:
                await asyncio.sleep(VOLUME_KEY_GAP_S)
            await self._send(lambda remote: remote.send_key_command(key_code))

    async def set_volume(self, percent: int) -> None:
        """Step the volume to ``percent`` of the TV's maximum."""
        remote = await self._connected()
        volume_info = remote.volume_info or {}
        maximum, level = int(volume_info.get("max") or 0), volume_info.get("level")
        if not maximum or level is None:
            raise TVError("the TV did not report its volume")
        steps = volume_steps(int(level), maximum, percent)
        if steps:
            await self.key("VOLUME_UP" if steps > 0 else "VOLUME_DOWN", abs(steps))

    async def set_muted(self, muted: bool) -> None:
        """Mute or unmute."""
        remote = await self._connected()
        if bool((remote.volume_info or {}).get("muted")) != muted:
            await self.key("VOLUME_MUTE")

    async def open(self, app_or_link: str) -> None:
        """Open a link (``https://www.youtube.com/...``) or an app by package name, without waiting."""
        await self._send(lambda remote: remote.send_launch_app_command(app_or_link))

    async def open_app(self, package: str) -> bool:
        """Open an app; returns whether it became the current app (False: probably not installed).

        Tries the app's web link first, then the Play Store launch link other TVs accept.
        """
        for link in dict.fromkeys((APP_LINKS.get(package, package), package)):
            await self.open(link)
            if await self._wait_for(
                lambda: self._remote is not None and self._remote.current_app == package, APP_CONFIRM_S
            ):
                return True
        return False


def _androidtvremote(**kwargs: Any) -> Any:
    from androidtvremote2 import AndroidTVRemote

    return AndroidTVRemote(**kwargs)


async def _pair(host: str, cert_dir: Path, read_code: Callable[[], str]) -> str:
    """Pair with the TV; returns its name."""
    from androidtvremote2 import AndroidTVRemote

    cert_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    certfile, keyfile = cert_paths(cert_dir)
    remote = AndroidTVRemote(CLIENT_NAME, str(certfile), str(keyfile), host)
    await remote.async_generate_cert_if_missing()
    keyfile.chmod(0o600)
    name, _mac = await remote.async_get_name_and_mac()
    await remote.async_start_pairing()
    code = await asyncio.get_running_loop().run_in_executor(None, read_code)
    await remote.async_finish_pairing(code.strip())
    await remote.async_connect()
    remote.disconnect()
    return name


def discover_host(timeout_s: float = 4.0) -> str | None:
    """Find an Android TV through Bonjour (macOS ``dns-sd``); return its ``.local`` hostname, or None."""
    import re
    import subprocess

    def run(*args: str) -> str:
        try:
            return subprocess.run(
                ["perl", "-e", f"alarm {int(timeout_s)}; exec @ARGV", "dns-sd", *args],
                capture_output=True,
                text=True,
                timeout=timeout_s + 2,
            ).stdout
        except (OSError, subprocess.SubprocessError):
            return ""

    browse = run("-B", "_androidtvremote2._tcp", "local.")
    match = re.search(r"_androidtvremote2\._tcp\.\s+(.+?)\s*$", browse, re.MULTILINE)
    if not match:
        return None
    lookup = run("-L", match.group(1), "_androidtvremote2._tcp", "local.")
    host = re.search(r"can be reached at (\S+?)\.?:\d+", lookup)
    return host.group(1) if host else None


def main(argv: list[str] | None = None) -> int:
    """Pair with a TV: ``python -m reachy_mini_conversation_app.android_tv pair [host]``."""
    parser = argparse.ArgumentParser(description="Pair Reachy with an Android / Google TV.")
    sub = parser.add_subparsers(dest="command", required=True)
    pair = sub.add_parser("pair", help="pair once and save the TV's address to .env")
    pair.add_argument("host", nargs="?", default=None, help="TV hostname or IP (default: find it on the network)")
    pair.add_argument("--env", default=".env", help="the .env file to update (default: ./.env)")
    pair.add_argument("--cert-dir", default=str(DEFAULT_CERT_DIR), help="where to keep the client certificate")
    args = parser.parse_args(argv)
    host = args.host or discover_host()
    if not host:
        print("No Android TV found on the network; pass its address, e.g. 192.168.1.50", file=sys.stderr)
        return 1
    print(f"Pairing with the TV at {host}; a code will appear on the TV screen.")
    try:
        name = asyncio.run(_pair(host, Path(args.cert_dir), lambda: input("Code shown on the TV: ")))
    except Exception as error:  # androidtvremote2 raises its own errors and OSError
        print(f"Pairing failed: {type(error).__name__}: {error}", file=sys.stderr)
        return 1
    from dotenv import set_key

    env_path = Path(args.env)
    env_path.touch(mode=0o600, exist_ok=True)
    set_key(str(env_path), "ANDROID_TV_HOST", host)
    print(f"Paired with {name}; saved ANDROID_TV_HOST to {env_path}. Restart Reachy to use it.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
