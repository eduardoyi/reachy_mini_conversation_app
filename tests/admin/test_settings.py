"""The few ``.env`` keys the admin app may change, leaving every other line exactly as it was."""

from pathlib import Path

import pytest

from reachy_mini_conversation_app.admin.settings import (
    SETTINGS,
    SettingError,
    check,
    read_settings,
    write_settings,
)


ENV = (
    "# Reachy settings\n"
    "GEMINI_API_KEY='sk-secret value'\n"
    'export QUIET_HOURS="22:00-07:00"\n'
    "\n"
    "  FACE_RECOGNITION_ENABLED = 1   # on\n"
    "# WHATSAPP_CALLS_LANGUAGE=en\n"
    "lowercase=kept\n"
    "WHATSAPP_CALLERS=ana,ben\n"
    "OTHER=x # trailing comment\n"
)


@pytest.fixture
def env_file(tmp_path: Path) -> Path:
    """Write an .env file with secrets, comments, export and quoted values."""
    path = tmp_path / ".env"
    path.write_text(ENV)
    return path


def test_read_returns_every_editable_key_unquoted(env_file: Path) -> None:
    """Values are read without quotes, export or trailing comments; unset keys are empty."""
    values = read_settings(env_file)
    assert set(values) == set(SETTINGS)
    assert values["QUIET_HOURS"] == "22:00-07:00"
    assert values["FACE_RECOGNITION_ENABLED"] == "1"
    assert values["WHATSAPP_CALLERS"] == "ana,ben"
    assert values["WHATSAPP_CALLS_LANGUAGE"] == ""  # commented out
    assert "GEMINI_API_KEY" not in values and "OTHER" not in values


def test_read_missing_file_is_all_empty(tmp_path: Path) -> None:
    """No .env yet: every editable key is unset."""
    assert read_settings(tmp_path / ".env") == dict.fromkeys(SETTINGS, "")


def test_write_changes_only_allowlisted_lines(env_file: Path) -> None:
    """Changed keys are rewritten in place; every other line stays byte-identical."""
    write_settings(env_file, {"QUIET_HOURS": "21:30-07:00", "FACE_RECOGNITION_ENABLED": "0"})
    lines = env_file.read_text().splitlines()
    original = ENV.splitlines()
    assert lines[2] == "QUIET_HOURS=21:30-07:00"
    assert lines[4] == "FACE_RECOGNITION_ENABLED=0"
    assert [line for i, line in enumerate(lines) if i not in {2, 4}] == [
        line for i, line in enumerate(original) if i not in {2, 4}
    ]
    assert read_settings(env_file)["QUIET_HOURS"] == "21:30-07:00"


def test_write_appends_a_key_that_was_not_set(env_file: Path) -> None:
    """A new key goes at the end; a commented-out line with the same name is left alone."""
    write_settings(env_file, {"WHATSAPP_CALLS_LANGUAGE": "it"})
    text = env_file.read_text()
    assert text == ENV + "WHATSAPP_CALLS_LANGUAGE=it\n"


def test_write_empty_value_removes_the_key(env_file: Path) -> None:
    """Clearing a setting deletes its line so the app falls back to its default."""
    write_settings(env_file, {"WHATSAPP_CALLERS": "", "LOCAL_ACOUSTIC_WAKE_THRESHOLD": "  "})
    text = env_file.read_text()
    assert "WHATSAPP_CALLERS" not in text and "LOCAL_ACOUSTIC_WAKE_THRESHOLD" not in text
    assert text == ENV.replace("WHATSAPP_CALLERS=ana,ben\n", "")


def test_write_collapses_a_repeated_key_so_the_new_value_wins(tmp_path: Path) -> None:
    """A later duplicate line would override the change when the app loads, so only one line remains."""
    env_file = tmp_path / ".env"
    env_file.write_text("QUIET_HOURS=22:00-07:00\nA=1\nexport QUIET_HOURS=23:00-06:00\n")
    write_settings(env_file, {"QUIET_HOURS": "21:30-07:00"})
    assert env_file.read_text() == "QUIET_HOURS=21:30-07:00\nA=1\n"
    assert read_settings(env_file)["QUIET_HOURS"] == "21:30-07:00"


def test_write_creates_a_missing_file(tmp_path: Path) -> None:
    """Writing to a missing .env creates it with just the change."""
    env_file = tmp_path / ".env"
    write_settings(env_file, {"DISPLAY_NOW_PLAYING": "1"})
    assert env_file.read_text() == "DISPLAY_NOW_PLAYING=1\n"


