"""Build Big Brain's clue bank from a TV quiz archive CSV (docs/plans/party-games.md, "Question data").

Three stages, each cached as JSON lines in ``--out`` so a rerun pays only for what is missing:

1. Tags: Jev answers narrow questions about every sampled clue (needs the broadcast's picture? known only in
   the US? English wordplay? could the answer be out of date? fine for kids? how good for a game night?).
2. Rewrites: clues that pass become new statement-style clues in our own words ("This Swedish-born actress…"),
   in English and in Spanish written separately, with answers in both languages and every other form a judge
   should accept, plus a kids version when Jev thinks a child could know it (``REWRITE_MODEL``).
3. Checks: another provider's model answers each new clue blind (``CHECK_MODEL``) and Jev judges whether that
   answer matches ours, in each language. A mismatch flags the clue for review; it also tests the answer judge
   the game will use.

The archive's own wording never leaves the work folder: the bank keeps only the rewrites. ``report.md`` sums up
counts, costs and examples; ``review.csv`` lists every rewrite next to its original for reading.

Usage: uv run python scripts/trivia_bank.py --csv trivia.csv --out ~/.local/share/reachy/game-data/trivia-sample
"""

import os
import re
import csv
import json
import random
import asyncio
import argparse
from typing import Any
from pathlib import Path
from collections import Counter
from collections.abc import Callable, Iterable, Awaitable

import httpx
from dotenv import find_dotenv, load_dotenv
from google import genai
from openai import AsyncOpenAI
from google.genai import types


JEV_URL = "https://api.typesafe.ai/v1/systemone"
JEV_MODEL = "jev-latest"
REWRITE_MODEL = "gemini-3.8-flash"
CHECK_MODEL = "gpt-5.6-luna"
# Dollars per million tokens, as in usage_costs.py.
PRICES = {
    JEV_MODEL: {"input": 0.042, "output": 0.0, "thinking": 0.0},
    REWRITE_MODEL: {"input": 0.75, "output": 3.75, "thinking": 3.75},
    CHECK_MODEL: {"input": 0.20, "output": 1.20, "thinking": 1.20},
}

# A clue is rewritten only when Jev is fairly sure it is none of these and rates it at least "fine".
MAX_FLAG_P = 0.5
MIN_APPEAL = 2.0
# What the full run would keep, for the report.
GOOD_APPEAL = 2.5
# The hard pick still wants clues people enjoy.
HARD_MIN_APPEAL = 2.2
KIDS_P = 0.5
MATCH_P = 0.5
GIVEAWAY_P = 0.5

CATEGORIES = {
    "science": "Ciencia",
    "nature": "Naturaleza y animales",
    "geography": "Geografía",
    "history": "Historia",
    "arts": "Arte y literatura",
    "music": "Música",
    "screen": "Cine y TV",
    "sports": "Deportes",
    "food": "Comida y bebida",
    "words": "Palabras e idiomas",
    "myth": "Religión y mitología",
    "technology": "Tecnología e inventos",
    "people": "Personajes famosos",
    "everyday": "Vida cotidiana",
}

