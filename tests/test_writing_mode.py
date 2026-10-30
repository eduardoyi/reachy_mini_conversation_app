"""Tests for writing practice: one capital letter at a time on the e-ink screen."""

import asyncio
from pathlib import Path

import pytest

from reachy_mini_conversation_app import writing_mode
from reachy_mini_conversation_app.eink_display import EInkDisplay
from reachy_mini_conversation_app.writing_mode import (
    IDLE_END_S,
    STEP_COOLDOWN_S,
    WRITING_PHRASES,
    WritingMode,
    letter_phrase,
    writing_letters,
)


class _Room:
    """A writing mode with a real display, a hand-moved clock, a head to pat and a record of what was said."""

    def __init__(self, tmp_path: Path) -> None:
        self.now = 1000.0
        self.pats = 0
        self.said: list[str] = []
        self.quiet: list[bool] = []
        self.display = EInkDisplay(port=8090, refresh_s=60, cache_dir=tmp_path, image_model="gpt-image-2.5-flare")
        self.writing = WritingMode(
            display=self.display,
            say=lambda text, language: self.said.append(text),
            quiet_head=self.quiet.append,
            touches=lambda: self.pats,
            clock=lambda: self.now,
        )

    async def pat(self) -> None:
        self.now += STEP_COOLDOWN_S
        self.pats += 1
        await asyncio.sleep(0.25)  # the touch watch looks every 0.1 s


def _phrases(text: str) -> list[str]:
    letters = writing_letters(text)
    assert letters is not None
    return [letter_phrase(letters, step) for step in range(len(letters))]


def test_text_becomes_capitals_with_single_spaces() -> None:
    """Accents, Ñ and digits are kept; punctuation is dropped; letters Spanish writing lacks are refused."""
    assert writing_letters("te  quiero, mamá!") == list("TE QUIERO MAMÁ")
    assert writing_letters("ñandú 2") == list("ÑANDÚ 2")
    assert writing_letters("crème") is None
    assert writing_letters("¡¿!?") is None


@pytest.mark.parametrize(
    ("word", "expected"),
    [
        ("PAPÁ", ["Primero, la pe. Suena p, como en pato.", "Ahora, la a. Suena a.", None, "Ahora, la a con tilde. Suena a."]),
        ("CHOCOLATE", ["Primero, la ce. Va con la hache.", "Ahora, la hache. Con la ce, suena ch, como en chocolate."]),
        ("CIELO", ["Primero, la ce. Aquí suena como en cielo."]),
        ("GATO", ["Primero, la ge. Suena g, como en gato."]),
        ("GIGANTE", ["Primero, la ge. Aquí suena jjj, como en gigante."]),
        ("QUESO", ["Primero, la cu. Suena k, como en queso.", "Ahora, la u. Aquí no suena."]),
        ("LLAVE", ["Primero, la ele. Va con otra ele.", "Ahora, la ele. Con la otra ele, suena como en llave."]),
        ("PERRO", [None, None, "Ahora, la erre. Va con otra erre.", "Ahora, la erre. Con la otra erre, suena fuerte: rrr."]),
        ("RATÓN", ["Primero, la erre. Suena fuerte: rrr, como en ratón."]),
        ("PERA", [None, None, "Ahora, la erre. Aquí suena suave, como en pera."]),
        ("HOLA", ["Primero, la hache. Es muda: no suena."]),
        ("REY", [None, None, "Ahora, la i griega. Aquí suena i, como en rey."]),
        ("TE AMO", [None, None, "Ahora, un espacio."]),
        ("5", ["Primero, el número cinco."]),
    ],
)  # fmt: skip
def test_each_letter_is_named_with_its_sound_in_this_word(word: str, expected: list[str | None]) -> None:
    """Reachy names the letter and says how it sounds here; all such lines are cached on disk."""
    phrases = _phrases(word)
    for phrase, wanted in zip(phrases, expected):
        if wanted is not None:
            assert phrase == wanted
    assert set(phrases) <= WRITING_PHRASES


