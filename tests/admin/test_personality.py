"""Editing Reachy's instructions from the admin app: kept outside the profile, used by the next conversation."""

from pathlib import Path

import pytest

from reachy_mini_conversation_app import prompts
from reachy_mini_conversation_app.config import config
from reachy_mini_conversation_app.admin.records import StaleVersion
from reachy_mini_conversation_app.admin.personality import read_personality, write_personality


@pytest.fixture(autouse=True)
def _personality_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Keep edited instructions in a temp folder."""
    monkeypatch.setattr(config, "PERSONALITY_DIR", str(tmp_path / "personality"))
    return tmp_path / "personality"


def test_unedited_shows_the_profiles_own_instructions() -> None:
    """With nothing edited the text is the profile's, marked unedited."""
    current = read_personality()
    assert current["text"] == current["original"] and current["text"]
    assert current["edited"] is False and current["profile"] == "default"


def test_an_edit_is_used_by_the_next_conversation_and_the_original_undoes_it(_personality_dir: Path) -> None:
    """Saved text goes to the profile's own file outside the repository; saving the original removes it."""
    current = read_personality()
    edited = write_personality(current["text"] + "\n\n## BEDTIME\nWhisper after 8 pm.", current["version"])
    assert edited["edited"] is True and (_personality_dir / "default.md").is_file()
    assert "Whisper after 8 pm." in prompts.get_session_instructions()
    restored = write_personality(edited["original"], edited["version"])
    assert restored["edited"] is False and not (_personality_dir / "default.md").exists()
    assert "Whisper after 8 pm." not in prompts.get_session_instructions()


def test_a_stale_or_empty_save_is_refused() -> None:
    """A save based on an older version, or blank text, changes nothing."""
    current = read_personality()
    write_personality(current["text"] + " Extra.", current["version"])
    with pytest.raises(StaleVersion):
        write_personality("Something else.", current["version"])
    with pytest.raises(ValueError):
        write_personality("   ", read_personality()["version"])