_FLAGS: dict[str, str] = {
    "media": (
        "Does `clue` depend on something only the original broadcast had: a picture, video or sound ('seen here', "
        "'shown on the monitor', 'heard here'), or a presenter demonstrating or reading it from somewhere?"
    ),
    "us_local": (
        "To get `answer` from `clue`, does a player need knowledge that is mostly known only inside the United "
        "States: its state capitals and small cities, politics and politicians other than the most famous "
        "presidents, local history, college sports, TV shows and people famous mainly there, local brands, laws? "
        "American things known around the world do not count: world-famous singers, actors, athletes and films "
        "(Mariah Carey, Tony Hawk, Hollywood classics), global brands, and events like the Moon landing."
    ),
    "wordplay": (
        "Does `clue` or `category` rely on English wordplay (rhymes, puns, letters or spelling, words hidden in "
        "words, anagrams, sound-alikes, or the English form of a word) so that it would stop working in Spanish?"
    ),
    "dated": (
        "Could `answer` have changed since `clue` was written: a current record, office holder or ranking, "
        "'currently', 'newest', 'latest', a population, or anything described as present-day?"
    ),
    "adult": "Is `clue` or `answer` unsuitable for young children (sex, drugs, graphic violence, crude language)?",
    "kids": "Could a child of about 7 to 9 understand `clue` and have a fair chance of knowing `answer`?",
}
_TAG_QUESTIONS: dict[str, dict[str, Any]] = {
    **{key: {"type": "noul", "instructions": text} for key, text in _FLAGS.items()},
    "appeal": {
        "type": "score",
        "instructions": (
            "How good a clue would `clue` (answered by `answer`) make at a home trivia night for curious, "
            "well-educated adults in Latin America who follow world news and American pop culture, playing in "
            "Spanish or English?"
        ),
        "criteria": [
            "Bad: obscure, trivial, confusing or of no interest to them",
            "Weak: answerable but dull or very niche",
            "Fine: a fair clue that some of them would know",
            "Good: interesting and fair; many would enjoy trying",
            "Great: a satisfying 'I know this!' moment for most of the group",
        ],
    },
}
_HARDNESS_QUESTION: dict[str, dict[str, Any]] = {
    "hardness": {
        "type": "score",
        "instructions": (
            "How hard is `clue` (answered by `answer`) for curious, well-read adults in Latin America who follow "
            "world news and American pop culture?"
        ),
        "criteria": [
            "Almost everyone in the group would know it",
            "Most of them would know it",
            "About half of them would know it",
            "Only a few of them would know it",
            "Only a real buff would know it",
        ],
    }
}
_CHECK_QUESTIONS: dict[str, dict[str, Any]] = {
    "giveaway": {
        "type": "noul",
        "instructions": (
            "Does `clue` contain the words of its answer: any of `accepted_answers`, a distinctive word from one "
            "(not a generic word like 'the', 'river' or 'war'), or a direct translation of one (a clue saying "
            "'Jerez' when the answer is sherry, or 'Dog Star' when it is the dog days)? Being easy to answer, "
            "or famous, does not count."
        ),
    },
    "match": {
        "type": "noul",
        "instructions": (
            "Is `player_answer` a correct answer to `clue`, given that any of `accepted_answers` is right? Accept "
            "either language, small spelling or hearing slips, a surname alone for a person, and missing or extra "
            "articles. Too broad an answer, or a different thing, person or number, is wrong."
        ),
    },
}

REWRITE_PROMPT = """You write clues for Big Brain, a trivia game a home robot hosts for a Venezuelan family and
their friends, in English or Spanish. Below is a fact from an old quiz show: a category, a clue, its answer, and
the clue's value (higher was harder).

Write a new clue about the same fact, in your own words. Use a statement that points at the answer, as the host
reads it aloud and players name what it describes: "This Swedish-born actress retired in 1941…", "Esta actriz
nacida en Suecia…". Do not copy the old clue's wording; rephrase it, and add or swap a detail if that makes it
fairer. It must stand alone without the category, take under eight seconds to read (at most 25 words), have one
clear answer, and still be true today.

Write the English and the Spanish clues separately, each natural in its language. The Spanish is neutral Latin
American as spoken in Venezuela, uses Spanish names (Copérnico, Londres), never needs English to answer, and
names films, books and songs by their Latin American title. If the fact cannot work in Spanish, set "es" to
null.

Give the answer in both languages, and in "also_accept" every other form a judge should accept in any
language: the original title, the titles used in Spain and in Latin America, a surname alone, common short
forms.

{kids_instruction}

Pick the category key from: {categories}. Also give a specific topic in both languages, two to four words, that
could title a board column of several clues: specific enough to be fun, general enough that other clues share
it, and not a pun ("World rivers" / "Ríos del mundo", "1980s films" / "Películas de los 80", "Greek
mythology" / "Mitología griega", "Famous painters" / "Pintores famosos"). Rate difficulty for adults: 1 most know it, 2 many know it,
3 a few know it, 4 only buffs. If the fact is wrong, ambiguous or out of date, set "skip" to the reason and the
rest to null.

Category: {category}
Clue: {clue}
Answer: {answer}
Value: {value} ({round} round)

Reply with JSON only:
{{"skip": null, "category": "...", "topic": {{"en": "...", "es": "..."}}, "difficulty": 1, "clue": {{"en": "...", "es": "..."}},
"answer": {{"en": "...", "es": "..."}}, "also_accept": ["..."], "kids": null}}"""

