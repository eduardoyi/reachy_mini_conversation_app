"""Big Brain's board: five columns of four clues from the clue bank, four hidden squares and a final clue.

The bank (``scripts/trivia_bank.py``, then ``scripts/trivia_topics.py``) holds bilingual statement clues, each
with a broad category, a merged topic (``column``), a hardness from Jev (0 everyone knows it, 4 only buffs) and
every answer a judge accepts. A board's rows are the four quarters of the hardness of the clues it may use, so
200 points is always among the easiest and 800 among the hardest. Columns are themed topics with a clue in every
row when there are enough, broad categories otherwise. Kids mode uses only clues with a kids version and no adult
content. Clues used in the last ``RECENT_DAYS`` are left out, so a family does not see one twice in a month.
Clues measured in imperial units (miles, feet, Fahrenheit) are left out too: the family thinks in metric
(about 200 of 20,499 clues, 2026-11-01). A board never holds two clues with the same answer.
"""

import re
import json
import math
import time
import random
import logging
from typing import Any, Final
from pathlib import Path
from dataclasses import field, dataclass

from reachy_mini_conversation_app.games.big_brain_judge import plain


logger = logging.getLogger(__name__)

POINTS: Final[tuple[int, ...]] = (200, 400, 600, 800)
COLUMNS: Final[int] = 5
HIDDEN: Final[tuple[str, ...]] = ("double", "steal", "everyone", "bomb")
RECENT_DAYS: Final[float] = 30.0
# The final clue is among the hardest quarter, and one people enjoy.
FINAL_MIN_APPEAL: Final[float] = 2.3

_NUMBER_EN: Final[str] = (
    r"(?:\d[\d,.]*|one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|fifteen|twenty|thirty|forty|"
    r"fifty|sixty|seventy|eighty|ninety|hundred|thousand|million|billion)"
)
_NUMBER_ES: Final[str] = (
    r"(?:\d[\d,.]*|un|una|dos|tres|cuatro|cinco|seis|siete|ocho|nueve|diez|once|doce|quince|veinte|treinta|"
    r"cuarenta|cincuenta|sesenta|setenta|ochenta|noventa|cien|ciento|cientos|mil|millones)"
)
# A measurement, not a name: a number before the unit ("480 feet", "doce pies"), a speed, or degrees Fahrenheit.
# In Spanish "miles" means thousands, so only "millas" counts there.
_IMPERIAL: Final[dict[str, re.Pattern[str]]] = {
    "en": re.compile(
        rf"\b{_NUMBER_EN}[\s-]+(?:square[\s-]+|cubic[\s-]+)?"
        r"(?:miles?|feet|foot|ft|inch(?:es)?|yards?|gallons?|ounces?|oz|acres?|pounds?|lbs?)\b"
        r"|\bmiles per hour\b|\bmph\b|\bdegrees? fahrenheit\b|°\s?F\b|\bfahrenheit (?:scale|degrees?)\b",
        re.IGNORECASE,
    ),
    "es": re.compile(
        rf"\b{_NUMBER_ES}[\s-]+(?:millas?|pies?|pulgadas?|yardas?|galones|gal[oó]n|onzas?|libras?|acres?)\b"
        r"(?:[\s-]+cuadrad[oa]s)?|\bmillas por hora\b|\bgrados? fahrenheit\b|°\s?F\b",
        re.IGNORECASE,
    ),
}

CATEGORY_NAMES: Final[dict[str, dict[str, str]]] = {
    "science": {"en": "Science", "es": "Ciencia"},
    "nature": {"en": "Nature & Animals", "es": "Naturaleza y animales"},
    "geography": {"en": "Geography", "es": "Geografía"},
    "history": {"en": "History", "es": "Historia"},
    "arts": {"en": "Art & Literature", "es": "Arte y literatura"},
    "music": {"en": "Music", "es": "Música"},
    "screen": {"en": "Film & TV", "es": "Cine y TV"},
    "sports": {"en": "Sports", "es": "Deportes"},
    "food": {"en": "Food & Drink", "es": "Comida y bebida"},
    "words": {"en": "Words & Languages", "es": "Palabras e idiomas"},
    "myth": {"en": "Religion & Myth", "es": "Religión y mitología"},
    "technology": {"en": "Technology & Inventions", "es": "Tecnología e inventos"},
    "people": {"en": "Famous People", "es": "Personajes famosos"},
    "everyday": {"en": "Everyday Life", "es": "Vida cotidiana"},
}


@dataclass
class Clue:
    """One square's clue, in the game's language."""

    id: str
    column: str
    points: int
    text: str
    # The answer Richie says when nobody gets it, and every form the judge accepts (both languages).
    answer: str
    accept: list[str]
    hardness: float
    hidden: str | None = None
    used: bool = False


