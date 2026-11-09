"""La carrera de números ("juguemos a la carrera de números"): a counting race for children of 2 to 6 (docs/plans/number-race.md).

Siegler and Ramani's number board game with the cars from the story La gran carrera: the children drive Tito,
Richie drives Flecha, on straight tracks of squares 1 to 10 shown on the e-ink screen. On the children's turn a
pat on his head (or "¡gira!") spins a wheel of 1 or 2, the squares to count turn grey, and they count them out
loud from Tito's square ("cinco, seis"). The number Tito reaches heard anywhere in the answer is cheered and said
again; anything else, or nothing, and Richie counts with them. Nothing waits for long: after a while he spins
for them. On his turn he spins and counts Flecha's squares himself, counting on, as the children should. The
first car to 10 wins, then "¿Otra vez?". A straight track (farther along is bigger) and saying each number are
what made the original game work.
"""

import random
import logging
from typing import Final

from reachy_mini_conversation_app.games.base import Game, GameContext
from reachy_mini_conversation_app.games.hunt import ask_again
from reachy_mini_conversation_app.games.catalog import NUMBER_RACE, words
from reachy_mini_conversation_app.quick_commands import normalize
from reachy_mini_conversation_app.games.kids_audio import KidsAudio
from reachy_mini_conversation_app.games.number_race_screens import TITO, TRACK, FLECHA, render_number_race


logger = logging.getLogger(__name__)

SPINS: Final[tuple[int, ...]] = (1, 2)
# The children's spin: a reminder after SPIN_NUDGE_S, and Richie spins for them after SPIN_WAIT_S.
SPIN_NUDGE_S: Final[float] = 12.0
SPIN_WAIT_S: Final[float] = 30.0
# Pats in the first SPIN_SETTLE_S after "¡Te toca!" are the last turn's.
SPIN_SETTLE_S: Final[float] = 0.8
# The count may start up to COUNT_LISTEN_S after "¡Dos! A contar…", and is waited for COUNT_ANSWER_S at most.
COUNT_LISTEN_S: Final[float] = 6.0
COUNT_ANSWER_S: Final[float] = 15.0
_TICK_S: Final[float] = 0.1

# The number words, said one after another when counting, and sent to the speech model as hints.
NUMBER_WORDS: Final[dict[str, tuple[str, ...]]] = {
    "es": ("uno", "dos", "tres", "cuatro", "cinco", "seis", "siete", "ocho", "nueve", "diez"),
    "en": ("one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten"),
}
# Heard in either language, as children mix them; a few common mishearings too.
_HEARD: Final[dict[str, int]] = {
    **{word: number for language in NUMBER_WORDS.values() for number, word in enumerate(language, start=1)},
    "una": 1, "un": 1, "sei": 6, "nuevo": 9, "dies": 10, "won": 1, "too": 2, "tree": 3,
}  # fmt: skip
# Said without his name while the wheel waits: spin it.
_SPIN: Final[frozenset[str]] = frozenset(
    {"gira", "girala", "gira la rueda", "dale", "ya", "listo", "vamos", "spin", "spin it", "go", "ready"}
)
_CAPTION: Final[dict[str, str]] = {"es": "La carrera de números", "en": "Number race"}
_WON: Final[dict[str, dict[str, str]]] = {
    TITO: {"es": "¡Ganó Tito!", "en": "Tito wins!"},
    FLECHA: {"es": "¡Ganó Flecha!", "en": "Flecha wins!"},
}


def heard_numbers(text: str) -> set[int]:
    """Return the numbers 1 to 10 in ``text``, as words in Spanish or English or as digits."""
    found: set[int] = set()
    for token in normalize(text).split():
        number = int(token) if token.isdigit() else _HEARD.get(token)
        if number is not None and 1 <= number <= TRACK:
            found.add(number)
    return found


