"""Tests for the game catalog: games found by name in Spanish and English, and the words that steer them."""

import pytest

from reachy_mini_conversation_app.games.catalog import (
    QUIT_WORDS,
    PAUSE_WORDS,
    words,
    find_game,
    parse_game_request,
)


@pytest.mark.parametrize(
    ("text", "language"),
    [
        ("Juguemos a los colores", "es"),
        ("vamos a jugar a los colores", "es"),
        ("¿Jugamos al juego de los colores?", "es"),
        ("podemos jugar a los colores por favor", "es"),
        ("quiero jugar a los colore", "es"),  # as Whisper writes it
        ("let's play color hunt", "en"),
        ("Let's play the color game", "en"),
        ("can we play colors?", "en"),
        ("play the color game", "en"),
        ("let's play collar hunt", "en"),
        ("el juego de los colores", "es"),
        ("color hunt", "en"),
    ],
)
def test_the_color_hunt_is_found_however_it_is_asked_for(text: str, language: str) -> None:
    """What matters is the game's name, in any usual way of asking and either language."""
    request = parse_game_request(text)
    assert request is not None and request.game is not None
    assert (request.game.key, request.language) == ("colors", language)


@pytest.mark.parametrize(
    ("text", "language"),
    [
        ("juguemos", "es"),
        ("vamos a jugar un juego", "es"),
        ("juguemos al escondite", "es"),
        ("let's play a game", "en"),
    ],
)
def test_a_request_to_play_without_a_known_game_asks_which(text: str, language: str) -> None:
    """No game named, or one Reachy does not know: he asks which game."""
    request = parse_game_request(text)
    assert request is not None and request.game is None and request.language == language


@pytest.mark.parametrize("text", ["play Coldplay", "let's play some music", "pon música", "colores", "¿qué hora es?"])
def test_music_and_other_requests_are_not_games(text: str) -> None:
    """Saying "Play" starts music unless a game is named; a bare "colores" is not enough to ask."""
    assert parse_game_request(text) is None


def test_a_bare_stop_is_not_quitting_but_naming_the_game_is() -> None:
    """Kids shout "¡para!" during games; ending takes "para el juego" (a pause) or "se acabó el juego"."""
    assert words("¡Para!") not in QUIT_WORDS | PAUSE_WORDS
    assert words("Para el juego") in PAUSE_WORDS
    assert words("¡Se acabó el juego!") in QUIT_WORDS
    assert words("Stop the game, please") in QUIT_WORDS


def test_a_close_mishearing_still_finds_the_game() -> None:
    """A name Whisper spelled a little differently still matches; something unrelated does not."""
    assert find_game("los coloress") is not None
    assert find_game("el escondite") is None
