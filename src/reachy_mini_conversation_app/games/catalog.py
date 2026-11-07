"""The games Reachy plays: their names in Spanish and English, and the words that start, pause and end them.

What matters in a request is the game's name, wherever it sits in a usual way of asking: "juguemos a los
colores", "vamos a jugar al juego de los colores", "let's play color hunt", "can we play the color game".
A request to play without a known name ("juguemos", "let's play a game") asks which game.

Stories (story_file.py) are told through the same host, in Spanish and Italian: "cuéntanos los tres cerditos",
"raccontaci i tre porcellini"; "cuéntanos un cuento" asks which one.
"""

import re
import difflib
import logging
from typing import Final
from dataclasses import field, dataclass
from collections.abc import Iterable

from reachy_mini_conversation_app.quick_commands import normalize, strip_politeness


logger = logging.getLogger(__name__)


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
    # "game", or "story" for a story told from a story file (asked about and listed apart from the games).
    kind: str = "game"
    # The languages it is played in.
    languages: tuple[str, ...] = ("es", "en")
    # Its names by language, when they tell the language apart (a story asked for in Italian is told in Italian).
    spoken: dict[str, frozenset[str]] = field(default_factory=dict)


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

GALLINA_LOCA: Final[GameInfo] = GameInfo(
    key="gallina",
    names=frozenset(
        {
            "la gallina loca",
            "gallina loca",
            "la gallina",
            "gallina",
            "la gallinita loca",
            "gallinita loca",
            "la gallinita",
            "gallinita",
            "el juego de la gallina",
            "el juego de la gallina loca",
            "la gaina loca",
            "gaina loca",
            "la gallina local",
            "papa caliente",
            "la papa caliente",
            "crazy hen",
            "the crazy hen",
            "crazy chicken",
            "the crazy chicken",
            "the chicken game",
            "chicken game",
            "hot potato",
        }
    ),  # fmt: skip
    title={"es": "a la gallina loca", "en": "crazy hen"},
    pitch={
        "es": "La gallina dice tu nombre y algo loco que hacer, y cuando cacarea... ¡penitencia!",
        "en": "The hen calls your name and something silly to do, and when she squawks... a forfeit!",
    },
    rules=(
        "A hot-potato game for small children with no camera: each child says their name at the start, then "
        "Reachy, a crazy hen with a secret timer, calls one by name with something silly to do (walk like a duck, "
        "touch your nose, roar like a lion) and a drawing of it; a pat on his head or '¡listo!' passes the turn. "
        "The music speeds up, and when the hen squawks '¡COCOROCÓ!' whoever's turn it is does a silly forfeit. "
        "Nobody loses. 'Pausa' pauses, 'se acabó el juego' ends it."
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


BALLPARK: Final[GameInfo] = GameInfo(
    key="ballpark",
    names=frozenset(
        {
            "ballpark",
            "el ballpark",
            "ball park",
            "bolpark",
            "bol park",
            "bolpar",
            "ballpark game",
            "mas o menos",
            "el mas o menos",
            "el de mas o menos",
            "los rangos",
            "el de los rangos",
            "rangos",
            "entre cuanto y cuanto",
            "the range game",
            "ranges",
            "more or less",
        }
    ),  # fmt: skip
    title={"es": "Ballpark", "en": "Ballpark"},
    pitch={
        "es": "No adivinen el número: escriban un rango donde estén seguros de que está. Gana el rango más chiquito "
        "que acierte. Cada quien juega desde su teléfono.",
        "en": "Don't guess the number: write a range you're sure it's in. The smallest right range wins. Everyone "
        "plays on their phone.",
    },
    rules=(
        "A range-guessing game for teams. Players scan the QR code on the e-ink screen with their phones, type a "
        "name and pick a team. Each question has a number for an answer; each team writes a range (from, to) it is "
        "sure holds it. A range that holds the answer scores 1, the smallest one that holds it scores 3, and when "
        "every team is right the widest scores 0. Once per game each team may Copy another team's range, Swap two "
        "other teams' ranges (three teams or more) or Double its points, in secret on the phone. One surprise "
        "question per game: an exact answer scores 5, or everybody's points count double. The first team to 15 "
        "points wins. Spanish or English. Silly facts and pop culture. 'Pausa' pauses, 'se acabó el juego' ends it."
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

DETECTIVES: Final[GameInfo] = GameInfo(
    key="detectives",
    names=frozenset(
        {
            "detectives",
            "los detectives",
            "detective",
            "el detective",
            "juego de detectives",
            "el juego de detectives",
            "el caso",
            "caso",
            "un caso",
            "misterio",
            "un misterio",
            "el misterio",
            "detectivs",
            "detectiv",
            "the detectives",
            "detective game",
            "the detective game",
            "mystery",
            "a mystery",
            "mystery game",
            "the mystery game",
            "murder mystery",
            "a murder mystery",
            "whodunit",
            "who done it",
        }
    ),  # fmt: skip
    title={"es": "Detectives", "en": "Detectives"},
    pitch={
        "es": "Un misterio para resolver: yo hago de sospechosos y testigos, y ustedes investigan desde el teléfono.",
        "en": "A mystery to solve: I play the suspects and witnesses, and you investigate on your phones.",
    },
    rules=(
        "A mystery party game. Players scan the QR code on the e-ink screen with their phones and type a name; each "
        "phone gets a secret role. Reachy tells a pre-written case (a dinner-party mystery for adults, 'Who ate the "
        "cookies?' in kids mode); in turns each player questions a suspect or witness aloud, whom Reachy plays, or "
        "examines a clue shown on the screen. After a few rounds, or when most vote to, everyone accuses in secret on "
        "the phone and Reachy reveals the truth step by step. About 30 to 45 minutes; kids 15 to 20. Spanish or "
        "English. Needs phones on the home Wi-Fi. 'Pausa' pauses, 'se acabó el juego' ends it."
    ),
)


def _story_infos() -> list[GameInfo]:
    """Return a GameInfo for each story file that reads; a broken one is logged and left out."""
    from reachy_mini_conversation_app.games.story_file import LANGUAGES, STORIES_DIR, StoryError, load_story

    infos = []
    for path in sorted(STORIES_DIR.glob("*/story.yaml")):
        try:
            story = load_story(path)
        except StoryError as error:
            logger.warning("Story left out: %s", error)
            continue
        spoken = {language: frozenset(story.names[language]) for language in LANGUAGES}
        infos.append(
            GameInfo(
                key=story.key,
                names=frozenset().union(*spoken.values()),
                title={**story.title, "en": story.title["es"]},
                pitch={**story.pitch, "en": story.pitch["es"]},
                rules=story.rules
                or f"A story Reachy tells ({story.title['es']}), with moments where the children join in.",
                kind="story",
                languages=LANGUAGES,
                spoken=spoken,
            )
        )
    return infos


GAMES: Final[dict[str, GameInfo]] = {
    game.key: game
    for game in (
        COLOR_HUNT,
        TREASURE_HUNT,
        SIMON_SAYS,
        GALLINA_LOCA,
        BIG_BRAIN,
        BIG_BRAIN_HP,
        BIG_BRAIN_LOTR,
        BIG_BRAIN_SW,
        BIG_BRAIN_DISNEY,
        SCRIBBLE_RUSH,
        SHEEPLE,
        BALLPARK,
        DETECTIVES,
        *_story_infos(),
    )
}
STORIES: Final[tuple[str, ...]] = tuple(key for key, game in GAMES.items() if game.kind == "story")


@dataclass(frozen=True)
class GameRequest:
    """A request to play: ``game`` is None when no known game was named (Reachy asks which)."""

    game: GameInfo | None
    language: str
    # Asked for a story without saying which ("cuéntanos un cuento"): Reachy asks which story.
    story: bool = False


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
# "Cuéntanos…", "raccontaci…": the frame around a story's name; "un cuento" / "una storia" alone asks which.
_STORY_FRAMES: Final[dict[str, re.Pattern[str]]] = {
    "es": re.compile(
        r"^(?:cuentame|cuentanos|cuentenos|cuentanme|me cuentas|nos cuentas|leeme|leenos|puedes contarme|"
        r"puedes contarnos|quiero|queremos|ponme|ponnos)"
        r"(?: (?:un|una|el|la|los|las|otro|otra))?(?P<noun> cuento| cuentico| historia)?(?: de| del)?"
        r"(?P<name>(?: .*)?)$"
    ),
    "it": re.compile(
        r"^(?:raccontami|raccontaci|mi racconti|ci racconti|leggimi|leggici|voglio|vogliamo|un altra|unaltra)"
        r"(?: (?:una|un|la|il|lo|i|gli|le|unaltra|un altra|unaltro))?(?P<noun> storia| storiella| favola| fiaba)?"
        r"(?: di| dei| della| del| degli)?(?P<name>(?: .*)?)$"
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
     "chevere", "si va", "dale pues", "fino", "va pues", "si cuentalo", "si cuentanos",
     "dai", "va bene", "certo", "si dai", "si grazie", "andiamo", "si raccontala", "si per favore",
     "yes", "yeah", "yep", "sure", "ok", "okay", "yes please", "lets go", "lets play", "sure lets play"}
)  # fmt: skip
NO_WORDS: Final[frozenset[str]] = frozenset(
    {"no", "no gracias", "ahora no", "ahorita no", "luego", "despues", "mas tarde", "otro dia", "no quiero", "nope",
     "no thanks", "no grazie", "non ora", "dopo", "piu tardi", "un altro giorno", "non voglio",
     "not now", "later", "maybe later"}
)  # fmt: skip
PAUSE_WORDS: Final[frozenset[str]] = frozenset(
    {"pausa", "pausa el juego", "espera", "esperate", "un momento", "un momentico", "ya va", "para el juego",
     "pausa el cuento", "para el cuento", "aspetta", "aspettate", "un attimo", "un momentino", "metti in pausa",
     "ferma la storia",
     "pause", "pause the game",
     "wait", "hold on", "time out"}
)  # fmt: skip
RESUME_WORDS: Final[frozenset[str]] = frozenset(
    {"seguimos", "sigue", "seguir", "sigamos", "continua", "continuamos", "sigue jugando", "seguimos jugando",
     "sigue el cuento", "continua el cuento", "riprendi", "andiamo avanti", "vai avanti", "continuiamo",
     "continua la storia",
     "otra vez a jugar", "continue", "keep playing", "go on", "resume", "lets keep playing", "unpause"}
)  # fmt: skip
# A bare "para" or "stop" is shouted in games; ending needs the game named or "terminar".
QUIT_WORDS: Final[frozenset[str]] = frozenset(
    {"se acabo el juego", "termina el juego", "terminar el juego", "terminamos", "termina", "terminar",
     "ya no quiero jugar", "no quiero jugar mas", "ya no quiero jugar mas", "fin del juego", "acaba el juego",
     "se acabo el cuento", "termina el cuento", "ya no quiero el cuento", "ya no quiero mas cuento",
     "abbiamo finito", "basta storia", "basta con la storia", "finisci la storia", "fine della storia",
     "non voglio piu la storia", "stop alla storia",
     "stop the game", "end the game", "game over", "quit the game", "quit", "were done", "we are done",
     "i dont want to play anymore", "no more games"}
)  # fmt: skip


def words(text: str) -> str:
    """Normalize a transcript for the word sets: lowercase, no accents or punctuation, no "please"."""
    return strip_politeness(normalize(text))


# The kids' games: "¿Otra vez?" at the end of one can go on to another by name ("no, ¡el tesoro!").
KIDS_GAMES: Final[tuple[str, ...]] = ("colors", "treasure", "simon", "gallina")
# again_answer's other answers: stop playing, or another game without saying which.
STOP: Final[str] = "stop"
ANOTHER_GAME: Final[str] = "another game"
# The answer to "¿Otra vez?" is read loosely: children say more than "sí" (2026-11-02: a yes, then the color).
# A cue counts anywhere in the answer, as whole words; when a yes and a no are both there, the first one wins.
_AGAIN_YES: Final[frozenset[str]] = frozenset(
    {"si", "sii", "siii", "dale", "vamos", "claro", "vale", "venga", "bueno", "listo", "quiero", "otra", "otro",
     "yes", "yeah", "yep", "yup", "yay", "sure", "ok", "okay", "again", "more", "another", "lets go"}
)  # fmt: skip
_AGAIN_NO: Final[frozenset[str]] = frozenset(
    {"no", "nope", "nah", "ya no", "basta", "se acabo", "terminamos", "termina", "chao", "adios", "luego",
     "despues", "otro dia", "stop", "enough", "bye", "later", "were done", "we are done"}
)  # fmt: skip
_ANOTHER_GAME: Final[frozenset[str]] = frozenset(
    {"otro juego", "otra cosa", "algo diferente", "algo distinto", "otro diferente", "otro distinto",
     "another game", "different game", "something else", "a different one"}
)  # fmt: skip


def _first_at(said: str, phrases: Iterable[str]) -> int | None:
    """Return where the first of ``phrases`` starts in ``said`` (as whole words), or None."""
    padded = f" {said} "
    found = [padded.find(f" {phrase} ") for phrase in phrases]
    return min((at for at in found if at >= 0), default=None)


def again_answer(text: str, playing: str) -> str | None:
    """Read the answer to "¿Otra vez?" at the end of kids' game ``playing``.

    Return the game to play next (``playing`` again, or another kids' game named anywhere in the answer), STOP,
    ANOTHER_GAME (another game, not named), or None when the answer says none of these.
    """
    said = normalize(text)
    named = [(at, key) for key in KIDS_GAMES if (at := _first_at(said, GAMES[key].names)) is not None]
    if named:
        return min(named)[1]
    if _first_at(said, _ANOTHER_GAME) is not None:
        return ANOTHER_GAME
    yes, no = _first_at(said, _AGAIN_YES), _first_at(said, _AGAIN_NO)
    if yes is not None and (no is None or yes < no):
        return playing
    return STOP if no is not None else None


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
    story = _story_request(said)
    if story is not None:
        return story
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
    if game is None:
        return None
    return GameRequest(game, spoken_language(game, said) or guess_language(said))


def _story_request(said: str) -> GameRequest | None:
    """Return a request for a story ("cuéntanos los tres cerditos"), or None when ``said`` is not one."""
    for language, frame in _STORY_FRAMES.items():
        match = frame.match(said)
        if match is None:
            continue
        name = match.group("name").strip()
        game = find_game(name) if name else None
        if game is not None and game.kind == "story":
            return GameRequest(game, spoken_language(game, name) or language)
        # "Cuéntanos un cuento" asks which; "quiero agua" or "cuéntame un chiste" is not about stories.
        if match.group("noun") and (not name or name in _NO_NAME):
            return GameRequest(None, language, story=True)
    return None


def spoken_language(game: GameInfo, name: str) -> str | None:
    """Return the language whose names for ``game`` hold ``name`` (normalized), or None when none tells."""
    name = _NAME_EDGES.sub("", name.strip()).strip()
    for language, names in game.spoken.items():
        if name in names or difflib.get_close_matches(name, list(names), n=1, cutoff=0.82):
            return language
    return None


def guess_language(said: str) -> str:
    """Spanish unless the words are clearly English."""
    english = {"the", "lets", "play", "game", "color", "colour", "colors", "hunt", "can", "we"}
    return "en" if english & set(said.split()) else "es"
