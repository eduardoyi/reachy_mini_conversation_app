"""Writing practice: a child copies a word from the e-ink screen, one capital letter at a time.

"Richie, enséñame a escribir PAPÁ" shows the first letter very large, with the word filling in above it, and
Reachy names it and its sound ("Primero, la pe. Suena p, como en pato."). A pat on his head, or a room
transcript that is only "¡ya!", "listo" or "siguiente" (no name needed), shows the next; "atrás" goes back and
"ya terminé" ends it. It all runs locally: the room is already transcribed on this computer, the phrases are
fixed lines cached by the reply voice, and no cloud conversation stays open while he writes.
"""

import time
import asyncio
import logging
import itertools
import unicodedata
from typing import Final, Callable

from reachy_mini_conversation_app.eink_display import EInkDisplay, render_writing
from reachy_mini_conversation_app.quick_commands import normalize


logger = logging.getLogger(__name__)

# Letters a child learning to write manages in one go; spaces are not counted.
MAX_WRITING_LETTERS: Final[int] = 12
# Writing ends after this long without a step.
IDLE_END_S: Final[float] = 600.0
# A pat said with a "¡ya!" is one step, not two.
STEP_COOLDOWN_S: Final[float] = 2.0
# Holding the head still lapses after moves.HEAD_HOLD_MAX_S (180 s); it is renewed well before.
HOLD_RENEW_S: Final[float] = 60.0
_WATCH_S: Final[float] = 0.1

_LETTER_NAMES: Final[dict[str, str]] = {
    "A": "la a", "B": "la be", "C": "la ce", "D": "la de", "E": "la e", "F": "la efe", "G": "la ge",
    "H": "la hache", "I": "la i", "J": "la jota", "K": "la ka", "L": "la ele", "M": "la eme", "N": "la ene",
    "Ñ": "la eñe", "O": "la o", "P": "la pe", "Q": "la cu", "R": "la erre", "S": "la ese", "T": "la te",
    "U": "la u", "V": "la uve", "W": "la uve doble", "X": "la equis", "Y": "la i griega", "Z": "la zeta",
    "Á": "la a con tilde", "É": "la e con tilde", "Í": "la i con tilde", "Ó": "la o con tilde",
    "Ú": "la u con tilde", "Ü": "la u con diéresis",
    "0": "el número cero", "1": "el número uno", "2": "el número dos", "3": "el número tres",
    "4": "el número cuatro", "5": "el número cinco", "6": "el número seis", "7": "el número siete",
    "8": "el número ocho", "9": "el número nueve",
}  # fmt: skip
# Plosives cannot be said alone, so they come with a word; the rest are held ("mmm").
_SOUNDS: Final[dict[str, str]] = {
    "A": "Suena a.", "E": "Suena e.", "I": "Suena i.", "O": "Suena o.", "U": "Suena u.",
    "Á": "Suena a.", "É": "Suena e.", "Í": "Suena i.", "Ó": "Suena o.", "Ú": "Suena u.",
    "Ü": "Aquí la u sí suena.",
    "B": "Suena b, como en barco.", "C": "Suena k, como en casa.", "D": "Suena d, como en dedo.",
    "F": "Suena fff, como en foca.", "G": "Suena g, como en gato.", "H": "Es muda: no suena.",
    "J": "Suena jjj, como en jirafa.", "K": "Suena k, como en koala.", "L": "Suena lll, como en luna.",
    "M": "Suena mmm, como en mamá.", "N": "Suena nnn, como en nube.", "Ñ": "Suena ñ, como en niño.",
    "P": "Suena p, como en pato.", "Q": "Suena k, como en queso.", "R": "Suena fuerte: rrr, como en ratón.",
    "S": "Suena sss, como en sol.", "T": "Suena t, como en tomate.", "V": "Suena b, como en vaca.",
    "W": "Suena como en wifi.", "X": "Suena ks, como en taxi.", "Y": "Suena como en yoyó.",
    "Z": "Suena como en zapato.",
}  # fmt: skip
_SOFT_VOWELS: Final[frozenset[str]] = frozenset("EIÉÍ")
WRITABLE: Final[frozenset[str]] = frozenset(_LETTER_NAMES)

