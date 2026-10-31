"""The few app settings the admin app may change: lines of the app's ``.env`` file, applied on the next restart.

Only the keys in SETTINGS are read or written; every other line (keys, secrets, comments) is kept exactly as
it was, and values never leave this computer except as the settings page shows them.
"""

import re
from typing import Final
from pathlib import Path
from dataclasses import dataclass

from reachy_mini_conversation_app.config import _quiet_hours
from reachy_mini_conversation_app.admin.records import write_atomic


@dataclass(frozen=True)
class Setting:
    """One editable ``.env`` key: its label, kind (choice, flag, number, hours, names), allowed values and default."""

    label: str
    kind: str
    choices: tuple[str, ...] = ()
    low: float = 0.0
    high: float = 1.0
    default: str = ""


LANGUAGES: Final[tuple[str, ...]] = ("es", "en", "it", "pt")
SETTINGS: Final[dict[str, Setting]] = {
    "QUIET_HOURS": Setting("Quiet hours (no wake, calls declined)", "hours"),
    "REALTIME_TRANSCRIPTION_LANGUAGE": Setting("Conversation language", "choice", ("auto", *LANGUAGES)),
    "WHATSAPP_CALLS_LANGUAGE": Setting("Language for call announcements", "choice", LANGUAGES, default="es"),
    "WHATSAPP_CALLERS": Setting("Who may start calls (face ids)", "names", default="eduardo,adriana"),
    "LOCAL_ACOUSTIC_WAKE_THRESHOLD": Setting(
        "Wake sensitivity (lower wakes more easily)", "number", low=0.05, high=0.99, default="0.5"
    ),
    "FACE_RECOGNITION_ENABLED": Setting("Face recognition", "flag", default="0"),
    "SPEAKER_FOLLOWING_ENABLED": Setting("Turn toward whoever speaks", "flag", default="1"),
    "DISPLAY_NOW_PLAYING": Setting("Show the song on the e-ink screen", "flag", default="1"),
    "PHOTO_ARCHIVE_ENABLED": Setting("Keep photos in the gallery", "flag", default="1"),
}
_LINE = re.compile(r"^\s*(?:export\s+)?([A-Z][A-Z0-9_]*)\s*=\s*(.*?)\s*$")
_NAMES = re.compile(r"[a-z0-9_-]+(?:,[a-z0-9_-]+)*")


class SettingError(ValueError):
    """A key the admin app may not change or a value it does not accept."""


def _unquote(value: str) -> str:
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
        return value[1:-1]
    return value.split(" #", 1)[0].strip()


def read_settings(env_file: Path) -> dict[str, str]:
    """Return the current value of every editable key ("" when unset)."""
    values = dict.fromkeys(SETTINGS, "")
    try:
        lines = env_file.read_text().splitlines()
    except FileNotFoundError:
        return values
    for line in lines:
        match = _LINE.match(line)
        if match is not None and match.group(1) in SETTINGS:
            values[match.group(1)] = _unquote(match.group(2))
    return values


def check(key: str, value: str) -> str:
    """Return ``value`` cleaned for ``key``; raises SettingError when the key or value is not accepted."""
    setting = SETTINGS.get(key)
    if setting is None:
        raise SettingError(f"{key} cannot be changed here")
    if re.search(r"[\x00-\x1f\x7f]", value):
        raise SettingError(f"{setting.label}: one line of text")
    value = value.strip()
    if not value:
        return ""
    if setting.kind == "choice" and value not in setting.choices:
        raise SettingError(f"{setting.label}: choose one of {', '.join(setting.choices)}")
    if setting.kind == "flag" and value not in {"0", "1"}:
        raise SettingError(f"{setting.label}: on or off")
    if setting.kind == "number":
        try:
            number = float(value)
        except ValueError as error:
            raise SettingError(f"{setting.label}: a number") from error
        if not setting.low <= number <= setting.high:
            raise SettingError(f"{setting.label}: between {setting.low} and {setting.high}")
    if setting.kind == "hours" and _quiet_hours(value) is None:
        raise SettingError(f"{setting.label}: like 21:30-07:00")
    if setting.kind == "names":
        value = ",".join(part.strip().lower() for part in value.split(",") if part.strip())
        if not _NAMES.fullmatch(value):
            raise SettingError(f"{setting.label}: names separated by commas")
    return value


def write_settings(env_file: Path, changes: dict[str, str]) -> None:
    """Set each key in ``changes`` (checked) in ``env_file``; an empty value removes the key."""
    cleaned = {key: check(key, value) for key, value in changes.items()}
    try:
        lines = env_file.read_text().splitlines()
    except FileNotFoundError:
        lines = []
    kept: list[str] = []
    written: set[str] = set()
    for line in lines:
        match = _LINE.match(line)
        key = match.group(1) if match is not None else ""
        if key not in cleaned:
            kept.append(line)
        elif key not in written:  # a repeated key would win over the new value, so it goes
            written.add(key)
            if cleaned[key]:
                kept.append(f"{key}={cleaned[key]}")
    kept.extend(f"{key}={value}" for key, value in cleaned.items() if value and key not in written)
    write_atomic(env_file, ("\n".join(kept) + "\n").encode())
