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

TREASURE_HUNT: Final[GameInfo] = GameInfo(
    key="treasure",
    names=frozenset(
        {
            "buscar tesoros",
            "busca tesoros",
            "tesoros",
            "tesoro",
            "el tesoro",
            "los tesoros",
            "busca el tesoro",
            "buscar el tesoro",
            "la busqueda del tesoro",
            "busqueda del tesoro",
            "el juego del tesoro",
            "juego del tesoro",
            "cazar tesoros",
            "caza del tesoro",
            "la caza del tesoro",
            "treasure hunt",
            "the treasure hunt",
            "treasure",
            "treasure game",
            "the treasure game",
            "scavenger hunt",
            "a scavenger hunt",
            "a treasure hunt",
            "treasure hunting",
            "treasure hunts",
        }
    ),  # fmt: skip
    title={"es": "a buscar tesoros", "en": "treasure hunt"},
    pitch={
        "es": "Yo te digo algo de la casa, y tú lo buscas y me lo enseñas.",
        "en": "I name something from around the house, and you find it and show it to me.",
    },
    rules=(
        "Reachy names something from around the house (a ball, a shoe, a Lego, an animal, a fruit, something "
        "round...) and shows a drawing of it; a tune speeds up for 60 seconds while the children find it and hold "
        "it up to his camera. He cheers when he sees it and says 'not that one' when it is something else. Five "
        "rounds, a star for each find; nobody loses. 'Pausa' pauses, 'se acabó el juego' ends it."
    ),
)

SIMON_SAYS: Final[GameInfo] = GameInfo(
    key="simon",
    names=frozenset(
        {
            "simon dice",
            "simon",
            "el simon dice",
            "el juego de simon dice",
            "simon dices",
            "simone dice",
            "reachy dice",
            "richie dice",
            "ritchie dice",
            "simon says",
            "simon said",
            "the simon says game",
            "reachy says",
            "richie says",
        }
    ),  # fmt: skip
    title={"es": "a Simón dice", "en": "Simon says"},
    pitch={
        "es": "Yo te digo una pose, tú la haces y te quedas quieto.",
        "en": "I tell you a pose, you do it and hold still.",
    },
    rules=(
        "Reachy says 'Simón dice: ¡tócate la nariz!' (or another still pose: arms up, cover your eyes, thumbs up, "
        "bunny ears, crouch down...) and shows a drawing of it; the children do it and hold still while his camera "
        "checks. A star for each pose done, eight poses a game; nobody loses. Whole-body poses come after 'da un "
        "paso hacia atrás'. 'Pausa' pauses, 'se acabó el juego' ends it."
    ),
)

BIG_BRAIN: Final[GameInfo] = GameInfo(
    key="bigbrain",
    names=frozenset(
        {
            "big brain",
            "el big brain",
            "bigbrain",
            "big brains",
            "big brein",
            "big bren",
            "big brian",
            "vic brain",
            "big rain",
            "big drain",
            "the big brain",
            "big brain game",
            "trivia",
            "la trivia",
            "una trivia",
            "el juego de preguntas",
            "juego de preguntas",
            "preguntas",
            "las preguntas",
            "el concurso",
            "concurso",
            "quiz",
            "a quiz",
            "the quiz",
            "trivia game",
            "quiz show",
        }
    ),  # fmt: skip
    title={"es": "Big Brain", "en": "Big Brain"},
    pitch={
        "es": "Un concurso de preguntas por equipos: cada quien juega desde su teléfono.",
        "en": "A team quiz show: everyone plays on their phone.",
    },
    rules=(
        "A quiz show for teams. Players scan the QR code on the e-ink screen with their phones, type a name and pick "
        "a team (red, blue, green, yellow); phones are buzzers. Five themed columns of clues worth 200 to 800 "
        "points: the team in control picks a square on a phone, Reachy reads the clue in his game-show host voice, "
        "the first team to buzz answers out loud. Right wins the points and the next pick; wrong loses them. Hidden "
        "squares (double or nothing, a steal, everyone answers), then a final round with secret bets. About 30 to "
        "40 minutes. Spanish or English. Needs phones on the home Wi-Fi. 'Pausa' pauses, 'se acabó el juego' ends it."
    ),
)


