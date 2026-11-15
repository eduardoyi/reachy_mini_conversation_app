"""The card each phone shows when a phone game ends: the player's name, how they did and the game, to save and share.

A game puts ``card`` in a joined phone's state once it is over; ``static/endcard.js`` draws it as a picture with a
save (and, over https, share) button. Words only: no scores of other players, no photos.
"""

from typing import Any, Mapping, Sequence

from reachy_mini_conversation_app.games.big_brain_phones import Player


_WON: dict[str, str] = {"es": "¡Campeones!", "en": "Champions!"}
_TIED: dict[str, str] = {"es": "¡Empate en lo más alto!", "en": "Tied for first!"}


def _place(place: int, language: str) -> str:
    if language == "es":
        return f"{place}.º puesto"
    suffix = {1: "st", 2: "nd", 3: "rd"}.get(place if place < 20 else place % 10, "th")
    return f"{place}{suffix} place"


def _card(game: str, player: Player, title: str, line: str, *, team: str | None, won: bool) -> dict[str, Any]:
    return {"game": game, "name": player.name, "title": title, "line": line, "team": team, "won": won}


def team_card(
    game: str,
    language: str,
    player: Player,
    teams: Sequence[Mapping[str, Any]],
    *,
    value: str = "score",
    unit: Mapping[str, str] | None = None,
    winners: Sequence[str] | None = None,
) -> dict[str, Any] | None:
    """Return ``player``'s card from the game's ``teams`` (each with ``key``, ``name`` and ``value``).

    ``winners`` (team keys) overrides the highest ``value`` when the game decides its winner another way.
    """
    mine = next((team for team in teams if team["key"] == player.team), None)
    if mine is None:
        return None
    points = int(mine[value])
    level = sum(1 for team in teams if int(team[value]) == points)
    won = player.team in winners if winners is not None else all(points >= int(team[value]) for team in teams)
    if won:
        title = _TIED[language] if winners is None and level > 1 else _WON[language]
    else:
        title = _place(1 + sum(1 for team in teams if int(team[value]) > points), language)
    words = unit or {"es": "puntos", "en": "points"}
    return _card(game, player, title, f"{mine['name']} · {points} {words[language]}", team=player.team, won=won)


def case_card(game: str, language: str, player: Player, *, solved: bool, culprit: str) -> dict[str, Any]:
    """Return ``player``'s Detectives card: whether they named the culprit."""
    if language == "es":
        title, line = ("¡Caso resuelto!", f"Culpable: {culprit}") if solved else ("Se te escapó", f"Era {culprit}")
    else:
        title, line = ("Case solved!", f"Culprit: {culprit}") if solved else ("It got away", f"It was {culprit}")
    return _card(game, player, title, line, team=None, won=solved)
