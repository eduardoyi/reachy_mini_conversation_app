"""Every game has its Games-tab card: an entry in admin.js on a shelf, and its art (AGENTS.md, "Adding a game")."""

import re
from pathlib import Path

import pytest

from reachy_mini_conversation_app.games.catalog import GAMES, KIDS_GAMES


ADMIN = Path(__file__).resolve().parents[2] / "src" / "reachy_mini_conversation_app" / "static" / "admin"
_CARD = re.compile(r'^\s+(\w+): \{ name: "[^"]+", shelf: "(\w+)"', re.MULTILINE)


def _cards() -> dict[str, str]:
    return dict(_CARD.findall((ADMIN / "admin.js").read_text(encoding="utf-8")))


@pytest.mark.parametrize("key", list(GAMES))
def test_every_game_has_a_card_and_its_art(key: str) -> None:
    """A game without a card falls onto the party shelf with a blank picture (la gallina loca, 2026-11-03)."""
    assert key in _cards(), f"add {key} to GAME_CARDS in static/admin/admin.js"
    assert (ADMIN / "games" / f"{key}.webp").is_file(), f"make static/admin/games/{key}.webp"


def test_the_kids_games_sit_on_the_little_ones_shelf() -> None:
    """The kids' games (the ones "¿Otra vez?" switches between) are all on the "For little ones" shelf."""
    cards = _cards()
    assert all(cards.get(key) == "little" for key in KIDS_GAMES)
