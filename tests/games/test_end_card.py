from pathlib import Path

import pytest

from reachy_mini_conversation_app.games.end_card import case_card, team_card
from reachy_mini_conversation_app.games.big_brain_phones import Player


ANA = Player("p1", "Ana", "red", 0.0)
LUIS = Player("p2", "Luis", "blue", 0.0)
PAGES = Path(__file__).resolve().parents[2] / "src" / "reachy_mini_conversation_app" / "static"


def _teams(red: int, blue: int, green: int = 0) -> list[dict[str, object]]:
    return [
        {"key": "red", "name": "Rojo", "score": red},
        {"key": "blue", "name": "Azul", "score": blue},
        {"key": "green", "name": "Verde", "score": green},
    ]


def test_the_top_team_are_champions_and_the_rest_get_their_place() -> None:
    """The highest score wins; others see their place, ties sharing it; the line names the team and its points."""
    assert team_card("Big Brain", "es", ANA, _teams(2400, 1200)) == {
        "game": "Big Brain",
        "name": "Ana",
        "title": "¡Campeones!",
        "line": "Rojo · 2400 puntos",
        "team": "red",
        "won": True,
    }
    blue = team_card("Big Brain", "en", LUIS, _teams(2400, 1200, 1200))
    assert blue is not None and blue["title"] == "2nd place" and not blue["won"]
    tie = team_card("Big Brain", "es", ANA, _teams(900, 900))
    assert tie is not None and tie["title"] == "¡Empate en lo más alto!" and tie["won"]


def test_a_game_that_names_its_winners_and_counts_other_things() -> None:
    """Ballpark's winners override the scores; Scribble Rush counts squares, in its own words."""
    won = team_card("Ballpark", "es", LUIS, _teams(5, 3), winners=["blue"])
    assert won is not None and won["title"] == "¡Campeones!"
    squares = [{"key": "red", "name": "Rojo", "squares": 12}, {"key": "blue", "name": "Azul", "squares": 16}]
    card = team_card("Scribble Rush", "es", ANA, squares, value="squares", unit={"es": "casillas", "en": "squares"})
    assert card is not None and card["line"] == "Rojo · 12 casillas" and card["title"] == "2.º puesto"


def test_players_not_in_the_game_get_no_card() -> None:
    """A phone on a team that did not play has no card."""
    assert team_card("Big Brain", "es", Player("p3", "Eva", "yellow", 0.0), _teams(1, 2)) is None


def test_the_detectives_card_says_whether_they_named_the_culprit() -> None:
    """Solved or not, with the culprit's name; no team color."""
    solved = case_card("El último brindis", "es", ANA, solved=True, culprit="Julián Ferrer")
    assert (
        solved["title"] == "¡Caso resuelto!" and solved["line"] == "Culpable: Julián Ferrer" and solved["team"] is None
    )
    missed = case_card("The last toast", "en", LUIS, solved=False, culprit="Julián Ferrer")
    assert missed["title"] == "It got away" and not missed["won"]


@pytest.mark.parametrize("game", ["big_brain", "sheeple", "ballpark", "scribble", "detectives"])
def test_every_phone_game_page_loads_the_card_and_hands_it_each_state(game: str) -> None:
    """Each page loads endcard.js with its join code and passes every state to it."""
    page = (PAGES / game / "index.html").read_text()
    assert "'endcard.js' + location.search" in page and "RichieEndCard.update(m)" in page
