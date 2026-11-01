"""Write a Big Brain edition's clue bank from scratch: a themed set of clues, such as the Harry Potter films.

The TV quiz archive has too few clues on one franchise (151 on Harry Potter, mostly the books, 2026-11-01), so an
edition's clues are written new, column by column, and checked before they reach the bank:

1. Writing: Gemini, with Google Search, writes each column's clues in batches in English and Latin American
   Spanish (names as in the Latin American dub), each batch told the answers already used.
2. Facts: Gemini checks batches of clues: every fact true, the answer the only one that fits, and about the
   films (not only the books). Asked to verify, it answers from memory; for real-world facts (the cast, the
   making, release details) it also answers each clue blind with Google Search, which it then does use, and
   Jev judges that answer against ours.
3. Blind answer: another provider's model answers each clue without search (``CHECK_MODEL``) and Jev judges
   the match, as for the main bank, and whether the clue gives its answer away.
4. Ratings: Jev rates appeal and hardness for the edition's audience, and whether it suits children.

A clue reaches the bank when the facts hold, it gives nothing away, and the blind answer matches; a hard clue
(writer's difficulty 3 or 4) may miss the blind answer, since a model without search may not know production
trivia, when the fact check found it the only answer. A real-world clue must also match the searched answer. Each stage is cached as JSON lines in ``--out``; a rerun
pays only for what is missing. ``report.md`` sums it up and lists what was left out.

Usage: uv run python scripts/edition_bank.py --edition harry-potter --out ~/.local/share/reachy/game-data/trivia-bank/editions/harry-potter
"""

import os
import re
import json
import asyncio
import argparse
from typing import Any
from pathlib import Path
from collections import Counter

import httpx
from dotenv import find_dotenv, load_dotenv
from google import genai
from openai import AsyncOpenAI
from trivia_bank import (
    CHECK_MODEL,
    CHECK_PROMPT,
    REWRITE_MODEL,
    _CHECK_QUESTIONS,
    Costs,
    plain,
    ask_jev,
    accepted,
    complete,
    run_stage,
    answer_key,
    read_jsonl,
)
from google.genai import types


BATCH = 12
FACT_BATCH = 6
SEARCHED_PROMPT = "Search the web, then answer this trivia clue about {subject}. Reply with only the answer.\n\n{clue}"
MATCH_P = 0.5
GIVEAWAY_P = 0.5
KIDS_P = 0.5
ADULT_P = 0.5
MIN_APPEAL = 2.0

EDITIONS: dict[str, dict[str, Any]] = {
    "harry-potter": {
        "id": "hp",
        "subject": (
            "the eight Harry Potter films (2001 to 2011): what happens and is seen in them, and how they were made "
            "(cast, crew, directors, filming locations, sets, props, effects, music, release). Nothing that is "
            "only in the books, and nothing from Fantastic Beasts, the stage play, the theme parks or the games"
        ),
        "audience": "a family and their friends who have seen all eight Harry Potter films, some of them many times",
        "spanish": (
            "Spanish names are those of the Latin American Spanish dub and release: the films as 'Harry Potter y la "
            "piedra filosofal', '... y la cámara secreta', '... y el prisionero de Azkaban', '... y el cáliz de "
            "fuego', '... y la Orden del Fénix', '... y el misterio del príncipe', '... y las reliquias de la "
            "muerte'; 'el Sombrero Seleccionador', 'el Callejón Diagon', 'los mortífagos', 'el Giratiempo'. "
            "Character names stay as in the films (Hermione, Snape, Dumbledore)"
        ),
        "columns": {
            "hogwarts": ("Hogwarts", "Hogwarts", "the school: its houses, teachers, classes, ghosts, rooms, rules"),
            "spells": ("Spells & potions", "Hechizos y pociones", "spells, charms, curses and potions in the films"),
            "creatures": ("Magical creatures", "Criaturas mágicas", "creatures, beasts, pets and owls"),
            "friends": (
                "Harry's friends",
                "Los amigos de Harry",
                "the Weasleys, Hermione, the Order of the Phoenix, Dumbledore's Army and Harry's other allies",
            ),
            "dark": ("Dark wizards", "Magos oscuros", "Voldemort, the Death Eaters, Umbridge and other villains"),
            "objects": (
                "Magical objects",
                "Objetos mágicos",
                "wands, Horcruxes, the Deathly Hallows, the Marauder's Map and other magical things",
            ),
            "places": (
                "The wizarding world",
                "El mundo mágico",
                "places outside the castle: Diagon Alley, Hogsmeade, the Ministry, Gringotts, Azkaban, the Burrow",
            ),
            "quotes": (
                "Who said it?",
                "¿Quién lo dijo?",
                "a famous short line from the films (at most 12 words, quoted) and when it is said; the answer is "
                "the character who says it",
            ),
            "cast": (
                "The cast",
                "El elenco",
                "the actors: who played whom, their ages, other roles, casting stories; the answer is an actor or "
                "the character they played",
            ),
            "making": (
                "Behind the scenes",
                "Detrás de cámaras",
                "how the films were made: directors, composers, filming locations, sets, props, effects, makeup",
            ),
            "films": (
                "The eight films",
                "Las ocho películas",
                "the films themselves: which film something happens in, their order, release, length, records",
            ),
            "games": (
                "Quidditch & the Triwizard",
                "Quidditch y el Torneo",
                "Quidditch, the Triwizard Tournament, the Yule Ball and other contests",
            ),
        },
        "per_column": 48,
        # Real-world facts (names, dates, places, numbers) the checker must look up rather than remember.
        "search_columns": ("cast", "making", "films"),
    },
}

