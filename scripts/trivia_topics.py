"""Merge near-duplicate topics in Big Brain's clue bank into board columns (docs/plans/party-games.md).

The writer names each clue's topic freely, so "human anatomy" and "human body", or "1980s movies" and "1980s
films", end up apart. Every topic name is embedded; going from the most common topic down, each joins the most
similar column so far when the two are at least ``--similarity`` alike, and starts a new column otherwise.
Starting from the most common keeps chains from drifting ("south american cities" never reaches "south
american nations" through a third topic). Each bank entry gains ``column`` ({"en", "es"}): the column's own
topic in both languages. The bank is rewritten in place after a copy is kept as ``bank.before-columns.jsonl``.

Usage: uv run python scripts/trivia_topics.py --bank ~/.local/share/reachy/game-data/trivia-bank/bank.jsonl
"""

import os
import json
import shutil
import argparse
from typing import Any
from pathlib import Path
from collections import Counter

import numpy as np
from dotenv import find_dotenv, load_dotenv
from openai import OpenAI


EMBEDDING_MODEL = "text-embedding-3-small"
# 0.75 merged "south american cities" into "south american nations"; 0.85 kept merges like "horror writers" /
# "horror authors" and gave as many full columns (2026-11-01).
SIMILARITY = 0.85


def _topic(entry: dict[str, Any], language: str) -> str:
    return str((entry.get("topic") or {}).get(language) or "").strip()


def columns(names: list[str], vectors: np.ndarray, similarity: float) -> dict[str, str]:
    """Map each topic name (most common first) to the column it joins."""
    heads: list[int] = []
    head_of: dict[str, str] = {}
    for index, name in enumerate(names):
        if heads:
            scores = vectors[heads] @ vectors[index]
            best = int(scores.argmax())
            if scores[best] >= similarity:
                head_of[name] = names[heads[best]]
                continue
        heads.append(index)
        head_of[name] = name
    return head_of


def main() -> None:
    """Add a column to every bank entry."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--bank", type=Path, required=True)
    parser.add_argument("--similarity", type=float, default=SIMILARITY)
    args = parser.parse_args()
    load_dotenv(find_dotenv(usecwd=True))
    path: Path = args.bank.expanduser()
    bank = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]

    counts = Counter(_topic(entry, "en").lower() for entry in bank if _topic(entry, "en"))
    names = [name for name, _ in counts.most_common()]
    client = OpenAI(api_key=os.environ["OPENAI_API_KEY"])
    embedded: list[list[float]] = []
    for start in range(0, len(names), 1000):
        response = client.embeddings.create(model=EMBEDDING_MODEL, input=names[start : start + 1000])
        embedded += [item.embedding for item in response.data]
    vectors = np.array(embedded, dtype=np.float32)
    vectors /= np.linalg.norm(vectors, axis=1, keepdims=True)
    head_of = columns(names, vectors, args.similarity)

    # A column's Spanish name is the one its own topic was given most often.
    spanish: dict[str, Counter[str]] = {}
    for entry in bank:
        name = _topic(entry, "en").lower()
        if name and head_of.get(name) == name and _topic(entry, "es"):
            spanish.setdefault(name, Counter())[_topic(entry, "es")] += 1
    english = {name: _topic(entry, "en") for entry in bank if (name := _topic(entry, "en").lower())}
    for entry in bank:
        name = _topic(entry, "en").lower()
        head = head_of.get(name)
        if head:
            es = spanish.get(head, Counter()).most_common(1)
            entry["column"] = {"en": english[head], "es": es[0][0] if es else english[head]}
        else:
            entry["column"] = None

    shutil.copyfile(path, path.with_name("bank.before-columns.jsonl"))
    path.write_text("".join(json.dumps(entry, ensure_ascii=False) + "\n" for entry in bank), encoding="utf-8")
    sizes = Counter(entry["column"]["en"].lower() for entry in bank if entry.get("column"))
    print(
        f"{len(names)} topics -> {len(sizes)} columns; {sum(1 for n in sizes.values() if n >= 4)} with at least "
        f"4 clues; biggest: {dict(sizes.most_common(12))}"
    )


if __name__ == "__main__":
    main()
