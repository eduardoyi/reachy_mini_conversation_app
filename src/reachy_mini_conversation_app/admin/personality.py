"""Reachy's instructions as the admin app edits them: the active profile's own, or the household's edited copy.

Edits go to ``PERSONALITY_DIR/<profile>.md`` (prompts.py reads it at the start of each conversation), never into
the profile in the repository, so an update cannot overwrite them. Saving the original text again removes the copy.
"""

import hashlib
from typing import Any, Final

from reachy_mini_conversation_app.config import config
from reachy_mini_conversation_app.prompts import personality_path, edited_instructions
from reachy_mini_conversation_app.admin.records import RecordError, StaleVersion, write_atomic
from reachy_mini_conversation_app.profile_store import ProfileFormatError, read_profile, canonical_profile_name


MAX_CHARS: Final[int] = 40_000


def _version(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()[:16]


def read_personality() -> dict[str, Any]:
    """Return the active profile's name, its original and current instructions, and a version to save against."""
    profile = config.REACHY_MINI_CUSTOM_PROFILE
    try:
        original = read_profile(profile).instructions.strip()
    except (FileNotFoundError, ProfileFormatError) as error:
        raise RecordError(f"the {canonical_profile_name(profile)} profile cannot be read") from error
    edited = edited_instructions(profile)
    text = edited or original
    return {
        "profile": canonical_profile_name(profile),
        "text": text,
        "original": original,
        "edited": edited is not None and edited != original,
        "version": _version(text),
    }


def write_personality(text: str, based_on: str) -> dict[str, Any]:
    """Save ``text`` as the instructions if nobody saved since ``based_on``; the original text undoes the edit."""
    current = read_personality()
    if current["version"] != based_on:
        raise StaleVersion("the personality changed elsewhere")
    text = text.strip().replace("\r\n", "\n")
    if not text or len(text) > MAX_CHARS:
        raise ValueError(f"the personality must be 1 to {MAX_CHARS} characters")
    path = personality_path(config.REACHY_MINI_CUSTOM_PROFILE)
    if text == current["original"]:
        path.unlink(missing_ok=True)
    else:
        write_atomic(path, (text + "\n").encode())
    return read_personality()