@pytest.mark.asyncio
async def test_a_word_is_written_one_letter_per_pat(tmp_path: Path) -> None:
    """Each pat shows the next letter and says it; the last one shows the word and praises it."""
    room = _Room(tmp_path)

    assert room.writing.start("Papá") is None

    assert room.writing.active and room.display.owner() == "writing"
    assert room.quiet == [True]  # head held still, so a pat shows
    assert room.said == ["Primero, la pe. Suena p, como en pato."]
    first = room.display.current().png
    for _ in range(3):
        await room.pat()
    assert room.said[-1] == "Ahora, la a con tilde. Suena a."
    assert room.display.current().png != first

    await room.pat()

    assert not room.writing.active
    assert room.said[-1] == "¡Muy bien! Has escrito papá."
    assert room.display.owner() == "requested"  # the finished word follows the usual screen rules
    assert room.quiet[-1] is False


@pytest.mark.asyncio
async def test_words_heard_in_the_room_step_go_back_and_end(tmp_path: Path) -> None:
    """Only a writing word counts: "¡ya!" steps, "atrás" goes back, "ya terminé" ends; other talk is ignored."""
    room = _Room(tmp_path)
    assert not room.writing.hear("ya")  # nothing to write yet
    room.writing.start("sol")

    room.now += STEP_COOLDOWN_S
    assert room.writing.hear("¡Ya!")
    assert room.said[-1] == "Ahora, la o. Suena o."
    room.now += STEP_COOLDOWN_S
    assert room.writing.hear("Atrás.")
    assert room.said[-1] == "Primero, la ese. Suena sss, como en sol."
    assert not room.writing.hear("ya voy, mamá")
    assert not room.writing.hear("pon música")

    assert room.writing.hear("Ya terminé")
    assert not room.writing.active
    assert room.said[-1] == "Vale, lo dejamos aquí."
    assert room.display.owner() == "requested"
    await asyncio.sleep(0.15)
    assert room.quiet[-1] is False


@pytest.mark.asyncio
async def test_a_pat_with_a_ya_is_one_step(tmp_path: Path) -> None:
    """Steps closer than the cooldown count once."""
    room = _Room(tmp_path)
    room.writing.start("sol")
    room.now += STEP_COOLDOWN_S

    room.writing.hear("ya")
    room.pats += 1
    await asyncio.sleep(0.25)

    assert room.said == ["Primero, la ese. Suena sss, como en sol.", "Ahora, la o. Suena o."]


@pytest.mark.asyncio
async def test_ten_quiet_minutes_end_it(tmp_path: Path) -> None:
    """With no step for IDLE_END_S the head is released and the screen goes back to the usual rules."""
    room = _Room(tmp_path)
    room.writing.start("sol")

    room.now += IDLE_END_S
    await asyncio.sleep(0.25)

    assert not room.writing.active
    assert room.quiet == [True, False]
    assert room.display.owner() == "requested"


@pytest.mark.asyncio
async def test_the_head_stays_held_through_a_long_letter(tmp_path: Path) -> None:
    """Holding the head still lapses on its own, so the watch renews it."""
    room = _Room(tmp_path)
    room.writing.start("sol")

    room.now += writing_mode.HOLD_RENEW_S
    await asyncio.sleep(0.25)

    assert room.quiet == [True, True]
    room.writing.stop()


@pytest.mark.asyncio
async def test_too_long_or_unwritable_text_is_refused(tmp_path: Path) -> None:
    """More than 12 letters, or nothing writable, says why and changes nothing."""
    room = _Room(tmp_path)

    too_long = room.writing.start("murciélago gigante")
    assert too_long is not None and "17 letters" in too_long
    assert room.writing.start("¿?") is not None
    assert not room.writing.active and room.said == [] and room.display.owner() == "home"


@pytest.mark.asyncio
async def test_a_new_word_replaces_the_one_being_written(tmp_path: Path) -> None:
    """Starting again begins the new word; the old watch stops."""
    room = _Room(tmp_path)
    room.writing.start("sol")
    room.writing.start("mar")

    assert room.said[-1] == "Primero, la eme. Suena mmm, como en mamá."
    await room.pat()
    assert room.said[-1] == "Ahora, la a. Suena a."
    assert len([task for task in asyncio.all_tasks() if task.get_name() == "writing-touches"]) == 1
    room.writing.stop()