def test_write_with_unknown_key_changes_nothing(env_file: Path) -> None:
    """A key outside the allowlist (a secret, say) is refused and the file is untouched."""
    with pytest.raises(SettingError):
        write_settings(env_file, {"QUIET_HOURS": "21:30-07:00", "GEMINI_API_KEY": "stolen"})
    assert env_file.read_text() == ENV


def test_write_with_invalid_value_changes_nothing(env_file: Path) -> None:
    """One bad value refuses the whole change."""
    with pytest.raises(SettingError):
        write_settings(env_file, {"FACE_RECOGNITION_ENABLED": "0", "QUIET_HOURS": "25:00-01:00"})
    assert env_file.read_text() == ENV


@pytest.mark.parametrize(
    "key,value,cleaned",
    [
        ("REALTIME_TRANSCRIPTION_LANGUAGE", "auto", "auto"),
        ("REALTIME_TRANSCRIPTION_LANGUAGE", " es ", "es"),
        ("WHATSAPP_CALLS_LANGUAGE", "pt", "pt"),
        ("FACE_RECOGNITION_ENABLED", "1", "1"),
        ("SPEAKER_FOLLOWING_ENABLED", "0", "0"),
        ("LOCAL_ACOUSTIC_WAKE_THRESHOLD", "0.05", "0.05"),
        ("LOCAL_ACOUSTIC_WAKE_THRESHOLD", "0.99", "0.99"),
        ("LOCAL_ACOUSTIC_WAKE_THRESHOLD", "0.5", "0.5"),
        ("QUIET_HOURS", "21:30-07:00", "21:30-07:00"),
        ("QUIET_HOURS", "7:00-9:30", "7:00-9:30"),
        ("WHATSAPP_CALLERS", " Ana , BEN,, eduardo_yi ", "ana,ben,eduardo_yi"),
        ("WHATSAPP_CALLERS", "adriana-y", "adriana-y"),
        ("QUIET_HOURS", "", ""),
    ],
)
def test_check_accepts_and_cleans_valid_values(key: str, value: str, cleaned: str) -> None:
    """Each kind accepts its valid values, trimmed (names lowercased and comma-joined)."""
    assert check(key, value) == cleaned


@pytest.mark.parametrize(
    "key,value",
    [
        ("NOT_A_SETTING", "1"),
        ("GEMINI_API_KEY", "x"),
        ("REALTIME_TRANSCRIPTION_LANGUAGE", "fr"),
        ("REALTIME_TRANSCRIPTION_LANGUAGE", "ES"),
        ("WHATSAPP_CALLS_LANGUAGE", "auto"),
        ("REALTIME_TRANSCRIPTION_LANGUAGE", "es\nGEMINI_API_KEY=x"),
        ("FACE_RECOGNITION_ENABLED", "true"),
        ("FACE_RECOGNITION_ENABLED", "2"),
        ("LOCAL_ACOUSTIC_WAKE_THRESHOLD", "0.01"),
        ("LOCAL_ACOUSTIC_WAKE_THRESHOLD", "1"),
        ("LOCAL_ACOUSTIC_WAKE_THRESHOLD", "high"),
        ("LOCAL_ACOUSTIC_WAKE_THRESHOLD", "nan"),
        ("QUIET_HOURS", "25:00-01:00"),
        ("QUIET_HOURS", "21:60-07:00"),
        ("QUIET_HOURS", "22:00-22:00"),
        ("QUIET_HOURS", "tonight"),
        ("WHATSAPP_CALLERS", "ana ben"),
        ("WHATSAPP_CALLERS", "ana,b=n"),
        ("WHATSAPP_CALLERS", ", ,"),
        ("WHATSAPP_CALLERS", "ana\nGEMINI_API_KEY=x"),
    ],
)
def test_check_rejects_unknown_keys_and_invalid_values(key: str, value: str) -> None:
    """Unknown keys and values outside each kind's rule are refused (no line injection)."""
    with pytest.raises(SettingError):
        check(key, value)


def test_hours_with_a_newline_cannot_split_the_env_line(env_file: Path) -> None:
    """A setting must never write more than its own one line."""
    try:
        write_settings(env_file, {"QUIET_HOURS": "21:30\n-07:00"})
    except SettingError:
        return
    assert len(env_file.read_text().splitlines()) == len(ENV.splitlines())