@dataclass
class Board:
    """A game's board: columns left to right, each with its clues from 200 to 800 points, and the final."""

    titles: list[str]
    clues: list[list[Clue]]
    final: Clue
    final_category: str
    language: str
    kids: bool = False
    ids: list[str] = field(default_factory=list)

    def clue(self, column: int, row: int) -> Clue | None:
        """Return the clue at ``column``, ``row`` when it exists and is not used yet."""
        if 0 <= column < len(self.clues) and 0 <= row < len(self.clues[column]):
            found = self.clues[column][row]
            return None if found.used else found
        return None

    @property
    def left(self) -> int:
        """How many squares are still on the board."""
        return sum(not clue.used for column in self.clues for clue in column)


class BoardError(RuntimeError):
    """There are not enough clues to make a board."""


def load_bank(path: Path) -> list[dict[str, Any]]:
    """Read the clue bank, one JSON entry a line."""
    with path.open(encoding="utf-8") as file:
        return [json.loads(line) for line in file if line.strip()]


def read_history(path: Path) -> dict[str, float]:
    """Return when each clue was last used (seconds since the epoch)."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return {str(key): float(value) for key, value in data.items()}
    except (OSError, ValueError, AttributeError):
        return {}


def remember(path: Path, ids: list[str], now: float | None = None) -> None:
    """Note that ``ids`` were played now, forgetting clues older than ``RECENT_DAYS``."""
    now = time.time() if now is None else now
    history = {key: when for key, when in read_history(path).items() if now - when < RECENT_DAYS * 86400}
    history.update(dict.fromkeys(ids, now))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(history), encoding="utf-8")


def _text(entry: dict[str, Any], language: str, kids: bool) -> str:
    source = entry.get("kids") if kids else entry.get("clue")
    return str((source or {}).get(language) or "").strip()


def imperial(entry: dict[str, Any]) -> bool:
    """Whether a clue (or its answer, or its kids version) measures in miles, feet, Fahrenheit and the like."""
    for language, pattern in _IMPERIAL.items():
        texts = [(entry.get(key) or {}).get(language) for key in ("clue", "answer", "kids")]
        if any(pattern.search(str(text)) for text in texts if text):
            return True
    return False


def _answers(entry: dict[str, Any]) -> set[str]:
    """Every accepted form of a clue's answer, plain, to keep two clues with one answer off a board."""
    forms = {
        plain(str(form)) for form in [*(entry.get("accept") or []), *(entry.get("answer") or {}).values()] if form
    }
    # "Dementor" and "Dementors" are one answer.
    return (forms | {form[:-1] for form in forms if form.endswith("s") and len(form) > 3}) - {""}


def _quarter_edges(values: list[float]) -> tuple[float, float, float]:
    ordered = sorted(values)
    return tuple(ordered[int(len(ordered) * share)] for share in (0.25, 0.5, 0.75))  # type: ignore[return-value]