KIDS_INSTRUCTION = """Also write an easier clue for children of about 7 to 9 (simple words, a hint built in if
it helps), with the same answer, as "kids": {"en": "...", "es": "..."}."""
NO_KIDS_INSTRUCTION = 'Set "kids" to null.'

CHECK_PROMPT = "This trivia clue describes something. Reply with only what it describes, in the clue's language."


class Costs:
    """Tokens by model, for the report."""

    def __init__(self) -> None:
        """Start at zero."""
        self.tokens: dict[str, Counter[str]] = {model: Counter() for model in PRICES}
        self.requests: Counter[str] = Counter()

    def add(self, model: str, **tokens: int) -> None:
        """Count one request's tokens."""
        self.requests[model] += 1
        self.tokens[model].update(tokens)

    def dollars(self, model: str) -> float:
        """Return the dollars spent on ``model``."""
        return sum(count * PRICES[model][kind] / 1e6 for kind, count in self.tokens[model].items())


def load_sample(path: Path, size: int, seed: int) -> list[dict[str, str]]:
    """Read the archive CSV and return ``size`` random clues, each with a stable id."""
    with path.open(newline="", encoding="utf-8", errors="replace") as file:
        reader = csv.reader(file)
        next(reader)
        rows = [
            {"id": str(index), "round": r[0].strip(), "category": r[1], "value": r[2], "clue": r[3], "answer": r[4]}
            for index, r in enumerate(reader)
            if len(r) >= 5
        ]
    if size <= 0 or size >= len(rows):
        return rows
    return random.Random(seed).sample(rows, size)


def read_jsonl(path: Path) -> dict[str, dict[str, Any]]:
    """Return the records already written to ``path`` by id."""
    if not path.exists():
        return {}
    records = (json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip())
    return {record["id"]: record for record in records}


async def run_stage(
    items: list[dict[str, Any]],
    path: Path,
    work: Callable[[dict[str, Any]], Awaitable[dict[str, Any]]],
    concurrency: int,
    label: str,
) -> dict[str, dict[str, Any]]:
    """Run ``work`` on every item not yet in ``path``, appending each result as it finishes."""
    done = read_jsonl(path)
    todo = [item for item in items if item["id"] not in done]
    gate = asyncio.Semaphore(concurrency)
    failures = 0

    async def one(item: dict[str, Any]) -> None:
        nonlocal failures
        async with gate:
            for attempt in range(3):
                try:
                    record = {"id": item["id"], **await work(item)}
                    break
                except Exception as error:  # a flaky provider must not stop the batch
                    if attempt == 2:
                        failures += 1
                        print(f"{label}: {item['id']} failed: {type(error).__name__}: {str(error)[:120]}")
                        return
                    await asyncio.sleep(2 * (attempt + 1))
            done[item["id"]] = record
            with path.open("a", encoding="utf-8") as file:
                file.write(json.dumps(record, ensure_ascii=False) + "\n")

    await asyncio.gather(*(one(item) for item in todo))
    print(f"{label}: {len(todo) - failures} new, {failures} failed, {len(done)} in total")
    return done


async def ask_jev(
    client: httpx.AsyncClient, costs: Costs, state: dict[str, Any], questions: dict[str, Any]
) -> dict[str, Any]:
    """Ask Jev ``questions`` about ``state`` and return the answers by question id."""
    response = await client.post(
        JEV_URL,
        headers={"Authorization": f"Bearer {os.environ['TYPESAFE_API_KEY']}"},
        json={"model": JEV_MODEL, "state": state, "questions": questions},
    )
    response.raise_for_status()
    body = response.json()
    usage = body.get("usage") or {}
    costs.add(JEV_MODEL, input=int(usage.get("input_tokens") or 0))
    answers: dict[str, Any] = body["answers"]
    return answers


def tag_summary(answers: dict[str, Any]) -> dict[str, float]:
    """Return each flag's probability and the appeal score."""
    tags = {key: round(float(answers[key]["noul"]), 3) for key in _FLAGS}
    tags["appeal"] = round(float(answers["appeal"]["score"]), 2)
    return tags


