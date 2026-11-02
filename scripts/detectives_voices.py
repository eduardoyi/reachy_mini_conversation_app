"""Design an ElevenLabs voice for each Detectives character that has a description but no voice yet.

For every character in ``games/detectives_cases/*.json`` whose ``"voice"`` has a ``description`` and no ``id``,
ElevenLabs' voice design (``eleven_ttv_v3``) makes previews from the description reading the character's Spanish
lines, the first preview is saved to the account as "Detectives · <case> · <name>", and its id is written into
the case file. Every preview is kept as ``<out>/<case>/<character>-<n>.mp3`` to listen to; to use another take,
delete the voice in ElevenLabs, remove its ``id`` and run again. Each voice takes one of the account's slots.

Usage: uv run python scripts/detectives_voices.py --out ~/.local/share/reachy/game-data/detectives/voice-previews
"""

import os
import json
import base64
import argparse
from pathlib import Path

import httpx
from dotenv import find_dotenv, load_dotenv

from reachy_mini_conversation_app.games.detectives_case import CASES_DIR, Case, Character, parse_case


DESIGN_MODEL = "eleven_ttv_v3"
API = "https://api.elevenlabs.io/v1"


def sample_text(character: Character) -> str:
    """Return 100 to 1000 characters of the character's own Spanish lines for the previews to read."""
    parts = [character.fallback["es"], *(fact["es"] for fact in character.knows), character.alibi["es"]]
    text = ""
    for part in parts:
        if len(text) >= 100:
            break
        text = f"{text} {part}".strip()
    return text[:1000]


def design(client: httpx.Client, case: Case, character: Character, out: Path) -> str:
    """Design ``character``'s voice, keep its previews in ``out`` and return the saved voice's id."""
    assert character.voice is not None
    response = client.post(
        f"{API}/text-to-voice/design",
        json={
            "voice_description": character.voice.description,
            "model_id": DESIGN_MODEL,
            "text": sample_text(character),
        },  # fmt: skip
        timeout=120,
    )
    response.raise_for_status()
    previews = response.json()["previews"]
    folder = out / case.id
    folder.mkdir(parents=True, exist_ok=True)
    for number, preview in enumerate(previews, 1):
        (folder / f"{character.id}-{number}.mp3").write_bytes(base64.b64decode(preview["audio_base_64"]))
    saved = client.post(
        f"{API}/text-to-voice",
        json={
            "voice_name": f"Detectives · {case.title['en']} · {character.name['en']}",
            "voice_description": character.voice.description,
            "generated_voice_id": previews[0]["generated_voice_id"],
        },  # fmt: skip
        timeout=60,
    )
    saved.raise_for_status()
    return str(saved.json()["voice_id"])


def write_id(path: Path, description: str, voice_id: str) -> None:
    """Add ``voice_id`` to the voice line that holds ``description`` in the case file, keeping its layout."""
    text = path.read_text(encoding="utf-8")
    old = json.dumps({"description": description}, ensure_ascii=False)
    new = json.dumps({"description": description, "id": voice_id}, ensure_ascii=False)
    if text.count(old) != 1:
        raise SystemExit(f"{path.name}: cannot find one voice line for {description[:40]!r}; add the id by hand")
    path.write_text(text.replace(old, new), encoding="utf-8")


def main() -> None:
    """Design the missing voices."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", type=Path, required=True, help="where to keep the previews")
    parser.add_argument("--cases", type=Path, default=CASES_DIR, help="the case files to read and update")
    args = parser.parse_args()
    load_dotenv(find_dotenv(usecwd=True))
    key = os.getenv("ELEVENLABS_API_KEY", "").strip()
    if not key:
        raise SystemExit("ELEVENLABS_API_KEY is not set")
    with httpx.Client(headers={"xi-api-key": key}) as client:
        for path in sorted(args.cases.glob("*.json")):
            case = parse_case(json.loads(path.read_text(encoding="utf-8")))
            for character in case.characters:
                if character.voice is None or character.voice.id:
                    continue
                voice_id = design(client, case, character, args.out.expanduser())
                write_id(path, character.voice.description, voice_id)
                print(f"{case.id}/{character.id}: {voice_id}")


if __name__ == "__main__":
    main()