_NEXT_WORDS: Final[frozenset[str]] = frozenset(
    {"ya", "listo", "lista", "siguiente", "la siguiente", "otra", "otra letra", "ya esta", "ya lo hice", "hecho",
     "sigue", "venga"}
)  # fmt: skip
_BACK_WORDS: Final[frozenset[str]] = frozenset({"otra vez", "atras", "la anterior", "repite", "repitela"})
_END_WORDS: Final[frozenset[str]] = frozenset(
    {"ya termine", "termine", "he terminado", "se acabo", "ya no quiero", "no quiero mas", "basta", "fin", "para",
     "para ya"}
)  # fmt: skip
_STOPPED: Final[str] = "Vale, lo dejamos aquí."


def writing_letters(text: str) -> list[str] | None:
    """Return the capitals of ``text`` with " " between words, or None when it has a character to skip."""
    letters: list[str] = []
    for character in unicodedata.normalize("NFC", text).upper():
        if character.isspace() or character in "-,":
            if letters and letters[-1] != " ":
                letters.append(" ")
        elif character in WRITABLE:
            letters.append(character)
        elif character.isalnum():
            return None  # a letter Spanish writing does not use (è, ç): better to leave it to the conversation
    while letters and letters[-1] == " ":
        letters.pop()
    return letters or None


def letter_phrase(letters: list[str], step: int) -> str:
    """Return what Reachy says when ``letters[step]`` comes up: its name, then its sound in this word."""
    letter = letters[step]
    opening = "Primero" if step == 0 else "Ahora"
    if letter == " ":
        return f"{opening}, un espacio."
    before = letters[step - 1] if step > 0 else ""
    after = letters[step + 1] if step + 1 < len(letters) else ""
    if letter.isdigit():
        return f"{opening}, {_LETTER_NAMES[letter]}."
    if letter == "C" and after == "H":
        sound = "Va con la hache."
    elif letter == "H" and before == "C":
        sound = "Con la ce, suena ch, como en chocolate."
    elif letter == "C" and after in _SOFT_VOWELS:
        sound = "Aquí suena como en cielo."
    elif letter == "G" and after in _SOFT_VOWELS:
        sound = "Aquí suena jjj, como en gigante."
    elif letter == "L" and after == "L":
        sound = "Va con otra ele."
    elif letter == "L" and before == "L":
        sound = "Con la otra ele, suena como en llave."
    elif letter == "R" and after == "R":
        sound = "Va con otra erre."
    elif letter == "R" and before == "R":
        sound = "Con la otra erre, suena fuerte: rrr."
    elif letter == "R" and before not in ("", " ", "L", "N", "S"):
        sound = "Aquí suena suave, como en pera."
    elif letter == "U" and (before == "Q" or before == "G" and after in _SOFT_VOWELS):
        sound = "Aquí no suena."
    elif letter == "Y" and after in ("", " "):
        sound = "Aquí suena i, como en rey."
    else:
        sound = _SOUNDS[letter]
    return f"{opening}, {_LETTER_NAMES[letter]}. {sound}"


def _all_phrases() -> frozenset[str]:
    """Every letter phrase in every neighbourhood, so the reply voice keeps them all on disk."""
    around = ["", " ", *WRITABLE]
    phrases = {_STOPPED}
    for before, letter, after in itertools.product(around, [" ", *WRITABLE], around):
        letters = [character for character in (before, letter, after) if character]
        phrases.add(letter_phrase(letters, 1 if before else 0))
    return frozenset(phrases)


WRITING_PHRASES: Final[frozenset[str]] = _all_phrases()


