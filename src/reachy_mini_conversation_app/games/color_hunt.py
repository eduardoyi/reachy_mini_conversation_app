"""The color hunt ("juguemos a los colores"): find something of the color Reachy names and show it to him.

Jev is asked which main color the object held up is. Each color also accepts its neighbors, so a child is never
told off for a color in between: Reachy's camera shows red and hot pink as orange-ish (spike, 2026-10-31), and
the household chose generous matches (2026-10-31). A color is named as wrong only when Jev is sure of a color
outside the group. The rounds, the tune and the looking are the shared hunt (games/hunt.py).
"""

from typing import Any, Final

from reachy_mini_conversation_app.games.hunt import HELD_QUESTION, Hunt, Check, hunt_phrases
from reachy_mini_conversation_app.games.catalog import COLOR_HUNT


ROUND_S: Final[float] = 30.0
# Naming a wrong color needs a sure answer.
MIN_WRONG_P: Final[float] = 0.7

# The colors Reachy asks for: Spanish and English names, and the Jev answers that count as each.
TARGETS: Final[dict[str, tuple[str, str, frozenset[str]]]] = {
    "red": ("rojo", "red", frozenset({"red", "orange", "pink"})),
    "orange": ("naranja", "orange", frozenset({"orange", "red", "yellow"})),
    "yellow": ("amarillo", "yellow", frozenset({"yellow", "orange"})),
    "green": ("verde", "green", frozenset({"green"})),
    "blue": ("azul", "blue", frozenset({"blue", "purple"})),
    "purple": ("morado", "purple", frozenset({"purple", "pink", "blue"})),
    "pink": ("rosado", "pink", frozenset({"pink", "red", "orange"})),
    "brown": ("marrón", "brown", frozenset({"brown", "orange", "black"})),
    "white": ("blanco", "white", frozenset({"white", "gray"})),
    "black": ("negro", "black", frozenset({"black", "gray", "brown"})),
    "gray": ("gris", "gray", frozenset({"gray", "white", "black"})),
}
_QUESTIONS: Final[dict[str, dict[str, Any]]] = {
    "held": HELD_QUESTION,
    "color": {
        "type": "choice",
        "instructions": (
            "What is the main color of the object someone is holding up or showing to the camera in `frame`? "
            "Ignore the background, the person's skin and clothes."
        ),
        "criteria": {
            **{key: {"purple": "purple or violet", "gray": "gray or silver"}.get(key, key) for key in TARGETS},
            "none": "no object is being held up or shown to the camera",
        },
    },
}

_LINES: Final[dict[str, dict[str, str]]] = {
    "start": {"es": "¡Vamos a jugar a los colores!", "en": "Let's play color hunt!"},
    "find": {"es": "¡Busca algo {color}!", "en": "Find something {color}!"},
    "found": {"es": "¡Sí! ¡{color}!", "en": "Yes! {color}!"},
    "wrong": {"es": "Mmm, eso es {seen}. ¡Busca algo {color}!", "en": "Hmm, that's {seen}. Find something {color}!"},
    "time_up": {"es": "¡Se acabó el tiempo! Era el {color}.", "en": "Time's up! It was {color}."},
}


def _name(color: str, language: str) -> str:
    spanish, english, _ = TARGETS[color]
    return spanish if language == "es" else english


def _line(kind: str, color: str, language: str, seen: str = "") -> str:
    name = _name(color, language) if color else ""
    if kind == "find" and language == "es":
        name = name.upper()  # said with emphasis
    if kind == "found":
        name = name[:1].upper() + name[1:]
    return _LINES[kind][language].format(color=name, seen=_name(seen, language) if seen else "")


def color_hunt_phrases() -> frozenset[str]:
    """Every line the color hunt says, in both languages, for the speech cache."""
    lines: set[str] = set(hunt_phrases())
    for language in ("es", "en"):
        lines.add(_line("start", "", language))
        for target, (_, _, family) in TARGETS.items():
            lines |= {_line(kind, target, language) for kind in ("find", "found", "time_up")}
            lines |= {_line("wrong", target, language, seen) for seen in TARGETS if seen not in family}
    return frozenset(lines)


class ColorHunt(Hunt):
    """Five rounds of finding a color."""

    info = COLOR_HUNT
    round_s = ROUND_S
    prefix = "colors"

    @classmethod
    def phrases(cls) -> frozenset[str]:
        """Every fixed line, for the speech cache."""
        return color_hunt_phrases()

    def targets(self) -> list[str]:
        """Every color."""
        return list(TARGETS)

    def questions(self, target: str) -> dict[str, dict[str, Any]]:
        """Held up, and which main color."""
        return _QUESTIONS

    def judge(self, answers: dict[str, dict[str, Any]], target: str) -> Check:
        """Count the color when it is in the target's group; a sure color outside it is wrong."""
        choice = answers["color"]
        color = str(choice["choice"])
        probability = float(choice["probabilities"][color])
        color = color if color in TARGETS else "none"
        family = TARGETS[target][2]
        return Check(
            held=float(answers["held"]["noul"]),
            seen=color,
            match=color in family,
            wrong=color != "none" and color not in family and probability >= MIN_WRONG_P,
            probability=probability,
        )

    def line(self, kind: str, target: str, language: str, seen: str = "") -> str:
        """Return what Reachy says."""
        return _line(kind, target, language, seen)

    def name(self, target: str, language: str) -> str:
        """Return the color's name."""
        return _name(target, language)

    def moments(self, kind: str, target: str, seen: str = "") -> tuple[str, ...]:
        """Return the recorded moments; "wrong" says which color it is ("Eso es azul") and the target again."""
        if kind == "wrong" and seen in TARGETS:
            return (f"colors_seen_{seen}", f"colors_again_{target}")
        return super().moments(kind, target, seen)