def passes(tags: dict[str, float], min_appeal: float = MIN_APPEAL) -> bool:
    """Whether a tagged clue is worth rewriting."""
    flagged = any(tags[key] >= MAX_FLAG_P for key in ("media", "us_local", "wordplay", "dated"))
    return not flagged and tags["appeal"] >= min_appeal


def answer_key(answer: str) -> str:
    """Return ``answer`` reduced for spotting the same answer in several clues."""
    words = re.sub(r"[^a-z0-9 ]", "", answer.lower()).split()
    return " ".join(words[1:] if words[:1] in (["the"], ["a"], ["an"]) else words)


def choose(
    candidates: list[dict[str, Any]],
    top: int,
    per_answer: int,
    order: Callable[[dict[str, Any]], float],
    taken: Iterable[str] = (),
) -> list[dict[str, Any]]:
    """Return the first ``top`` candidates by ``order`` (all when 0), at most ``per_answer`` clues per answer.

    ``taken`` holds the answers of clues already rewritten; they count toward ``per_answer``.
    """
    chosen: list[dict[str, Any]] = []
    seen: Counter[str] = Counter(answer_key(answer) for answer in taken)
    for clue in sorted(candidates, key=order):
        key = answer_key(clue["answer"])
        if seen[key] >= per_answer:
            continue
        seen[key] += 1
        chosen.append(clue)
        if top and len(chosen) >= top:
            break
    return chosen


def plain(value: Any) -> Any:
    """Return ``value`` with the Markdown emphasis the writer sometimes adds removed, in every string."""
    if isinstance(value, str):
        return re.sub(r"\*+([^*]+)\*+", r"\1", value).replace("*", "")
    if isinstance(value, list):
        return [plain(item) for item in value]
    if isinstance(value, dict):
        return {key: plain(item) for key, item in value.items()}
    return value


def complete(record: dict[str, Any]) -> bool:
    """Whether a rewrite has an English clue and an answer."""
    clue, answer = record.get("clue"), record.get("answer")
    return isinstance(clue, dict) and bool(clue.get("en")) and isinstance(answer, dict) and bool(answer.get("en"))


def accepted(record: dict[str, Any]) -> list[str]:
    """Return every answer the judge accepts, in both languages."""
    answers = [record["answer"].get("en"), record["answer"].get("es"), *(record.get("also_accept") or [])]
    return list(dict.fromkeys(str(answer) for answer in answers if answer))


