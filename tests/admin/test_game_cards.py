"""Every game has its Games-tab card: an entry in admin.js on a shelf, and its art (AGENTS.md, "Adding a game")."""

import re
from pathlib import Path

import pytest

from reachy_mini_conversation_app.games.catalog import GAMES, KIDS_GAMES


ADMIN = Path(__file__).resolve().parents[2] / "src" / "reachy_mini_conversation_app" / "static" / "admin"
_CARD = re.compile(r'^\s+(\w+): \{ name: "[^"]+", shelf: "(\w+)"(?:, art: "(\w+)")?', re.MULTILINE)


def _cards() -> dict[str, str]:
    return {key: shelf for key, shelf, _ in _CARD.findall((ADMIN / "admin.js").read_text(encoding="utf-8"))}


def _art() -> dict[str, str]:
    """Each card's art file name: its key, or its ``art`` when the key is too long for a Windows wheel path."""
    return {key: art or key for key, _, art in _CARD.findall((ADMIN / "admin.js").read_text(encoding="utf-8"))}


@pytest.mark.parametrize("key", list(GAMES))
def test_every_game_has_a_card_and_its_art(key: str) -> None:
    """A game without a card falls onto the party shelf with a blank picture (la gallina loca, 2026-11-03)."""
    assert key in _cards(), f"add {key} to GAME_CARDS in static/admin/admin.js"
    art = _art()[key]
    assert (ADMIN / "games" / f"{art}.webp").is_file(), f"make static/admin/games/{art}.webp"


def test_the_kids_games_sit_on_the_little_ones_or_learning_shelf() -> None:
    """The kids' games (the ones "¿Otra vez?" switches between) are on "For little ones" or "Learning games"."""
    cards = _cards()
    assert all(cards.get(key) in ("little", "learning") for key in KIDS_GAMES)
    assert cards["numbers"] == "learning"  # learning games are their own category (Eduardo, 2026-11-09)