def build_board(
    bank: list[dict[str, Any]],
    language: str,
    *,
    kids: bool = False,
    recent: dict[str, float] | None = None,
    rng: random.Random | None = None,
    now: float | None = None,
) -> Board:
    """Pick a fresh board for ``language``; raise BoardError when the bank cannot fill one."""
    rng = rng or random.Random()
    now = time.time() if now is None else now
    recent = recent or {}
    pool = [
        entry
        for entry in bank
        if _text(entry, language, kids)
        and entry.get("hardness") is not None
        and (not kids or not entry.get("adult"))
        and not imperial(entry)
        and now - recent.get(str(entry.get("id")), -math.inf) >= RECENT_DAYS * 86400
    ]
    if len(pool) < COLUMNS * len(POINTS) + 1:
        raise BoardError(f"only {len(pool)} clues to choose from")
    edges = _quarter_edges([float(entry["hardness"]) for entry in pool])

    def row_of(entry: dict[str, Any]) -> int:
        return sum(float(entry["hardness"]) >= edge for edge in edges)

    def by_rows(entries: list[dict[str, Any]]) -> list[list[dict[str, Any]]]:
        rows: list[list[dict[str, Any]]] = [[] for _ in POINTS]
        for entry in entries:
            rows[row_of(entry)].append(entry)
        # A row left empty borrows the nearest clue from a neighbouring row that can spare one, so a small themed
        # column (an edition's "Hogwarts" with no hard clue left) still plays, easiest to hardest.
        for row in range(len(rows)):
            if rows[row]:
                continue
            for distance in range(1, len(rows)):
                spare = [r for r in (row + distance, row - distance) if 0 <= r < len(rows) and len(rows[r]) >= 2]
                if spare:
                    donor = rows[spare[0]]
                    pick = min if spare[0] > row else max
                    chosen = pick(donor, key=lambda entry: float(entry["hardness"]))
                    donor.remove(chosen)
                    rows[row].append(chosen)
                    break
        return rows

    topics: dict[str, list[dict[str, Any]]] = {}
    categories: dict[str, list[dict[str, Any]]] = {}
    for entry in pool:
        column = entry.get("column") or {}
        if column.get("en"):
            topics.setdefault(str(column["en"]).lower(), []).append(entry)
        categories.setdefault(str(entry.get("category") or "everyday"), []).append(entry)

    # Themed columns first, at most one per broad category so a board is varied; broad categories fill the rest.
    candidates: list[tuple[str, list[list[dict[str, Any]]], str]] = []
    for entries in topics.values():
        rows = by_rows(entries)
        if all(rows):
            title = str((entries[0].get("column") or {}).get(language) or entries[0]["column"]["en"])
            candidates.append((title, rows, str(entries[0].get("category") or "")))
    rng.shuffle(candidates)
    chosen: list[tuple[str, list[list[dict[str, Any]]]]] = []
    used_categories: set[str] = set()
    for title, rows, category in candidates:
        if len(chosen) == COLUMNS - 1:  # one broad category keeps every board approachable
            break
        if category not in used_categories:
            chosen.append((title, rows))
            used_categories.add(category)
    broad = [key for key in categories if key not in used_categories and all(by_rows(categories[key]))]
    rng.shuffle(broad)
    for key in broad[: COLUMNS - len(chosen)]:
        # An edition's categories are its columns ("spells"), named by the column titles they carry.
        named = (categories[key][0].get("column") or {}).get(language) if key not in CATEGORY_NAMES else None
        title = named or CATEGORY_NAMES.get(key, {}).get(language, key.title())
        chosen.append((title, by_rows(categories[key])))
    # A small bank: a second themed column from a category already on the board.
    titles = {title for title, _ in chosen}
    for title, rows, _ in candidates:
        if len(chosen) < COLUMNS and title not in titles:
            chosen.append((title, rows))
            titles.add(title)
    if len(chosen) < COLUMNS:
        raise BoardError("not enough columns with a clue in every row")
    rng.shuffle(chosen)

    taken_answers: set[str] = set()
    ids: list[str] = []

    def make(entry: dict[str, Any], column: str, points: int) -> Clue:
        ids.append(str(entry["id"]))
        taken_answers.update(_answers(entry))
        answer = str((entry.get("answer") or {}).get(language) or (entry.get("answer") or {}).get("en") or "")
        return Clue(
            id=str(entry["id"]),
            column=column,
            points=points,
            text=_text(entry, language, kids),
            answer=answer,
            accept=[str(item) for item in entry.get("accept") or [answer]],
            hardness=float(entry["hardness"]),
        )

    def pick(entries: list[dict[str, Any]]) -> dict[str, Any]:
        fresh = [entry for entry in entries if str(entry["id"]) not in ids and not _answers(entry) & taken_answers]
        return rng.choice(fresh or entries)

    clues = [[make(pick(rows[row]), title, POINTS[row]) for row in range(len(POINTS))] for title, rows in chosen]
    hidden_cells = rng.sample([(column, row) for column in range(COLUMNS) for row in (1, 2, 3)], len(HIDDEN))
    columns_used: set[int] = set()
    for kind, (column, row) in zip(HIDDEN, hidden_cells):
        while column in columns_used:  # hidden squares in different columns
            column = (column + 1) % COLUMNS
        columns_used.add(column)
        clues[column][row].hidden = kind

    finals = [
        entry
        for entry in pool
        if row_of(entry) == 3
        and float(entry.get("appeal") or 0) >= FINAL_MIN_APPEAL
        and str(entry["id"]) not in ids
        and not _answers(entry) & taken_answers
    ]
    final_entry = rng.choice(finals or [entry for entry in pool if str(entry["id"]) not in ids])
    column = final_entry.get("column") or {}
    category = str(
        column.get(language)
        or CATEGORY_NAMES.get(str(final_entry.get("category")), {}).get(language)
        or final_entry.get("category")
    )
    final = make(final_entry, category, 0)
    logger.info("Big Brain board: %s; final in %s", ", ".join(title for title, _ in chosen), category)
    return Board(
        titles=[title for title, _ in chosen],
        clues=clues,
        final=final,
        final_category=category,
        language=language,
        kids=kids,
        ids=ids,
    )