WRITE_PROMPT = """You write clues for Big Brain, a trivia game a home robot hosts for {audience}, in English or
Spanish. This is a special edition about {subject}.

Use Google Search to check every fact before you use it: the edition is only as good as its facts. Write {count}
new clues for the board column "{column}": {focus}.

Each clue is a statement that points at its answer, as the host reads it aloud and players name what it describes
("This Hogwarts ghost..." / "Este fantasma de Hogwarts..."). It must stand alone without the column title, take
under eight seconds to read (at most 25 words), have exactly one answer that fits, and never contain the answer's
words. Mix difficulty for the audience: about a quarter each of 1 (anyone who saw the films knows it), 2 (fans
know it), 3 (big fans know it), 4 (only superfans or film buffs know it).

Write the English and the Spanish clues separately, each natural in its language; the Spanish is neutral Latin
American as spoken in Venezuela. {spanish}. Give the answer in both languages and, in "also_accept", every other
form a judge should accept in either language: the other language's name, a surname alone, nicknames, short
forms. For difficulty 1 and 2, also write an easier clue a child of 8 to 11 who saw the films could answer, with
the same answer, as "kids"; otherwise "kids" is null.

Do not repeat these answers, already used in this column: {used}.

Reply with JSON only, a list:
[{{"difficulty": 1, "clue": {{"en": "...", "es": "..."}}, "answer": {{"en": "...", "es": "..."}}, "also_accept": ["..."],
"kids": null}}]"""

FACT_PROMPT = """Check these trivia clues about {subject}. Use Google Search for every one you are not certain of.

For each clue, decide:
- "true": every fact in the clue is correct, and so is the answer;
- "only": no other answer from the films fits the clue as well;
- "films": the clue is about the films or their making, not something only in the books.
Give "problem" in a few words when any is false, else null.

{clues}

Reply with JSON only, a list in the same order: [{{"n": 1, "true": true, "only": true, "films": true, "problem": null}}]"""


class EditionCosts(Costs):
    """Tokens by model, and Google searches."""

    def __init__(self) -> None:
        """Start at zero."""
        super().__init__()
        self.searches = 0


def edition_questions(audience: str) -> dict[str, dict[str, Any]]:
    """Return Jev's rating questions for an edition's audience."""
    return {
        "adult": {
            "type": "noul",
            "instructions": "Is `clue` or `answer` unsuitable for young children (sex, drugs, graphic violence, crude language)?",
        },
        "kids": {
            "type": "noul",
            "instructions": f"Could a child of about 8 to 11 among {audience} understand `clue` and know `answer`?",
        },
        "appeal": {
            "type": "score",
            "instructions": f"How good a clue would `clue` (answered by `answer`) make at a trivia night for {audience}?",
            "criteria": [
                "Bad: wrong-feeling, confusing or of no interest to them",
                "Weak: answerable but dull or very niche",
                "Fine: a fair clue that some of them would know",
                "Good: interesting and fair; many would enjoy trying",
                "Great: a satisfying 'I know this!' moment for most of them",
            ],
        },
        "hardness": {
            "type": "score",
            "instructions": f"How hard is `clue` (answered by `answer`) for {audience}?",
            "criteria": [
                "Almost everyone in the group would know it",
                "Most of them would know it",
                "About half of them would know it",
                "Only a few of them would know it",
                "Only a superfan or film buff would know it",
            ],
        },
    }