SHEEPLE: Final[GameInfo] = GameInfo(
    key="sheeple",
    names=frozenset(
        {
            "sheeple",
            "el sheeple",
            "sheeple game",
            "shipul",
            "shipol",
            "shiple",
            "sheep pull",
            "sheep people",
            "she pull",
            "las encuestas",
            "encuestas",
            "la encuesta",
            "encuesta",
            "survey",
            "surveys",
            "the survey",
            "survey says",
        }
    ),  # fmt: skip
    title={"es": "Sheeple", "en": "Sheeple"},
    pitch={
        "es": "Adivinen qué contestó la encuesta: gana quien piensa como el rebaño. Cada quien juega desde su teléfono.",
        "en": "Guess what the survey said: whoever thinks like the flock wins. Everyone plays on their phone.",
    },
    rules=(
        "A survey game for teams. Players scan the QR code on the e-ink screen with their phones, type a name and "
        "pick a team. Each board is a question asked to 100 people with its answers hidden, ranked by how many said "
        "them. Flock rounds: everyone types a guess on the phone, Reachy reveals the answers from least to most "
        "popular and each guess scores the people who gave that answer. Team rounds: teams take turns, one player "
        "taps the phone and says the guess aloud; a hit scores, a miss passes the turn, six misses end the board. "
        "Six boards, the last one double, then the winning team. Spanish or English (the surveys were asked in the "
        "US). Kids mode leaves out adult and US-only questions. 'Pausa' pauses, 'se acabó el juego' ends it."
    ),
)


def _edition_names(subjects: tuple[str, ...]) -> frozenset[str]:
    """Return the ways to ask for a Big Brain edition: "big brain harry potter", "trivia de hogwarts"..."""
    shows = ("big brain", "bigbrain", "big brains", "big brein", "big brian", "el big brain")
    quizzes = ("trivia", "la trivia", "quiz", "concurso", "el concurso", "juego de preguntas", "preguntas")
    names: set[str] = set()
    for subject in subjects:
        for show in shows:
            names |= {
                f"{show} {subject}",
                f"{show} de {subject}",
                f"{show} edicion {subject}",
                f"{show} edicion de {subject}",
                f"{show} {subject} edition",
                f"{subject} {show}",
            }
        for quiz in quizzes:
            names |= {f"{quiz} de {subject}", f"{quiz} {subject}", f"{subject} {quiz}"}
    return frozenset(names)


BIG_BRAIN_HP: Final[GameInfo] = GameInfo(
    key="bigbrain_hp",
    names=_edition_names(("harry potter", "hogwarts", "harry poter", "hari potter", "jari poter")),
    title={"es": "Big Brain, edición Hogwarts", "en": "Big Brain, Hogwarts edition"},
    pitch={
        "es": "El concurso de Big Brain, todo sobre las películas de Harry Potter: cada quien juega desde su teléfono.",
        "en": "The Big Brain quiz show, all about the Harry Potter films: everyone plays on their phone.",
    },
    rules=(
        "Big Brain's Hogwarts edition: the same team quiz show, with every clue about the eight Harry Potter films "
        "(story, characters, spells, creatures, the cast and how the films were made). Start it when someone asks "
        "for Big Brain with Harry Potter or Hogwarts; plain Big Brain is the general quiz."
    ),
)

BIG_BRAIN_LOTR: Final[GameInfo] = GameInfo(
    key="bigbrain_lotr",
    names=_edition_names(
        (
            "el senor de los anillos",
            "senor de los anillos",
            "lord of the rings",
            "the lord of the rings",
            "lotr",
            "la tierra media",
            "tierra media",
            "middle earth",
            "el senor de los anillo",
            "lord of the ring",
        )
    ),  # fmt: skip
    title={"es": "Big Brain, edición Tierra Media", "en": "Big Brain, Middle-earth edition"},
    pitch={
        "es": "El concurso de Big Brain, todo sobre las películas de El Señor de los Anillos: cada quien juega desde su teléfono.",  # fmt: skip
        "en": "The Big Brain quiz show, all about the Lord of the Rings films: everyone plays on their phone.",
    },
    rules=(
        "Big Brain's Middle-earth edition: the same team quiz show, with every clue about Peter Jackson's three "
        "Lord of the Rings films (story, characters, places, battles, the cast and how the films were made). Start "
        "it when someone asks for Big Brain with the Lord of the Rings or Middle-earth; plain Big Brain is the "
        "general quiz."
    ),
)

