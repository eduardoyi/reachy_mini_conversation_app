"""Tests for Big Brain's fixed host lines: both languages, known audio tags, nothing the voice would read aloud."""

import re

from reachy_mini_conversation_app.games.big_brain_lines import TAGS, LINES, TEAMS, shown, tags_in


def test_every_moment_has_takes_in_both_languages() -> None:
    """Each moment can be said in English and in Spanish, with at least two takes for the common ones."""
    for moment, takes in LINES.items():
        assert set(takes) == {"en", "es"}, moment
        assert all(takes[language] for language in ("en", "es")), moment
    for moment in ("correct", "wrong", "pick", "answer"):
        assert min(len(LINES[moment]["en"]), len(LINES[moment]["es"])) >= 6


def test_lines_use_only_known_tags_in_square_brackets() -> None:
    """A typo in a tag, or a tag in round or curly brackets, would be read aloud by the voice."""
    for moment, takes in LINES.items():
        for line in (*takes["en"], *takes["es"]):
            assert set(tags_in(line)) <= TAGS, (moment, line)
            assert not re.search(r"[(){}]", line), (moment, line)
            assert line.count("[") == line.count("]"), (moment, line)


def test_shown_text_has_no_tags() -> None:
    """The screen and phones get plain text."""
    assert shown("[booming] Welcome... [pause] to BIG BRAIN!") == "Welcome... to BIG BRAIN!"
    assert shown("[gasps] [booming] ¡Doble o nada!") == "¡Doble o nada!"
    for takes in LINES.values():
        assert all("[" not in shown(line) for line in (*takes["en"], *takes["es"]))


def test_each_team_has_a_buzz_and_a_win() -> None:
    """A buzz calls out the team and a win crowns it, in both languages; a Freeze or a Hijack names its target."""
    for team, (english, spanish) in TEAMS.items():
        assert any(spanish in line for line in LINES[f"buzz_{team}"]["es"])
        assert any(spanish.upper() in line for line in LINES[f"winner_{team}"]["es"])
        for card in ("freeze", "hijack"):
            assert all(spanish in line.lower() for line in LINES[f"{card}_{team}"]["es"])
            assert all(english in line.lower() for line in LINES[f"{card}_{team}"]["en"])