class WritingMode:
    """One word at a time on the screen, stepped by pats on Reachy's head or by short words heard in the room."""

    def __init__(
        self,
        *,
        display: EInkDisplay,
        say: Callable[[str, str], None],
        quiet_head: Callable[[bool], None],
        touches: Callable[[], int],
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        """Show screens on ``display`` and ``say`` phrases; ``quiet_head`` holds the head still to feel pats."""
        self._display = display
        self._say = say
        self._quiet_head = quiet_head
        self._touches = touches
        self._clock = clock
        self._letters: list[str] = []
        self._text = ""
        self._step = 0
        self._stepped_at = -IDLE_END_S
        self._watch: asyncio.Task[None] | None = None
        self._screen = b""

    @property
    def active(self) -> bool:
        """Whether a word is being written now."""
        return bool(self._letters)

    def start(self, text: str) -> str | None:
        """Start writing ``text``; return why it cannot be written instead (too long, nothing to write)."""
        letters = writing_letters(text)
        if letters is None:
            return "It has nothing a child can write here: only letters, accented vowels, Ñ and digits."
        count = sum(1 for letter in letters if letter != " ")
        if count > MAX_WRITING_LETTERS:
            return f"It has {count} letters; a child learning to write manages {MAX_WRITING_LETTERS} at most."
        if self.active:
            self._end()
        self._letters, self._text, self._step = letters, " ".join(text.split()), 0
        self._quiet_head(True)
        self._watch = asyncio.get_running_loop().create_task(
            self._watch_touches(self._touches(), self._clock()), name="writing-touches"
        )
        logger.info("Writing practice started letters=%d", count)
        self._show_step()
        return None

    def hear(self, text: str) -> bool:
        """Act on ``text`` when it is only a writing word ("ya", "atrás", "ya terminé"); False otherwise."""
        if not self.active:
            return False
        said = normalize(text)
        if said in _NEXT_WORDS:
            self._next()
        elif said in _BACK_WORDS:
            self._back()
        elif said in _END_WORDS:
            logger.info("Writing practice ended: asked")
            self.stop()
            self._say(_STOPPED, "es")
        else:
            return False
        return True

    def stop(self) -> None:
        """End writing now (another request, or asked); the screen stays and follows the usual rules again."""
        if not self.active:
            return
        self._end()
        self._display.show(self._screen)

    def _next(self) -> None:
        now = self._clock()
        if now - self._stepped_at < STEP_COOLDOWN_S:
            return
        self._step += 1
        if self._step < len(self._letters):
            self._show_step()
            return
        logger.info("Writing practice finished")
        letters, text = self._letters, self._text
        self._end()
        self._display.show(render_writing(letters, len(letters)))
        self._say(f"¡Muy bien! Has escrito {text.lower()}.", "es")

    def _back(self) -> None:
        if self._clock() - self._stepped_at < STEP_COOLDOWN_S:
            return
        self._step = max(0, self._step - 1)
        self._show_step()

    def _show_step(self) -> None:
        self._stepped_at = self._clock()
        self._screen = render_writing(self._letters, self._step)
        self._display.show(self._screen, owner="writing")
        self._say(letter_phrase(self._letters, self._step), "es")

    def _end(self) -> None:
        self._letters = []
        self._quiet_head(False)
        watch, self._watch = self._watch, None
        if watch is not None and watch is not asyncio.current_task():
            watch.cancel()

    async def _watch_touches(self, seen: int, held_at: float) -> None:
        """Step on each pat after the ``seen`` ones, keep the head held, and end after IDLE_END_S without a step."""
        while self.active:
            await asyncio.sleep(_WATCH_S)
            now = self._clock()
            if now - self._stepped_at >= IDLE_END_S:
                logger.info("Writing practice ended: idle")
                self.stop()
                return
            if now - held_at >= HOLD_RENEW_S:
                self._quiet_head(True)
                held_at = now
            count = self._touches()
            if count > seen:
                seen = count
                self._next()