BIG_BRAIN_SW: Final[GameInfo] = GameInfo(
    key="bigbrain_sw",
    names=_edition_names(
        (
            "star wars",
            "starwars",
            "la guerra de las galaxias",
            "guerra de las galaxias",
            "star war",
            "estar wars",
            "los jedi",
            "jedi",
            "la galaxia",
            "galactica",
            "galactic",
        )
    ),  # fmt: skip
    title={"es": "Big Brain, edición Galáctica", "en": "Big Brain, Galactic edition"},
    pitch={
        "es": "El concurso de Big Brain, todo sobre las seis películas de Star Wars: cada quien juega desde su teléfono.",  # fmt: skip
        "en": "The Big Brain quiz show, all about the six Star Wars films: everyone plays on their phone.",
    },
    rules=(
        "Big Brain's Galactic edition: the same team quiz show, with every clue about the six Star Wars saga films "
        "(the original trilogy and the prequels: characters, planets, ships, the Force, the cast and how the films "
        "were made). Start it when someone asks for Big Brain with Star Wars; plain Big Brain is the general quiz."
    ),
)

BIG_BRAIN_DISNEY: Final[GameInfo] = GameInfo(
    key="bigbrain_disney",
    names=_edition_names(("disney", "de disney", "disney pixar", "disney y pixar", "pixar", "disni", "dizni")),
    title={"es": "Big Brain, edición Disney", "en": "Big Brain, Disney edition"},
    pitch={
        "es": "El concurso de Big Brain, todo sobre las películas animadas de Disney y Pixar: cada quien juega desde su teléfono.",  # fmt: skip
        "en": "The Big Brain quiz show, all about Disney and Pixar's animated films: everyone plays on their phone.",
    },
    rules=(
        "Big Brain's Disney edition: the same team quiz show, with every clue about Disney and Pixar's animated "
        "films (heroines, villains, sidekicks, songs, places, the voices and how the films were made). Start it "
        "when someone asks for Big Brain with Disney or Pixar; plain Big Brain is the general quiz."
    ),
)

SCRIBBLE_RUSH: Final[GameInfo] = GameInfo(
    key="scribble",
    names=frozenset(
        {
            "scribble rush",
            "scribble",
            "scribbles rush",
            "scribble rash",
            "scribble brush",
            "scrible rush",
            "skribble rush",
            "scribble russ",
            "escribel rush",
            "escribol rush",
            "escrible rush",
            "dibujo relampago",
            "el dibujo relampago",
            "dibujos relampago",
            "dibujo rapido",
            "dibujar",
            "a dibujar",
            "dibujos",
            "los dibujos",
            "drawing",
            "drawing game",
            "the drawing game",
            "draw",
            "pictionary",
        }
    ),  # fmt: skip
    title={"es": "Scribble Rush", "en": "Scribble Rush"},
    pitch={
        "es": "Ustedes dibujan una palabra secreta a toda prisa y yo trato de adivinarla: cada quien juega desde su teléfono.",  # fmt: skip
        "en": "You draw a secret word in a rush and I try to guess it: everyone plays on their phone.",
    },
    rules=(
        "A drawing race for two or three teams. Players scan the QR code on the e-ink screen, type a name and pick "
        "a team. On a team's turn one player sees a secret word on their phone and draws it on paper in 20 seconds "
        "while a tick-tock speeds up, then holds it up to Reachy's camera; Reachy makes up to three guesses aloud "
        "and the earlier he gets it, the more squares the team moves on the track (24 squares, six rounds). Kids "
        "mode has easy words and a square for every try. Needs the camera and phones on the home Wi-Fi. 'Pausa' "
        "pauses, 'se acabó el juego' ends it."
    ),
)

GAMES: Final[dict[str, GameInfo]] = {
    game.key: game
    for game in (
        COLOR_HUNT,
        TREASURE_HUNT,
        SIMON_SAYS,
        BIG_BRAIN,
        BIG_BRAIN_HP,
        BIG_BRAIN_LOTR,
        BIG_BRAIN_SW,
        BIG_BRAIN_DISNEY,
        SCRIBBLE_RUSH,
        SHEEPLE,
    )
}


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
    {"si", "si si", "vale", "dale", "claro", "bueno", "venga", "vamos", "si vamos", "si quiero", "si porfa", "listo",
     "chevere", "si va", "dale pues", "fino", "va pues",
     "yes", "yeah", "yep", "sure", "ok", "okay", "yes please", "lets go", "lets play", "sure lets play"}
)  # fmt: skip
NO_WORDS: Final[frozenset[str]] = frozenset(
    {"no", "no gracias", "ahora no", "ahorita no", "luego", "despues", "mas tarde", "otro dia", "no quiero", "nope",
     "no thanks",
     "not now", "later", "maybe later"}
)  # fmt: skip
PAUSE_WORDS: Final[frozenset[str]] = frozenset(
    {"pausa", "pausa el juego", "espera", "esperate", "un momento", "un momentico", "ya va", "para el juego",
     "pause", "pause the game",
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
