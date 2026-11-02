"""Make the games' fixed host clips with ElevenLabs (docs/plans/party-games.md, "Host, sounds and music").

Every take in ``games/big_brain_lines.py`` becomes ``<out>/<moment>/<language>-<take>.wav`` (24 kHz mono, what
the game plays) in the chosen host
voice on Eleven v4 Turbo, audio tags included. ``manifest.json`` records each clip's text, so a rerun makes only
the clips whose text is new or changed. With ``--check``, Whisper transcribes every clip and flags any that
read a tag aloud or say something other than the line; flagged clips are listed in ``check.json`` and can be
remade by deleting them and running again. ``--set kids`` makes the kids' games' lines (games/kids_lines.py)
instead, in the voice given with ``--voice``.

Usage: uv run python scripts/big_brain_voice.py --out ~/.local/share/reachy/game-data/big-brain/voice --check
       uv run python scripts/big_brain_voice.py --set kids --voice VOICE_ID \
           --out ~/.local/share/reachy/game-data/kids/voice --check
"""

import os
import re
import json
import wave
import asyncio
import difflib
import argparse
from typing import Any
from pathlib import Path

import httpx
from dotenv import find_dotenv, load_dotenv
from openai import AsyncOpenAI

from reachy_mini_conversation_app.games.kids_lines import TAGS as KIDS_TAGS
from reachy_mini_conversation_app.games.kids_lines import LINES as KIDS_LINES
from reachy_mini_conversation_app.games.big_brain_lines import TAGS, LINES, shown


VOICE_ID = "PluKrefK4JWrlyoTgZkd"  # "Richie Big Brain host", designed 2026-11-01
MODEL = "eleven_v4_turbo"
# The Creator plan allows five requests at a time.
CONCURRENCY = 5
# Below this similarity between the line and what Whisper heard, a clip is flagged.
MIN_SIMILARITY = 0.6


def _words(text: str) -> list[str]:
    return re.sub(r"[^\w\s]", " ", text.lower()).split()


async def make(
    client: httpx.AsyncClient, gate: asyncio.Semaphore, text: str, language: str, path: Path, voice: str = VOICE_ID
) -> None:
    """Say ``text`` in ``voice`` and write it to ``path``, retrying while ElevenLabs is busy."""
    async with gate:
        for attempt in range(8):
            response = await client.post(
                f"https://api.elevenlabs.io/v1/text-to-speech/{voice}",
                params={"output_format": "pcm_24000"},
                json={"text": text, "model_id": MODEL, "language_code": language},
            )
            if response.status_code != 429:
                break
            await asyncio.sleep(5 * (attempt + 1))
        response.raise_for_status()
        path.parent.mkdir(parents=True, exist_ok=True)
        with wave.open(str(path), "wb") as audio:
            audio.setnchannels(1)
            audio.setsampwidth(2)
            audio.setframerate(24000)
            audio.writeframes(response.content)


async def check(
    openai: AsyncOpenAI, gate: asyncio.Semaphore, text: str, language: str, path: Path, tags: frozenset[str] = TAGS
) -> dict[str, Any]:
    """Transcribe a clip and compare it with its line."""
    async with gate:
        with path.open("rb") as audio:
            heard = (await openai.audio.transcriptions.create(model="whisper-1", file=audio, language=language)).text
    # A tag counts as read aloud only when its word is not part of the line itself ("We'll pause here").
    said_tags = [
        tag for tag in tags if re.search(rf"\b{re.escape(tag)}\b", heard.lower()) and tag not in shown(text).lower()
    ]
    similarity = difflib.SequenceMatcher(None, _words(shown(text)), _words(heard)).ratio()
    return {"heard": heard, "tags_read_aloud": said_tags, "similarity": round(similarity, 2)}


async def main() -> None:
    """Make every missing or changed clip, then optionally check them all."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--check", action="store_true", help="transcribe every clip and flag bad ones")
    parser.add_argument("--set", default="big-brain", choices=("big-brain", "kids"), help="which game's lines")
    parser.add_argument("--voice", default=VOICE_ID, help="the ElevenLabs voice id (Big Brain's host by default)")
    args = parser.parse_args()
    lines, tags = (KIDS_LINES, KIDS_TAGS) if args.set == "kids" else (LINES, TAGS)
    load_dotenv(find_dotenv(usecwd=True))
    out: Path = args.out.expanduser()
    out.mkdir(parents=True, exist_ok=True)
    manifest_path = out / "manifest.json"
    manifest: dict[str, dict[str, list[dict[str, str]]]] = (
        json.loads(manifest_path.read_text(encoding="utf-8")) if manifest_path.exists() else {}
    )

    wanted: dict[str, dict[str, list[dict[str, str]]]] = {}
    todo: list[tuple[str, str, Path]] = []
    for moment, takes in lines.items():
        for language, lines in takes.items():
            entries = []
            for take, text in enumerate(lines, start=1):
                path = out / moment / f"{language}-{take:02d}.wav"
                entries.append({"file": str(path.relative_to(out)), "text": text})
                old = manifest.get(moment, {}).get(language, [])
                unchanged = take <= len(old) and old[take - 1]["text"] == text and path.exists()
                if not unchanged:
                    todo.append((text, language, path))
            wanted.setdefault(moment, {})[language] = entries

    headers = {"xi-api-key": os.environ["ELEVENLABS_API_KEY"]}
    gate = asyncio.Semaphore(CONCURRENCY)
    async with httpx.AsyncClient(headers=headers, timeout=120) as client:
        results = await asyncio.gather(
            *(make(client, gate, text, language, path, args.voice) for text, language, path in todo),
            return_exceptions=True,
        )
    failed = [(path, error) for (_, _, path), error in zip(todo, results) if isinstance(error, BaseException)]
    for path, error in failed:
        print(f"failed: {path.relative_to(out)}: {type(error).__name__}: {str(error)[:150]}")
    manifest_path.write_text(json.dumps(wanted, indent=1, ensure_ascii=False), encoding="utf-8")
    characters = sum(len(text) for text, _, _ in todo)
    print(f"made {len(todo) - len(failed)} clips ({characters:,} characters), {len(failed)} failed")

    if args.check:
        openai = AsyncOpenAI(api_key=os.environ["OPENAI_API_KEY"])
        clips = [
            (moment, language, entry)
            for moment, takes in wanted.items()
            for language, entries in takes.items()
            for entry in entries
            if (out / entry["file"]).exists()
        ]
        check_gate = asyncio.Semaphore(8)
        checks = await asyncio.gather(
            *(check(openai, check_gate, e["text"], lang, out / e["file"], tags) for _, lang, e in clips)
        )
        flagged = [
            {"file": entry["file"], "text": entry["text"], **result}
            for (_, _, entry), result in zip(clips, checks)
            if result["tags_read_aloud"] or result["similarity"] < MIN_SIMILARITY
        ]
        (out / "check.json").write_text(json.dumps(flagged, indent=1, ensure_ascii=False), encoding="utf-8")
        print(f"checked {len(clips)} clips; {len(flagged)} flagged (check.json)")


if __name__ == "__main__":
    asyncio.run(main())
