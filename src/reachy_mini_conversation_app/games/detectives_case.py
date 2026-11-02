"""Detectives' cases: pre-written mysteries in English and Spanish, loaded from ``detectives_cases/*.json``.

A case is fixed data, written once, so the story cannot contradict itself: what happened (the intro), every
character's sheet (personality, alibi, what they know, their secret and what they lie about), the clues and the
round each may be examined from, the culprit and motive, and the solution Richie reveals step by step. Only the
witness lines are made at game time, from one character's sheet (``detectives_witness.py``). The schema is
documented in docs/features/detectives.md.
"""

import json
import random
from typing import Any, Final
from pathlib import Path
from dataclasses import dataclass


CASES_DIR: Final[Path] = Path(__file__).resolve().parent / "detectives_cases"
LANGUAGES: Final[tuple[str, ...]] = ("en", "es")
KINDS: Final[tuple[str, ...]] = ("suspect", "witness")

Text = dict[str, str]


class CaseError(ValueError):
    """A case file that does not follow the schema."""


@dataclass(frozen=True)
class Voice:
    """How a character sounds: a description for ElevenLabs' voice design, and the designed voice once made."""

    description: str
    id: str | None = None  # the account's voice id; None until scripts/detectives_voices.py designs it


@dataclass(frozen=True)
class Character:
    """Someone the detectives can question: a suspect (who may be accused) or a witness."""

    id: str
    kind: str
    name: Text
    about: Text  # one public line, on the phones and the board
    personality: Text
    alibi: Text
    knows: tuple[Text, ...]
    secret: Text | None
    lies: Text | None
    fallback: Text  # said when the witness model fails
    voice: Voice | None = None  # None: Richie says their lines in his own voice


@dataclass(frozen=True)
class Clue:
    """A piece of evidence, examined on a player's turn from ``round`` on."""

    id: str
    round: int
    title: Text
    text: Text


@dataclass(frozen=True)
class Motive:
    """One of the motives offered in the accusation."""

    id: str
    text: Text


@dataclass(frozen=True)
class Case:
    """One mystery."""

    id: str
    kids: bool
    rounds: int
    title: Text
    intro: Text
    characters: tuple[Character, ...]
    culprit: str
    motives: tuple[Motive, ...]
    motive: str
    clues: tuple[Clue, ...]
    insider: Text | None  # a private fact for one adult player's phone
    reveal: tuple[Text, ...]

    def character(self, character_id: str) -> Character | None:
        """Return the character with ``character_id``, or None."""
        return next((c for c in self.characters if c.id == character_id), None)

    @property
    def suspects(self) -> list[Character]:
        """Return the characters who may be accused."""
        return [c for c in self.characters if c.kind == "suspect"]


def _text(value: Any, where: str) -> Text:
    if not isinstance(value, dict) or not all(isinstance(value.get(lang), str) and value[lang].strip() for lang in LANGUAGES):  # fmt: skip
        raise CaseError(f"{where}: needs non-empty 'en' and 'es' text")
    return {lang: value[lang].strip() for lang in LANGUAGES}


def _optional_text(value: Any, where: str) -> Text | None:
    return None if value is None else _text(value, where)


def _voice(value: Any, where: str) -> Voice | None:
    if value is None:
        return None
    description = value.get("description") if isinstance(value, dict) else None
    voice_id = value.get("id") if isinstance(value, dict) else None
    if not isinstance(description, str) or not description.strip():
        raise CaseError(f"{where}: a voice needs a description")
    if voice_id is not None and (not isinstance(voice_id, str) or not voice_id.isalnum()):
        raise CaseError(f"{where}: a voice id is letters and digits")
    return Voice(description.strip(), voice_id)