async def main() -> None:
    """Sample, tag, rewrite and check; then write the report."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--csv", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--size", type=int, default=1000, help="clues to sample; 0 for the whole archive")
    parser.add_argument("--rewrite-top", type=int, default=0, help="rewrite only the best N by appeal; 0 for all")
    parser.add_argument("--per-answer", type=int, default=2, help="at most this many clues with the same answer")
    parser.add_argument(
        "--pick",
        choices=("appeal", "hard"),
        default="appeal",
        help="rewrite the most enjoyable clues, or the hardest still-enjoyable ones not yet rewritten",
    )
    parser.add_argument("--seed", type=int, default=20261101)
    parser.add_argument("--concurrency", type=int, default=12)
    args = parser.parse_args()
    load_dotenv(find_dotenv(usecwd=True))  # run from the repo, wherever the script file sits
    out: Path = args.out.expanduser()
    out.mkdir(parents=True, exist_ok=True)
    costs = Costs()
    sample = load_sample(args.csv.expanduser(), args.size, args.seed)
    by_id = {clue["id"]: clue for clue in sample}

    async with httpx.AsyncClient(timeout=60) as http:

        async def tag(clue: dict[str, Any]) -> dict[str, Any]:
            state = {key: clue[key] for key in ("category", "clue", "answer")}
            return {"tags": tag_summary(await ask_jev(http, costs, state, _TAG_QUESTIONS))}

        tagged = await run_stage(sample, out / "tags.jsonl", tag, args.concurrency, "tags")

        gemini = genai.Client(api_key=os.environ["GEMINI_API_KEY"])
        passing = [by_id[key] | record for key, record in tagged.items() if passes(record["tags"])]

        async def rate(clue: dict[str, Any]) -> dict[str, Any]:
            state = {key: clue[key] for key in ("category", "clue", "answer")}
            answers = await ask_jev(http, costs, state, _HARDNESS_QUESTION)
            return {"hardness": round(float(answers["hardness"]["score"]), 2)}

        hardness: dict[str, float] = {}
        if args.pick == "hard":
            rated = await run_stage(passing, out / "hardness.jsonl", rate, args.concurrency, "hardness")
            hardness = {key: record["hardness"] for key, record in rated.items()}
            done = read_jsonl(out / "rewrites.jsonl")
            candidates = choose(
                [clue for clue in passing if clue["id"] not in done and clue["tags"]["appeal"] >= HARD_MIN_APPEAL],
                args.rewrite_top,
                args.per_answer,
                lambda clue: -hardness.get(clue["id"], 0.0),
                [by_id[key]["answer"] for key in done if key in by_id],
            )
            candidates = [*(by_id[key] | tagged[key] for key in done if key in by_id), *candidates]
        else:
            hardness = {key: record["hardness"] for key, record in read_jsonl(out / "hardness.jsonl").items()}
            candidates = choose(passing, args.rewrite_top, args.per_answer, lambda clue: -clue["tags"]["appeal"])

        async def rewrite(clue: dict[str, Any]) -> dict[str, Any]:
            kids = clue["tags"]["kids"] >= KIDS_P and clue["tags"]["adult"] < MAX_FLAG_P
            prompt = REWRITE_PROMPT.format(
                kids_instruction=KIDS_INSTRUCTION if kids else NO_KIDS_INSTRUCTION,
                categories=", ".join(CATEGORIES),
                category=clue["category"],
                clue=clue["clue"],
                answer=clue["answer"],
                value=clue["value"],
                round=clue["round"],
            )
            response = await gemini.aio.models.generate_content(
                model=REWRITE_MODEL,
                contents=prompt,
                config=types.GenerateContentConfig(
                    response_mime_type="application/json",
                    thinking_config=types.ThinkingConfig(thinking_level=types.ThinkingLevel.LOW),
                ),
            )
            usage = response.usage_metadata
            if usage is not None:
                costs.add(
                    REWRITE_MODEL,
                    input=int(usage.prompt_token_count or 0),
                    output=int(usage.candidates_token_count or 0),
                    thinking=int(usage.thoughts_token_count or 0),
                )
            written = json.loads(response.text or "")
            if not isinstance(written, dict) or not (written.get("skip") or complete(written)):
                raise ValueError("no clue")
            return {"rewrite": written}

        rewritten = await run_stage(candidates, out / "rewrites.jsonl", rewrite, args.concurrency, "rewrites")

        openai = AsyncOpenAI(api_key=os.environ["OPENAI_API_KEY"])
        usable = [
            {"id": key} | record
            for key, record in rewritten.items()
            if not record["rewrite"].get("skip") and complete(record["rewrite"])
        ]

        async def check(item: dict[str, Any]) -> dict[str, Any]:
            record = item["rewrite"]
            results: dict[str, Any] = {}
            for language in ("en", "es"):
                clue = (record.get("clue") or {}).get(language)
                if not clue:
                    continue
                response = await openai.responses.create(
                    model=CHECK_MODEL,
                    instructions=CHECK_PROMPT,
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
            return {"check": results}

        checked = await run_stage(usable, out / "checks.jsonl", check, args.concurrency, "checks")

    write_report(out, sample, tagged, rewritten, checked, hardness, costs)


def write_report(
    out: Path,
    sample: list[dict[str, str]],
    tagged: dict[str, dict[str, Any]],
    rewritten: dict[str, dict[str, Any]],
    checked: dict[str, dict[str, Any]],
    hardness: dict[str, float],
    costs: Costs,
) -> None:
    """Write ``report.md`` (counts, costs, examples) and ``review.csv`` (every rewrite next to its original)."""
    by_id = {clue["id"]: clue for clue in sample}
    flag_counts = Counter(key for record in tagged.values() for key in _FLAGS if record["tags"][key] >= MAX_FLAG_P)
    low_appeal = sum(1 for record in tagged.values() if record["tags"]["appeal"] < MIN_APPEAL)
    passed = [key for key, record in tagged.items() if passes(record["tags"])]
    good = [key for key, record in tagged.items() if passes(record["tags"], GOOD_APPEAL)]
    skipped = {key: r["rewrite"]["skip"] for key, r in rewritten.items() if r["rewrite"].get("skip")}
    malformed = [key for key, r in rewritten.items() if key not in skipped and not complete(r["rewrite"])]
    kept = [key for key in rewritten if key not in skipped and key not in malformed]
    with_es = [key for key in kept if (rewritten[key]["rewrite"].get("clue") or {}).get("es")]
    with_kids = [key for key in kept if rewritten[key]["rewrite"].get("kids")]

    def result(key: str, language: str) -> dict[str, Any]:
        found: dict[str, Any] = checked.get(key, {}).get("check", {}).get(language) or {}
        return found

    def verified(key: str, language: str) -> bool:
        return float(result(key, language).get("match", 0.0)) >= MATCH_P

    def gives_away(key: str) -> bool:
        return any(float(result(key, language).get("giveaway", 0.0)) >= GIVEAWAY_P for language in ("en", "es"))

    giveaways = [key for key in kept if gives_away(key)]
    en_ok = sum(verified(key, "en") for key in kept)
    es_ok = sum(verified(key, "es") for key in with_es)
    disagree = [
        key
        for key in kept
        if key in checked and not (verified(key, "en") and (key not in with_es or verified(key, "es")))
    ]
    categories = Counter(rewritten[key]["rewrite"].get("category") for key in kept)
    topics = Counter(str((rewritten[key]["rewrite"].get("topic") or {}).get("en", "")).lower() for key in kept)
    columns = sum(1 for count in topics.values() if count >= 4)
    difficulty = Counter(rewritten[key]["rewrite"].get("difficulty") for key in kept)
    total = sum(costs.dollars(model) for model in PRICES)
    full = 216_930

    lines = [
        "# Big Brain clue bank",
        "",
        f"- Sample: {len(sample)} random clues; tagged {len(tagged)}",
        f"- Jev says yes (p ≥ {MAX_FLAG_P}): "
        + ", ".join(f"{key} {count}" for key, count in flag_counts.most_common()),
        f"- Appeal under {MIN_APPEAL} (of 0–4): {low_appeal}",
        f"- Passed to rewriting: {len(passed)} ({len(good)} with appeal at least {GOOD_APPEAL}, about "
        f"{round(full * len(good) / max(len(sample), 1)):,} in the whole archive); the writer skipped "
        f"{len(skipped)}; kept {len(kept)}",
        f"- Malformed rewrites left out: {len(malformed)}",
        f"- Spanish versions: {len(with_es)}; kids versions: {len(with_kids)}",
        f"- Blind check agrees: English {en_ok} of {len(kept)}, Spanish {es_ok} of {len(with_es)}; "
        f"{len(disagree)} to review",
        f"- Clue gives the answer away (either language): {len(giveaways)}",
        f"- Categories: {dict(categories.most_common())}",
        f"- Topics: {len(topics)}, {columns} with at least 4 clues (a full column); most common: "
        f"{dict(topics.most_common(25))}",
        f"- Difficulty: {dict(sorted(difficulty.items(), key=lambda item: str(item[0])))}",
        f"- Jev hardness of kept clues (0 everyone knows – 4 buffs only), rounded: "
        f"{dict(sorted(Counter(round(hardness[key]) for key in kept if key in hardness).items()))}",
        "",
        "## Cost (this run's requests only)",
        "",
        "| Model | Requests | Tokens | Dollars |",
        "|---|---|---|---|",
    ]
    for model in PRICES:
        tokens = sum(costs.tokens[model].values())
        lines.append(f"| {model} | {costs.requests[model]} | {tokens:,} | ${costs.dollars(model):.4f} |")
    lines += [f"| total | | | ${total:.4f} |", "", "## Examples kept (best appeal first)", ""]
    for key in sorted(kept, key=lambda key: -tagged[key]["tags"]["appeal"])[:30]:
        record = rewritten[key]["rewrite"]
        lines.append(
            f"- **{by_id[key]['category']}** → {record.get('category')}, difficulty {record.get('difficulty')}, "
            f"appeal {tagged[key]['tags']['appeal']}; accepts: {', '.join(accepted(record))}"
        )
        for language in ("en", "es"):
            clue = (record.get("clue") or {}).get(language)
            if clue:
                check = result(key, language)
                lines.append(
                    f"  - {language}: {clue} — **{record['answer'].get(language)}** "
                    f"(blind: {check.get('blind', '?')}, match {check.get('match', '?')})"
                )
        if record.get("kids"):
            lines.append(f"  - kids: {record['kids'].get('es', '')}")
    lines += ["", "## Skipped by the writer", ""]
    lines += [f"- {by_id[key]['category']}: {reason}" for key, reason in list(skipped.items())[:15]]
    lines += ["", "## Blind check disagrees", ""]
    for key in disagree[:20]:
        record = rewritten[key]["rewrite"]
        lines.append(
            f"- {record['clue']['en']} — ours **{', '.join(accepted(record))}**, "
            f"blind: {result(key, 'en').get('blind')} / {result(key, 'es').get('blind')}"
        )
    lines += ["", "## Clue gives the answer away", ""]
    for key in giveaways[:20]:
        record = rewritten[key]["rewrite"]
        lines.append(f"- {record['clue']['en']} — **{record['answer'].get('en')}**")
    bank = [key for key in kept if key not in giveaways and key not in disagree]
    lines.insert(9, f"- In the bank (agreed in every language, no giveaway): {len(bank)}")
    with (out / "bank.jsonl").open("w", encoding="utf-8") as file:
        for key in bank:
            record = rewritten[key]["rewrite"]
            tags = tagged[key]["tags"]
            entry = plain(
                {
                    "id": key,
                    "category": record.get("category"),
                    "topic": record.get("topic"),
                    "difficulty": record.get("difficulty"),
                    "value": by_id[key]["value"],
                    "round": by_id[key]["round"],
                    "clue": record["clue"],
                    "answer": record["answer"],
                    "accept": accepted(record),
                    "kids": record.get("kids"),
                    "adult": tags["adult"] >= MAX_FLAG_P,
                    "appeal": tags["appeal"],
                    "hardness": hardness.get(key),
                }
            )
            file.write(json.dumps(entry, ensure_ascii=False) + "\n")
    (out / "report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")

    with (out / "review.csv").open("w", newline="", encoding="utf-8") as file:
        writer = csv.writer(file)
        writer.writerow(
            [
                "id",
                "original category",
                "original clue",
                "original answer",
                "value",
                "category",
                "topic",
                "difficulty",
                "en clue",
                "es clue",
                "answers accepted",
                "en blind",
                "en match",
                "es blind",
                "es match",
                "giveaway en",
                "giveaway es",
                "kids es",
                "kids en",
                "appeal",
                "us_local",
                "wordplay",
                "dated",
                "adult",
                "kids p",
            ]
        )
        for key in kept:
            record = rewritten[key]["rewrite"]
            tags = tagged[key]["tags"]
            clue, kids = record.get("clue") or {}, record.get("kids") or {}
            writer.writerow(
                [
                    key,
                    by_id[key]["category"],
                    by_id[key]["clue"],
                    by_id[key]["answer"],
                    by_id[key]["value"],
                    record.get("category"),
                    (record.get("topic") or {}).get("es"),
                    record.get("difficulty"),
                    clue.get("en"),
                    clue.get("es"),
                    " | ".join(accepted(record)),
                    result(key, "en").get("blind"),
                    result(key, "en").get("match"),
                    result(key, "es").get("blind"),
                    result(key, "es").get("match"),
                    result(key, "en").get("giveaway"),
                    result(key, "es").get("giveaway"),
                    kids.get("es"),
                    kids.get("en"),
                    tags["appeal"],
                    tags["us_local"],
                    tags["wordplay"],
                    tags["dated"],
                    tags["adult"],
                    tags["kids"],
                ]
            )
    print(f"report: {out / 'report.md'}")


if __name__ == "__main__":
    asyncio.run(main())