def json_reply(text: str) -> Any:
    """Return the JSON in a model's reply, without a code fence around it."""
    text = text.strip()
    fenced = re.search(r"```(?:json)?\s*(.*?)```", text, re.DOTALL)
    return json.loads(fenced.group(1) if fenced else text[text.find("[") :])


def count_gemini(costs: EditionCosts, response: Any) -> None:
    """Count a Gemini reply's tokens and its searches."""
    usage = response.usage_metadata
    if usage is not None:
        costs.add(
            REWRITE_MODEL,
            input=int(usage.prompt_token_count or 0),
            output=int(usage.candidates_token_count or 0),
            thinking=int(usage.thoughts_token_count or 0),
        )
    for candidate in response.candidates or []:
        grounding = candidate.grounding_metadata
        if grounding is not None:
            costs.searches += len(grounding.web_search_queries or [])


async def main() -> None:
    """Write, check and rate an edition's clues; then write the bank and the report."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--edition", choices=sorted(EDITIONS), required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--per-column", type=int, default=0, help="clues to write per column (0: the edition's)")
    parser.add_argument("--columns", default="", help="only these column keys, comma-separated")
    parser.add_argument("--concurrency", type=int, default=8)
    args = parser.parse_args()
    load_dotenv(find_dotenv(usecwd=True))
    edition = EDITIONS[args.edition]
    out: Path = args.out.expanduser()
    out.mkdir(parents=True, exist_ok=True)
    costs = EditionCosts()
    gemini = genai.Client(api_key=os.environ["GEMINI_API_KEY"])
    search = types.GenerateContentConfig(
        tools=[types.Tool(google_search=types.GoogleSearch())],
        thinking_config=types.ThinkingConfig(thinking_level=types.ThinkingLevel.LOW),
    )
    # On low thinking the checker answered from memory without searching (2026-11-01 sample: 0 searches).
    verify = types.GenerateContentConfig(
        tools=[types.Tool(google_search=types.GoogleSearch())],
        thinking_config=types.ThinkingConfig(thinking_level=types.ThinkingLevel.HIGH),
    )
    per_column = args.per_column or edition["per_column"]
    wanted = [key for key in edition["columns"] if not args.columns or key in args.columns.split(",")]

    # 1. Writing, column by column; batches in a column go one after another so each knows the answers used.
    drafts_path = out / "drafts.jsonl"
    drafts = read_jsonl(drafts_path)

    async def write_column(key: str) -> None:
        name_en, _, focus = edition["columns"][key]
        mine = [record for record in drafts.values() if record["column"] == key]
        failures = 0
        while len(mine) < per_column and failures < 3:
            used = sorted({str(record["answer"]["en"]) for record in mine}) or ["none yet"]
            prompt = WRITE_PROMPT.format(
                audience=edition["audience"],
                subject=edition["subject"],
                count=min(BATCH, per_column - len(mine)),
                column=name_en,
                focus=focus,
                spanish=edition["spanish"],
                used="; ".join(used),
            )
            try:
                response = await gemini.aio.models.generate_content(
                    model=REWRITE_MODEL, contents=prompt, config=search
                )
                count_gemini(costs, response)
                written = json_reply(response.text or "")
            except Exception as error:  # a flaky reply must not stop the column
                failures += 1
                print(f"write {key}: {type(error).__name__}: {str(error)[:120]}")
                continue
            for item in written if isinstance(written, list) else []:
                if not isinstance(item, dict) or not complete(item):
                    continue
                record = {"id": f"{edition['id']}-{key}-{len(mine) + 1:03d}", "column": key, **plain(item)}
                mine.append(record)
                drafts[record["id"]] = record
                with drafts_path.open("a", encoding="utf-8") as file:
                    file.write(json.dumps(record, ensure_ascii=False) + "\n")
        print(f"write {key}: {len(mine)} drafts")

    await asyncio.gather(*(write_column(key) for key in wanted))
    items = [record for record in drafts.values() if record["column"] in wanted]

    # 2. Facts, in batches of the same column.
    batches: list[dict[str, Any]] = []
    for key in wanted:
        column = [record for record in items if record["column"] == key]
        for start in range(0, len(column), FACT_BATCH):
            chunk = column[start : start + FACT_BATCH]
            batches.append({"id": ",".join(record["id"] for record in chunk), "records": chunk})

    async def check_facts(batch: dict[str, Any]) -> dict[str, Any]:
        listed = "\n".join(
            f"{n}. {record['clue']['en']} Answer: {record['answer']['en']}"
            for n, record in enumerate(batch["records"], 1)
        )
        response = await gemini.aio.models.generate_content(
            model=REWRITE_MODEL,
            contents=FACT_PROMPT.format(subject=edition["subject"], clues=listed),
            config=verify,
        )
        count_gemini(costs, response)
        verdicts = json_reply(response.text or "")
        if not isinstance(verdicts, list) or len(verdicts) != len(batch["records"]):
            raise ValueError("verdicts do not match the clues")
        return {"verdicts": {record["id"]: verdict for record, verdict in zip(batch["records"], verdicts)}}

    checked_facts = await run_stage(batches, out / "facts.jsonl", check_facts, args.concurrency, "facts")
    facts: dict[str, dict[str, Any]] = {}
    for record in checked_facts.values():
        facts.update(record["verdicts"])

    # 3 and 4. The blind answer and Jev's ratings, for every clue.
    openai = AsyncOpenAI(api_key=os.environ["OPENAI_API_KEY"])
    ratings_questions = edition_questions(edition["audience"])
    async with httpx.AsyncClient(timeout=60) as http:

        async def check(record: dict[str, Any]) -> dict[str, Any]:
            results: dict[str, Any] = {}
            for language in ("en", "es"):
                clue = record["clue"].get(language)
                if not clue:
                    continue
                response = await openai.responses.create(
                    model=CHECK_MODEL,
                    instructions=CHECK_PROMPT + " The clue is about the Harry Potter films.",
                    input=clue,
                    reasoning={"effort": "low"},
                    max_output_tokens=600,
                    store=False,
                )
                if response.usage is not None:
                    thinking = int(response.usage.output_tokens_details.reasoning_tokens or 0)
                    costs.add(
                        CHECK_MODEL,
                        input=response.usage.input_tokens,
                        output=response.usage.output_tokens - thinking,
                        thinking=thinking,
                    )
                blind = response.output_text.strip()
                state = {"clue": clue, "accepted_answers": accepted(record), "player_answer": blind}
                judged = await ask_jev(http, costs, state, _CHECK_QUESTIONS)
                results[language] = {
                    "blind": blind,
                    "match": round(float(judged["match"]["noul"]), 3),
                    "giveaway": round(float(judged["giveaway"]["noul"]), 3),
                }
            if record["column"] in edition["search_columns"]:
                response = await gemini.aio.models.generate_content(
                    model=REWRITE_MODEL,
                    contents=SEARCHED_PROMPT.format(subject=edition["subject"], clue=record["clue"]["en"]),
                    config=search,
                )
                count_gemini(costs, response)
                searched = (response.text or "").strip()
                state = {"clue": record["clue"]["en"], "accepted_answers": accepted(record), "player_answer": searched}
                judged = await ask_jev(http, costs, state, {"match": _CHECK_QUESTIONS["match"]})
                results["searched"] = {"blind": searched, "match": round(float(judged["match"]["noul"]), 3)}
            state = {"clue": record["clue"]["en"], "answer": record["answer"]["en"]}
            rated = await ask_jev(http, costs, state, ratings_questions)
            ratings = {key: round(float(rated[key]["noul"]), 3) for key in ("adult", "kids")}
            ratings |= {key: round(float(rated[key]["score"]), 2) for key in ("appeal", "hardness")}
            return {"check": results, "ratings": ratings}

        checked = await run_stage(items, out / "checks.jsonl", check, args.concurrency * 2, "checks")

    write_bank(out, edition, items, facts, checked, costs)


def verdict(record: dict[str, Any], fact: dict[str, Any] | None, check: dict[str, Any] | None) -> str | None:
    """Return why a clue stays out of the bank, or None when it goes in."""
    if fact is None or check is None:
        return "not checked"
    if not fact.get("true"):
        return f"fact: {fact.get('problem') or 'not true'}"
    if not fact.get("films"):
        return f"books: {fact.get('problem') or 'not in the films'}"
    results = dict(check["check"])
    searched = results.pop("searched", None)
    if any(result["giveaway"] >= GIVEAWAY_P for result in results.values()):
        return "gives the answer away"
    if check["ratings"]["appeal"] < MIN_APPEAL:
        return "dull"
    if searched is not None and searched["match"] < MATCH_P:
        return f"searched answer: {searched['blind']}"
    missed = [language for language, result in results.items() if result["match"] < MATCH_P]
    if missed and not (int(record.get("difficulty") or 0) >= 3 and fact.get("only")):
        return f"blind answer: {results[missed[0]]['blind']}"
    if not fact.get("only"):
        return f"not the only answer: {fact.get('problem')}"
    return None


def write_bank(
    out: Path,
    edition: dict[str, Any],
    items: list[dict[str, Any]],
    facts: dict[str, dict[str, Any]],
    checked: dict[str, dict[str, Any]],
    costs: EditionCosts,
) -> None:
    """Write ``bank.jsonl`` (the game's format) and ``report.md``."""
    reasons: dict[str, str] = {}
    kept: list[dict[str, Any]] = []
    seen: Counter[str] = Counter()
    for record in items:
        reason = verdict(record, facts.get(record["id"]), checked.get(record["id"]))
        key = answer_key(str(record["answer"]["en"]))
        if reason is None and seen[key] >= 2:
            reason = "third clue with this answer"
        if reason is not None:
            reasons[record["id"]] = reason
            continue
        seen[key] += 1
        kept.append(record)
    with (out / "bank.jsonl").open("w", encoding="utf-8") as file:
        for record in kept:
            ratings = checked[record["id"]]["ratings"]
            name_en, name_es, _ = edition["columns"][record["column"]]
            kids_ok = ratings["kids"] >= KIDS_P and ratings["adult"] < ADULT_P
            entry = {
                "id": record["id"],
                "category": record["column"],
                "column": {"en": name_en, "es": name_es},
                "difficulty": record.get("difficulty"),
                "clue": record["clue"],
                "answer": record["answer"],
                "accept": accepted(record),
                "kids": record.get("kids") if kids_ok and isinstance(record.get("kids"), dict) else None,
                "adult": ratings["adult"] >= ADULT_P,
                "appeal": ratings["appeal"],
                "hardness": ratings["hardness"],
            }
            file.write(json.dumps(entry, ensure_ascii=False) + "\n")

    by_column = Counter(record["column"] for record in kept)
    why = Counter(reason.split(":")[0] for reason in reasons.values())
    dollars = sum(costs.dollars(model) for model in costs.tokens)
    searches = costs.searches
    lines = [
        f"# {edition['id']} edition bank",
        "",
        f"- Written: {len(items)}; in the bank: {len(kept)}; left out: {len(reasons)}",
        f"- Left out by reason: {', '.join(f'{reason} {count}' for reason, count in why.most_common())}",
        f"- By column: {', '.join(f'{column} {count}' for column, count in by_column.most_common())}",
        f"- With a kids clue: {sum(1 for record in kept if record.get('kids'))}",
        f"- Model cost: ${dollars:.2f} plus {searches} Google searches",
        "",
        "## Left out (first 60)",
        "",
    ]
    by_id = {record["id"]: record for record in items}
    lines += [
        f"- {by_id[key]['clue']['en']} — **{by_id[key]['answer']['en']}**: {reason}"
        for key, reason in list(reasons.items())[:60]
    ]
    lines += ["", "## A sample of the bank", ""]
    for record in kept[:: max(1, len(kept) // 40)]:
        ratings = checked[record["id"]]["ratings"]
        lines.append(
            f"- [{record['column']}, hardness {ratings['hardness']}] {record['clue']['en']} — **{record['answer']['en']}**"
            f" / {record['clue'].get('es')} — **{record['answer'].get('es')}**"
        )
    (out / "report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"bank: {len(kept)} clues; report: {out / 'report.md'}")


if __name__ == "__main__":
    asyncio.run(main())