class NumberRace(Game):
    """Tito and Flecha take turns along squares 1 to 10; the children spin and count Tito's squares."""

    info = NUMBER_RACE
    holds_head = True  # pats spin the wheel

    def __init__(self, audio: KidsAudio, rng: random.Random | None = None) -> None:
        """Speak with ``audio`` (the kids' recorded voice); ``rng`` spins the wheel."""
        self._audio = audio
        self._rng = rng or random.Random()
        self._at = {TITO: 0, FLECHA: 0}
        self._spin_open = False
        self._spin_said = False

    def heard(self, context: GameContext, text: str) -> bool:
        """Spin on "¡gira!" (or "¡ya!", "spin") said without the name while the wheel waits."""
        if self._spin_open and words(text) in _SPIN:
            self._spin_said = True
            return True
        return False

    async def play(self, context: GameContext) -> None:
        """Race until nobody wants another; then thanks, or the kids' game they asked for."""
        context.services.hint_answers(list(NUMBER_WORDS.get(context.language, NUMBER_WORDS["es"])))
        while True:
            await self._race(context)
            following = await ask_again(context, self._audio)
            if following == self.info.key:
                continue
            context.next_game = following
            if following is None:
                await self._audio.say(context, "thanks")
            return

    def _show(self, context: GameContext, counting: tuple[str, range] = ("", range(0))) -> None:
        context.show(render_number_race(self._at[TITO], self._at[FLECHA], _CAPTION[context.language], counting))
        context.progress = f"Tito {self._at[TITO]}, Flecha {self._at[FLECHA]} of {TRACK}"

    async def _race(self, context: GameContext) -> None:
        """One race from the start line to 10."""
        self._at = {TITO: 0, FLECHA: 0}
        self._show(context)
        await self._audio.say(context, "race_start", sounds=("intro_race",))
        first = True
        while True:
            await self._kids_turn(context, first)
            first = False
            if self._at[TITO] >= TRACK:
                return await self._finish(context, TITO)
            await self._flecha_turn(context)
            if self._at[FLECHA] >= TRACK:
                return await self._finish(context, FLECHA)

    def _spin(self, car: str) -> range:
        """Spin the wheel for ``car`` and return the squares it moves over (a spin past 10 stops at 10)."""
        start = self._at[car]
        return range(start + 1, min(TRACK, start + self._rng.choice(SPINS)) + 1)

    async def _kids_turn(self, context: GameContext, first: bool) -> None:
        """Wait for a spin, ask the children to count Tito's squares, then cheer the number or count together."""
        await self._audio.say(context, "race_turn_first" if first else "race_turn")
        await self._wait_spin(context)
        squares = self._spin(TITO)
        self._show(context, (TITO, squares))
        await self._audio.say(context, sounds=("spin",))
        said = await self._audio.ask(context, f"race_count_{len(squares)}", COUNT_ANSWER_S, listen_s=COUNT_LISTEN_S)
        if said:
            context.played()
        reached = squares[-1]
        right = reached in heard_numbers(said or "")
        logger.info(
            "Number race: Tito %d to %d, counted %s", squares[0] - 1, reached, "right" if right else "together"
        )
        self._at[TITO] = reached
        self._show(context)
        if right:
            await self._audio.say(context, "race_right", f"num_{reached}", sounds=("putt",))
        else:
            await self._audio.say(context, "race_together", *(f"num_{n}" for n in squares), after=("putt",))

    async def _flecha_turn(self, context: GameContext) -> None:
        """Richie spins for Flecha and counts its squares out loud, counting on from where it is."""
        squares = self._spin(FLECHA)
        self._show(context, (FLECHA, squares))
        await self._audio.say(
            context, f"race_flecha_{len(squares)}", *(f"num_{n}" for n in squares), sounds=("spin",), after=("zoom",)
        )
        self._at[FLECHA] = squares[-1]
        self._show(context)

    async def _wait_spin(self, context: GameContext) -> None:
        """Return on a pat or "¡gira!", reminding once on the way; after SPIN_WAIT_S Richie spins for them."""
        touches = context.services.touches
        seen = touches()
        self._spin_said, self._spin_open = False, True
        started = context.played_s
        nudged = False
        try:
            while True:
                await context.sleep(_TICK_S)
                elapsed = context.played_s - started
                if elapsed < SPIN_SETTLE_S:
                    seen, self._spin_said = touches(), False
                elif touches() > seen or self._spin_said:
                    context.played()
                    return
                if elapsed >= SPIN_WAIT_S:
                    logger.info("Number race: nobody spun, Richie spins")
                    return
                if not nudged and elapsed >= SPIN_NUDGE_S:
                    nudged = True
                    await self._audio.say(context, "race_spin_nudge")
        finally:
            self._spin_open = False

    async def _finish(self, context: GameContext, winner: str) -> None:
        """Show who reached 10 and cheer: Tito's win, or Flecha's with "¡La próxima le ganamos!"."""
        context.show(render_number_race(self._at[TITO], self._at[FLECHA], _WON[winner][context.language]))
        context.progress = f"{winner} reached {TRACK}"
        moment = "race_tito_wins" if winner == TITO else "race_flecha_wins"
        await self._audio.say(context, moment, sounds=("celebrate",) if winner == TITO else ("time_up",))
