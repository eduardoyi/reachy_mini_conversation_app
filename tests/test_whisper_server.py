"""Tests for the local Whisper server's per-clip limits."""

import importlib.util
from pathlib import Path


_SPEC = importlib.util.spec_from_file_location(
    "whisper_server", Path(__file__).resolve().parents[1] / "scripts" / "whisper_server.py"
)
assert _SPEC is not None and _SPEC.loader is not None
whisper_server = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(whisper_server)


def test_token_budget_grows_with_clip_length_and_stops_at_whisper_limit() -> None:
    """A longer clip may produce more text, never past Whisper's decoder limit."""
    assert whisper_server.token_budget(0) == 16
    assert whisper_server.token_budget(3.0) == 46
    assert whisper_server.token_budget(5.9) == 75
    assert whisper_server.token_budget(60.0) == whisper_server.MAX_NEW_TOKENS
    assert whisper_server.token_budget(float("nan")) == 16


def test_token_budget_leaves_room_for_fast_speech() -> None:
    """About six tokens a second is fast talk; the budget must not cut it off."""
    for seconds in (1.0, 4.0, 10.0, 25.0):
        assert whisper_server.token_budget(seconds) > seconds * 6


def test_languages_default_to_household_and_auto_means_any() -> None:
    """Unset uses the household's languages; auto lets Whisper detect any."""
    assert whisper_server.parse_languages(None) == ("en", "es", "it", "pt")
    assert whisper_server.parse_languages(" ") == ("en", "es", "it", "pt")
    assert whisper_server.parse_languages("ES, en,es") == ("es", "en")
    assert whisper_server.parse_languages("auto") == ()