def _id(value: Any, where: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise CaseError(f"{where}: needs an id")
    return value


def _list(value: Any, where: str) -> list[Any]:
    if not isinstance(value, list) or not value:
        raise CaseError(f"{where}: needs a non-empty list")
    return value


def parse_case(raw: Any) -> Case:
    """Return the case in ``raw`` (a case file's JSON), or raise CaseError naming what is wrong."""
    if not isinstance(raw, dict):
        raise CaseError("a case is a JSON object")
    case_id = _id(raw.get("id"), "case")
    rounds = raw.get("rounds")
    if not isinstance(rounds, int) or not 1 <= rounds <= 8:
        raise CaseError(f"{case_id}: rounds must be 1 to 8")
    characters = tuple(
        Character(
            id=_id(entry.get("id"), f"{case_id} character"),
            kind=str(entry.get("kind")),
            name=_text(entry.get("name"), f"{case_id}.{entry.get('id')}.name"),
            about=_text(entry.get("about"), f"{case_id}.{entry.get('id')}.about"),
            personality=_text(entry.get("personality"), f"{case_id}.{entry.get('id')}.personality"),
            alibi=_text(entry.get("alibi"), f"{case_id}.{entry.get('id')}.alibi"),
            knows=tuple(
                _text(fact, f"{case_id}.{entry.get('id')}.knows")
                for fact in _list(entry.get("knows"), f"{case_id}.{entry.get('id')}.knows")
            ),  # fmt: skip
            secret=_optional_text(entry.get("secret"), f"{case_id}.{entry.get('id')}.secret"),
            lies=_optional_text(entry.get("lies"), f"{case_id}.{entry.get('id')}.lies"),
            fallback=_text(entry.get("fallback"), f"{case_id}.{entry.get('id')}.fallback"),
            voice=_voice(entry.get("voice"), f"{case_id}.{entry.get('id')}.voice"),
        )
        for entry in _list(raw.get("characters"), f"{case_id}.characters")
    )
    clues = tuple(
        Clue(
            id=_id(entry.get("id"), f"{case_id} clue"),
            round=int(entry.get("round", 0)),
            title=_text(entry.get("title"), f"{case_id}.{entry.get('id')}.title"),
            text=_text(entry.get("text"), f"{case_id}.{entry.get('id')}.text"),
        )
        for entry in _list(raw.get("clues"), f"{case_id}.clues")
    )
    motives = tuple(
        Motive(id=_id(entry.get("id"), f"{case_id} motive"), text=_text(entry.get("text"), f"{case_id} motive"))
        for entry in _list(raw.get("motives"), f"{case_id}.motives")
    )
    case = Case(
        id=case_id,
        kids=bool(raw.get("kids")),
        rounds=rounds,
        title=_text(raw.get("title"), f"{case_id}.title"),
        intro=_text(raw.get("intro"), f"{case_id}.intro"),
        characters=characters,
        culprit=_id(raw.get("culprit"), f"{case_id}.culprit"),
        motives=motives,
        motive=_id(raw.get("motive"), f"{case_id}.motive"),
        clues=clues,
        insider=_optional_text(raw.get("insider"), f"{case_id}.insider"),
        reveal=tuple(_text(step, f"{case_id}.reveal") for step in _list(raw.get("reveal"), f"{case_id}.reveal")),
    )
    ids = [c.id for c in characters]
    if len(set(ids)) != len(ids) or len({c.id for c in clues}) != len(clues) or len({m.id for m in motives}) != len(motives):  # fmt: skip
        raise CaseError(f"{case_id}: ids must be unique")
    if any(c.kind not in KINDS for c in characters):
        raise CaseError(f"{case_id}: a character's kind is 'suspect' or 'witness'")
    if len(case.suspects) < 3:
        raise CaseError(f"{case_id}: needs at least three suspects")
    culprit = case.character(case.culprit)
    if culprit is None or culprit.kind != "suspect" or culprit.secret is None or culprit.lies is None:
        raise CaseError(f"{case_id}: the culprit must be a suspect with a secret and a lie")
    if case.motive not in {m.id for m in motives} or len(motives) < 2:
        raise CaseError(f"{case_id}: the motive must be one of at least two motives")
    if any(not 1 <= clue.round <= rounds for clue in clues) or not any(clue.round == 1 for clue in clues):
        raise CaseError(f"{case_id}: clue rounds run from 1 to the case's rounds, with a clue in round 1")
    return case


def load_cases(folder: Path = CASES_DIR) -> list[Case]:
    """Return every case in ``folder``; a broken file raises CaseError naming it."""
    cases = []
    for path in sorted(folder.glob("*.json")):
        try:
            cases.append(parse_case(json.loads(path.read_text(encoding="utf-8"))))
        except (ValueError, AttributeError, TypeError) as error:
            raise CaseError(f"{path.name}: {error}") from error
    return cases


def pick_case(cases: list[Case], *, kids: bool, played: list[str], rng: random.Random) -> Case | None:
    """Return a case for kids or adults: one never played if any, else the one played longest ago."""
    fitting = [case for case in cases if case.kids == kids]
    fresh = [case for case in fitting if case.id not in played]
    if fresh:
        return rng.choice(fresh)
    return min(fitting, key=lambda case: played.index(case.id), default=None)
