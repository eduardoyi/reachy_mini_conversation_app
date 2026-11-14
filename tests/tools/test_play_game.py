"""Tests for play_game: Gemini reads a game's rules (and asks) before it may start it."""

from typing import Any

import pytest

from reachy_mini_conversation_app.tools import play_game
from reachy_mini_conversation_app.tools.play_game import PlayGame
from reachy_mini_conversation_app.tools.core_tools import ToolDependencies


class _Host:
    def __init__(self) -> None:
        self.started: list[tuple[str, str]] = []

    def start(self, key: str, language: str, kids: bool = False) -> str | None:
        self.started.append((key, language))
        return None


def _deps(host: Any) -> ToolDependencies:
    deps = ToolDependencies(reachy_mini=None, movement_manager=None)
    deps.games = host
    return deps


@pytest.mark.asyncio
async def test_start_needs_the_rules_first() -> None:
    """Starting without the rules is refused; after them, the game starts and the conversation closes by itself."""
    host = _Host()
    tool = PlayGame()
    closes: list[bool] = []
    deps = _deps(host)
    deps.end_conversation = lambda goodbye=True: closes.append(goodbye) or {"status": "ending"}
    refused = await tool(deps, game="colors", action="start")
    assert "rules first" in refused["error"] and host.started == []
    rules = await tool(deps, game="colors", action="rules", language="es")
    # Only the short question: no summary of the game (2026-11-14).
    assert "'¿Jugamos a los colores?'" in rules["ask"] and "Yo digo un color" not in rules["ask"]
    assert "red" in rules["rules"]
    started = await tool(deps, game="colors", action="start", language="es")
    assert started["status"] == "started" and "no goodbye" in started["note"]
    assert host.started == [("colors", "es")] and closes == [False]
    again = await tool(_deps(host), game="colors", action="start")
    assert "error" in again  # each start needs its own rules and question


@pytest.mark.asyncio
async def test_rules_read_long_ago_do_not_count(monkeypatch: pytest.MonkeyPatch) -> None:
    """Rules from an earlier conversation do not let a later one start the game without asking."""
    host = _Host()
    tool = PlayGame()
    now = [100.0]
    monkeypatch.setattr(play_game.time, "monotonic", lambda: now[0])
    await tool(_deps(host), game="colors", action="rules")
    now[0] += play_game.RULES_VALID_S + 1
    assert "error" in await tool(_deps(host), game="colors", action="start")


@pytest.mark.asyncio
async def test_unknown_games_and_no_host() -> None:
    """An unknown game lists the games; without the game host nothing starts."""
    tool = PlayGame()
    unknown = await tool(_deps(_Host()), game="chess", action="rules")
    assert unknown["games"] == [
        "colors",
        "treasure",
        "simon",
        "gallina",
        "numbers",
        "bigbrain",
        "bigbrain_hp",
        "bigbrain_lotr",
        "bigbrain_sw",
        "bigbrain_disney",
        "scribble",
        "sheeple",
        "ballpark",
        "detectives",
        "cenicienta",
        "donde_esta_bigotes",
        "el_cascanueces",
        "la_befana",
        "la_gran_carrera",
        "la_monedita_de_plata",
        "musicos_de_bremen",
        "nabo_gigante",
        "ricitos_de_oro",
        "tres_cerditos",
    ]
    missing = await tool(_deps(None), game="colors", action="rules")
    assert "not available" in missing["error"]


def test_the_description_names_every_game_in_both_languages() -> None:
    """Gemini sees each game's key and both names at all times (it is not folded into a skill)."""
    assert "colors (color hunt, 'a los colores')" in PlayGame.description
