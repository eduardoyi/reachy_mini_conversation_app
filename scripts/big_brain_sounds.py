"""Make Big Brain's sounds and music with ElevenLabs (docs/plans/party-games.md, "Big Brain build").

Each sound is several takes of an ElevenLabs sound effect (200 credits each) and each piece of music several
takes of Eleven Music (900 credits a minute), all our own: the prompts ask for original quiz-show moments and
never for a show's theme. Every take is kept as ``<out>/<name>/take-N.mp3`` and as the 24 kHz mono WAV the
game plays (converted with ffmpeg); ``chosen.json`` names the take the game uses, the first until Eduardo picks
another. A rerun makes only missing takes.

Usage: uv run python scripts/big_brain_sounds.py --out ~/.local/share/reachy/game-data/big-brain/sounds
"""

import os
import json
import asyncio
import argparse
import subprocess
from pathlib import Path

import httpx
from dotenv import find_dotenv, load_dotenv


TAKES = 3
CONCURRENCY = 4  # the Creator plan allows five requests at once
NOT_A_COPY = "Original, not imitating any TV show or existing theme."

# Name: (seconds, prompt).
SOUNDS: dict[str, tuple[float, str]] = {
    "board": (2.5, "Bright magical whoosh with a sparkling shimmer as a game board appears, quiz show style."),
    "pick": (0.8, "Short satisfying digital click with a soft pop, selecting a square on a game board."),
    "buzz_red": (0.8, "Short punchy low arcade buzzer, warm and round, game show contestant buzz-in."),
    "buzz_blue": (0.8, "Short bright electronic two-tone chirp buzzer, game show contestant buzz-in."),
    "buzz_green": (0.8, "Short bouncy synth boing buzzer, playful, game show contestant buzz-in."),
    "buzz_yellow": (0.8, "Short high sparkly bell-like buzzer ping, game show contestant buzz-in."),
    "correct": (1.5, "Happy rising three-note chime with sparkle, correct answer on a quiz show."),
    "wrong": (1.2, "Comic descending low brass wah-wah buzz, wrong answer on a quiz show, playful not harsh."),
    "time_up": (1.5, "Short descending electronic beeps ending in a soft low tone, time is up on a quiz show."),
    "hidden": (2.5, "Dramatic orchestral hit with rising shimmer, a secret bonus revealed on a quiz show."),
    "steal": (2.0, "Sneaky tiptoe pizzicato strings sting ending with a cheeky pop, stealing points."),
    "drumroll": (4.0, "Snare drum roll building for four seconds, ending with a crash cymbal."),
    "applause": (4.0, "Small enthusiastic studio audience applause and cheering, warm, four seconds."),
    "fanfare": (6.0, "Triumphant short brass fanfare with timpani for the winner of a quiz show."),
}
# Name: (seconds, prompt).
MUSIC: dict[str, tuple[float, str]] = {
    "intro": (
        20.0,
        "Upbeat retro television quiz show opening theme, big band brass and funky bass, bright and exciting, "
        "ends with a clear final hit. Instrumental.",
    ),
    "thinking": (
        30.0,
        "Light playful thinking music for a quiz final, gentle ticking clock, pizzicato and soft jazz piano, steady "
        "tempo, a little suspense, loops smoothly. Instrumental.",
    ),
    "final": (
        12.0,
        "Tense dramatic build for the final round of a quiz show, pulsing strings and low drums rising to a hit. "
        "Instrumental.",
    ),
    # The Hogwarts edition's opening (games/big_brain.py, Edition): magical rather than brassy.
    "intro_hp": (
        20.0,
        "Whimsical magical orchestral opening theme for a fantasy quiz show: twinkling celesta, soaring strings and "
        "French horns, mysterious at first then grand, ends with a clear final hit. Instrumental.",
    ),
    "outro": (
        20.0,
        "Cheerful closing theme for a quiz show, brassy and warm, celebratory, ends with a final flourish. "
        "Instrumental.",
    ),
}


async def sound(client: httpx.AsyncClient, seconds: float, prompt: str) -> bytes:
    """Return one sound effect take as mp3."""
    response = await client.post(
        "https://api.elevenlabs.io/v1/sound-generation",
        params={"output_format": "mp3_44100_128"},
        json={"text": f"{prompt} {NOT_A_COPY}", "duration_seconds": seconds, "prompt_influence": 0.5},
    )
    response.raise_for_status()
    return response.content


async def music(client: httpx.AsyncClient, seconds: float, prompt: str) -> bytes:
    """Return one music take as mp3."""
    response = await client.post(
        "https://api.elevenlabs.io/v1/music",
        params={"output_format": "mp3_44100_128"},
        json={"prompt": f"{prompt} {NOT_A_COPY}", "music_length_ms": int(seconds * 1000)},
    )
    response.raise_for_status()
    return response.content


def to_wav(mp3: Path) -> None:
    """Write the 24 kHz mono WAV the game plays next to ``mp3``."""
    subprocess.run(
        ["ffmpeg", "-v", "error", "-y", "-i", str(mp3), "-ar", "24000", "-ac", "1", str(mp3.with_suffix(".wav"))],
        check=True,
    )


async def main() -> None:
    """Make every missing take, convert them, and write chosen.json."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--only", default="", help="only these sounds, comma-separated (e.g. intro_hp)")
    args = parser.parse_args()
    load_dotenv(find_dotenv(usecwd=True))
    out: Path = args.out.expanduser()
    gate = asyncio.Semaphore(CONCURRENCY)
    jobs = [(name, "sound", seconds, prompt) for name, (seconds, prompt) in SOUNDS.items()]
    jobs += [(name, "music", seconds, prompt) for name, (seconds, prompt) in MUSIC.items()]
    if args.only:
        jobs = [job for job in jobs if job[0] in args.only.split(",")]

    async with httpx.AsyncClient(headers={"xi-api-key": os.environ["ELEVENLABS_API_KEY"]}, timeout=300) as client:

        async def take(name: str, kind: str, seconds: float, prompt: str, number: int) -> str | None:
            path = out / name / f"take-{number}.mp3"
            if path.exists():
                return None
            async with gate:
                for attempt in range(6):
                    try:
                        data = await (sound if kind == "sound" else music)(client, seconds, prompt)
                        break
                    except httpx.HTTPStatusError as error:
                        if error.response.status_code != 429 or attempt == 5:
                            return f"{name} take {number}: {error.response.status_code} {error.response.text[:200]}"
                        await asyncio.sleep(10 * (attempt + 1))
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
            to_wav(path)
            return None

        errors = await asyncio.gather(
            *(take(*job, number) for job in jobs for number in range(1, TAKES + 1)), return_exceptions=True
        )
    for error in errors:
        if error:
            print("failed:", error)
    chosen_path = out / "chosen.json"
    chosen = json.loads(chosen_path.read_text()) if chosen_path.exists() else {}
    for name, *_ in jobs:
        takes = sorted((out / name).glob("take-*.wav")) if (out / name).exists() else []
        if takes and (name not in chosen or not (out / name / chosen[name]).exists()):
            chosen[name] = takes[0].name
    chosen_path.write_text(json.dumps(chosen, indent=1))
    print(f"{len(chosen)} of {len(jobs)} sounds have a take; chosen.json written")


if __name__ == "__main__":
    asyncio.run(main())
