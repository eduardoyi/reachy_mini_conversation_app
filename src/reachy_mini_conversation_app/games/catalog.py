"""The games Reachy plays: their names in Spanish and English, and the words that start, pause and end them.

What matters in a request is the game's name, wherever it sits in a usual way of asking: "juguemos a los
colores", "vamos a jugar al juego de los colores", "let's play color hunt", "can we play the color game".
A request to play without a known name ("juguemos", "let's play a game") asks which game.
"""

import re
import difflib
from typing import Final
from dataclasses import dataclass

from reachy_mini_conversation_app.quick_commands import normalize, strip_politeness


@dataclass(frozen=True)
class GameInfo:
    """One game: how people call it, and what Reachy says about it."""

    key: str
    # Normalized names (no accents or punctuation), mishearings included.
    names: frozenset[str]
    # "a los colores" as in "¿Jugamos a los colores?"; "color hunt" as in "Shall we play color hunt?".
    title: dict[str, str]
    # One sentence on how it is played, said with the start question.
    pitch: dict[str, str]
    # For Gemini (play_game rules): how it goes, in English.
    rules: str


COLOR_HUNT: Final[GameInfo] = GameInfo(
    key="colors",
    names=frozenset(
        {
            "los colores",
            "colores",
            "el juego de los colores",
            "juego de los colores",
            "juego de colores",
            "busca colores",
            "buscar colores",
            "caza de colores",
            "los colore",
            "colore",
            "color hunt",
            "colour hunt",
            "color game",
            "colour game",
            "the color game",
            "colors",
            "colours",
            "color hunting",
            "the color hunt",
            "collar hunt",
            "color hand",
            "colour hand",
            "color hunts",
        }
    ),  # fmt: skip
    title={"es": "a los colores", "en": "color hunt"},
    pitch={
        "es": "Yo digo un color y tú me traes algo de ese color.",
        "en": "I say a color, and you bring me something that color.",
    },
    rules=(
        "Reachy names a color (red, blue, yellow, green, pink, white or black) and a tune speeds up for 30 "
        "seconds while the children find something of that color and hold it up to his camera. He cheers when "
        "he sees it and says which color it is when it is another one. Five rounds, a star for each find; "
        "nobody loses. 'Pausa' pauses, 'se acabó el juego' ends it, his name pauses and asks."
    ),
)

GAMES: Final[dict[str, GameInfo]] = {game.key: game for game in (COLOR_HUNT,)}


@dataclass(frozen=True)
class GameRequest:
    """A request to play: ``game`` is None when no known game was named (Reachy asks which)."""

    game: GameInfo | None
    language: str


# "Juguemos a…", "¿podemos jugar al…?", "let's play…": the frame around the game's name.
_FRAMES: Final[dict[str, re.Pattern[str]]] = {
    "es": re.compile(
        r"^(?:juguemos|vamos a jugar|jugamos|quiero jugar|queremos jugar|podemos jugar|quieres jugar|"
        r"a jugar|juega conmigo|jugar|vamos a jugar un juego|juguemos un juego)"
        r"(?: (?:a|al|con|un rato a))?(?: (?:un|el|la|los|las))?(?: juego(?: de)?)?(?P<name>(?: .*)?)$"
    ),
    "en": re.compile(
        r"^(?:lets play|let us play|can we play|could we play|shall we play|i want to play|we want to play|"
        r"wanna play|want to play|do you want to play|play)"
        r"(?: (?:a|the))?(?: game(?: of)?)?(?P<name>(?: .*)?)$"
    ),
}
# Said instead of a name: no game picked yet.
_NO_NAME: Final[frozenset[str]] = frozenset(
    {"", "algo", "un juego", "juego", "contigo", "conmigo", "a game", "game", "something", "with me", "with you"}
)
_NAME_EDGES: Final[re.Pattern[str]] = re.compile(r"^(?:a |al |el |la |los |las |the )|(?: game| juego| please)$")

# Answers and commands, each a whole transcript (normalized, politeness stripped).
YES_WORDS: Final[frozenset[str]] = frozenset(
    {"si", "si si", "vale", "dale", "claro", "bueno", "venga", "vamos", "si vamos", "si quiero", "si porfa",
     "yes", "yeah", "yep", "sure", "ok", "okay", "yes please", "lets go", "lets play", "sure lets play"}
)  # fmt: skip
NO_WORDS: Final[frozenset[str]] = frozenset(
    {"no", "no gracias", "ahora no", "luego", "mas tarde", "otro dia", "no quiero", "nope", "no thanks",
     "not now", "later", "maybe later"}
)  # fmt: skip
PAUSE_WORDS: Final[frozenset[str]] = frozenset(
    {"pausa", "pausa el juego", "espera", "esperate", "un momento", "para el juego", "pause", "pause the game",
     "wait", "hold on", "time out"}
)  # fmt: skip
RESUME_WORDS: Final[frozenset[str]] = frozenset(
    {"seguimos", "sigue", "seguir", "sigamos", "continua", "continuamos", "sigue jugando", "seguimos jugando",
     "otra vez a jugar", "continue", "keep playing", "go on", "resume", "lets keep playing", "unpause"}
)  # fmt: skip
# A bare "para" or "stop" is shouted in games; ending needs the game named or "terminar".
QUIT_WORDS: Final[frozenset[str]] = frozenset(
    {"se acabo el juego", "termina el juego", "terminar el juego", "terminamos", "termina", "terminar",
     "ya no quiero jugar", "no quiero jugar mas", "ya no quiero jugar mas", "fin del juego", "acaba el juego",
     "stop the game", "end the game", "game over", "quit the game", "quit", "were done", "we are done",
     "i dont want to play anymore", "no more games"}
)  # fmt: skip


def words(text: str) -> str:
    """Normalize a transcript for the word sets: lowercase, no accents or punctuation, no "please"."""
    return strip_politeness(normalize(text))


def find_game(name: str) -> GameInfo | None:
    """Return the game ``name`` names (normalized), allowing for a close mishearing."""
    name = _NAME_EDGES.sub("", name.strip()).strip()
    for _ in range(2):  # "al juego de los colores": the article, then "juego de"
        name = re.sub(r"^(?:juego de |juego |game of )", "", name).strip()
    candidates = {known: game for game in GAMES.values() for known in game.names}
    if name in candidates:
        return candidates[name]
    close = difflib.get_close_matches(name, list(candidates), n=1, cutoff=0.82)
    return candidates[close[0]] if close else None


def parse_game_request(text: str) -> GameRequest | None:
    """Return the request to play in ``text``, or None when it is not one.

    "Play" alone in English starts music too ("play Coldplay"), so it counts only with a known game's name.
    """
    said = words(text)
    for language, frame in _FRAMES.items():
        match = frame.match(said)
        if match is None:
            continue
        name = match.group("name").strip()
        game = find_game(name)
        if game is not None:
            return GameRequest(game, language)
        if said.startswith("play"):
            return None
        # "Juguemos al escondite" asks which game; "let's play some music" is not about games.
        if name in _NO_NAME or language == "es":
            return GameRequest(None, language)
        return None
    # Just the name, of two words or more: "el juego de los colores", "color hunt" (not "colores" alone).
    game = find_game(said) if len(said.split()) >= 2 else None
    return GameRequest(game, guess_language(said)) if game is not None else None


def guess_language(said: str) -> str:
    """Spanish unless the words are clearly English."""
    english = {"the", "lets", "play", "game", "color", "colour", "colors", "hunt", "can", "we"}
    return "en" if english & set(said.split()) else "es"
