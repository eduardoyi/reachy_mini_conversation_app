"""Judging a Big Brain answer: code first, then Jev, and the same answer always gets the same ruling.

Code accepts an answer that matches any accepted form of the clue's answer (both languages) once case, accents,
articles, punctuation and lead-ins ("creo que", "is it") are set aside, with small spelling or hearing slips,
or a surname alone for a person. Anything else goes to Jev with fixed rules: right, wrong, or right but too broad
("un barco" for the Titanic), which earns one "¿más específico?". Every Jev ruling is kept per clue and answer
(``rulings.json``), so a family arguing about an answer gets the same ruling next time too.
"""

import re
import json
import difflib
import logging
import unicodedata
from enum import Enum
from typing import Any, Final, Callable, Awaitable
from pathlib import Path


logger = logging.getLogger(__name__)

# A slip ("Bogdanovic", "Tereshkova" heard as "Tereskova") still counts above this similarity.
CLOSE_ENOUGH: Final[float] = 0.84
RIGHT_P: Final[float] = 0.5
TOO_BROAD_P: Final[float] = 0.6

_ARTICLES: Final[frozenset[str]] = frozenset({"the", "a", "an", "el", "la", "los", "las", "un", "una", "unos", "unas", "lo"})  # fmt: skip
_LEAD_INS: Final[re.Pattern[str]] = re.compile(
    r"^(?:(?:yo )?creo que (?:es |son |era |fue )?|es |son |seria |sera |quizas |tal vez |a ver |eh |em |mm |"
    r"i think (?:its |it is |it was |they are )?|its |it is |is it |was it |maybe |um |uh |who is |what is |"
    r"who are |what are |que es |quien es |quienes son )+"
)

JevAsk = Callable[[dict[str, Any], dict[str, dict[str, Any]], dict[str, bytes]], Awaitable[dict[str, dict[str, Any]]]]

_QUESTIONS: Final[dict[str, dict[str, Any]]] = {
    "right": {
        "type": "noul",
        "instructions": (
            "Is `player_answer` a correct answer to the quiz clue `clue`, given that any of `accepted_answers` is "
            "right? Accept either language, small spelling or hearing slips, a surname alone for a person, other "
            "common names for the same thing, and missing or extra articles. A different thing, person, place or "
            "number is wrong. An answer too broad to single out the right one (a category instead of the thing) "
            "is not correct."
        ),
    },
    "too_broad": {
        "type": "noul",
        "instructions": (
            "Is `player_answer` on the right track for the quiz clue `clue` (whose answer is any of "
            "`accepted_answers`) but too broad or incomplete to count, such as 'a ship' for the Titanic, 'a "
            "painter' for Frida Kahlo, or a first name alone when the surname matters?"
        ),
    },
}


class Ruling(Enum):
    """What the host does with an answer."""

    RIGHT = "right"
    WRONG = "wrong"
    SPECIFIC = "specific"  # on the right track: ask once for more


def plain(text: str) -> str:
    """Lowercase ``text`` without accents, punctuation, lead-ins or leading articles."""
    text = unicodedata.normalize("NFKD", text.lower())
    text = "".join(char for char in text if not unicodedata.combining(char))
    text = re.sub(r"[^\w\s]", " ", text)
    text = " ".join(text.split())
    text = _LEAD_INS.sub("", text).strip()
    words = text.split()
    while words and words[0] in _ARTICLES:
        words = words[1:]
    return " ".join(words)


def matches(said: str, accept: list[str]) -> bool:
    """Whether ``said`` is one of the ``accept`` forms by code alone (no model)."""
    heard = plain(said)
    if not heard:
        return False
    for form in accept:
        target = plain(form)
        if not target:
            continue
        if heard == target:
            return True
        if difflib.SequenceMatcher(None, heard, target).ratio() >= CLOSE_ENOUGH:
            return True
        words = target.split()
        # A surname alone for a person ("Garbo" for "Greta Garbo"), when it is a real word.
        if len(words) >= 2 and len(words[-1]) >= 4 and heard == words[-1]:
            return True
        # "Greta Garbo, la actriz": the answer said with a few more words around it.
        if f" {target} " in f" {heard} " and len(heard.split()) <= len(words) + 3:
            return True
    return False


class Judge:
    """Rules on answers; keeps Jev's rulings in ``path``."""

    def __init__(self, path: Path, jev: JevAsk | None) -> None:
        """Read the kept rulings."""
        self._path = path
        self._jev = jev
        try:
            self._rulings: dict[str, str] = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            self._rulings = {}

    async def rule(self, clue_id: str, clue_text: str, accept: list[str], said: str) -> Ruling:
        """Return the ruling on ``said`` for a clue; WRONG when nothing was said or Jev cannot be reached."""
        if not plain(said):
            return Ruling.WRONG
        if matches(said, accept):
            return Ruling.RIGHT
        key = f"{clue_id}|{plain(said)}"
        if key in self._rulings:
            return Ruling(self._rulings[key])
        if self._jev is None:
            return Ruling.WRONG
        try:
            answers = await self._jev(
                {"clue": clue_text, "accepted_answers": accept, "player_answer": said}, _QUESTIONS, {}
            )
            right = float(answers["right"]["noul"])
            broad = float(answers["too_broad"]["noul"])
        except Exception as error:
            logger.warning("Big Brain answer not judged by Jev: %s", type(error).__name__)
            return Ruling.WRONG
        ruling = Ruling.RIGHT if right >= RIGHT_P else Ruling.SPECIFIC if broad >= TOO_BROAD_P else Ruling.WRONG
        self._rulings[key] = ruling.value
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            self._path.write_text(json.dumps(self._rulings, ensure_ascii=False), encoding="utf-8")
        except OSError as error:
            logger.warning("Big Brain ruling not kept: %s", type(error).__name__)
        return ruling

    def overrule(self, clue_id: str, said: str) -> None:
        """Keep the room's verdict: this answer counts for this clue from now on."""
        self._rulings[f"{clue_id}|{plain(said)}"] = Ruling.RIGHT.value
        try:
            self._path.write_text(json.dumps(self._rulings, ensure_ascii=False), encoding="utf-8")
        except OSError:
            pass
