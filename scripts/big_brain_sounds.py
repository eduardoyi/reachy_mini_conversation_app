"""Make the games' sounds and music with ElevenLabs (docs/plans/party-games.md, "Big Brain build").

Each sound is several takes of an ElevenLabs sound effect (200 credits each) and each piece of music several
takes of Eleven Music (900 credits a minute), all our own: the prompts ask for original quiz-show moments and
never for a show's theme. Every take is kept as ``<out>/<name>/take-N.mp3`` and as the 24 kHz mono WAV the
game plays (converted with ffmpeg); ``chosen.json`` names the take the game uses, the first until Eduardo picks
another. A rerun makes only missing takes. ``--set kids`` makes the kids' games' set instead (the color hunt,
the treasure hunt, Simón dice, la gallina loca and the number race: games/kids_audio.py), two takes each.

Usage: uv run python scripts/big_brain_sounds.py --out ~/.local/share/reachy/game-data/big-brain/sounds
       uv run python scripts/big_brain_sounds.py --set kids --out ~/.local/share/reachy/game-data/kids/sounds
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
NOT_A_COPY = "Original, not imitating any TV show, song or existing theme."

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
    "intro_lotr": (
        20.0,
        "Epic orchestral fantasy opening theme for a quiz show: a lone tin whistle melody over soft strings, then "
        "full choir, horns and drums rising to a grand heroic finish with a clear final hit. Instrumental.",
    ),
    "intro_sw": (
        20.0,
        "Heroic space-opera orchestral opening theme for a quiz show: bold brass fanfare and timpani over soaring "
        "strings, adventurous and grand, ends with a clear final hit. Instrumental.",
    ),
    "intro_dis": (
        20.0,
        "Whimsical fairy-tale orchestral opening theme for a family quiz show: sparkling celesta and harp, sweeping "
        "strings and a hopeful melody building to a magical, joyful finish with a clear final hit. Instrumental.",
    ),
    "outro": (
        20.0,
        "Cheerful closing theme for a quiz show, brassy and warm, celebratory, ends with a final flourish. "
        "Instrumental.",
    ),
}

# The kids' games: short, soft and bright, for children of 2 to 6 with adults in the room. Music is a quiet bed.
KIDS_SOUNDS: dict[str, tuple[float, str]] = {
    "found": (1.8, "Magical sparkly chime cascade ending in a happy little xylophone ta-da, a small child found the "
              "right thing in a toddler game. Soft and bright, not loud."),
    "star": (1.0, "Single bright twinkling star ding with a gentle shimmer, earning a star in a children's game."),
    "boing": (0.8, "Soft comic cartoon boing with a tiny wobble, a gentle not-quite sound in a toddler game, "
              "friendly, never harsh."),
    "time_up": (1.5, "Gentle descending slide whistle into a soft marimba plonk, time is up in a toddler game, cute "
                "not sad."),
    "round": (1.0, "Quick playful whoosh with a bubbly pop and a tiny bell, a new round starts in a children's game."),
    "cheer": (2.0, "A small group of happy young children cheering yay and clapping, short and joyful, not "
              "screaming."),
    "snap": (0.9, "Playful camera shutter click with a sparkly twinkle, a photo of a child's funny pose."),
    "tiptoe": (1.2, "Three soft cartoon tiptoe boops stepping backwards, playful."),
    # La gallina loca
    "cluck": (1.2, "A funny cartoon hen clucking three quick times, bok bok bok, playful, for a toddler game."),
    "squawk": (2.5, "A silly cartoon hen squawking in a frantic flappy panic with a rooster crow at the end, funny "
               "not scary, for a toddler game."),
    "egg_pop": (1.0, "A cute cartoon pop of a hen laying an egg, a soft plop and a tiny sparkle, for a toddler game."),
    # The number race
    "spin": (1.5, "A cartoon prize wheel spinning with quick plastic clicks that slow down and stop, playful, for a "
             "toddler board game."),
    "putt": (1.2, "A cute little old cartoon truck puttering forward a few hops, putt putt putt with a tiny honk, "
             "playful, for a toddler game."),
    "zoom": (1.2, "A cartoon sports car zooming past with a quick vroom and a whoosh, playful not loud, for a "
             "toddler game."),
}  # fmt: skip
KIDS_MUSIC: dict[str, tuple[float, str]] = {
    "intro_colors": (10.0, "Short bright playful jingle for a toddler color game: ukulele, glockenspiel and "
                     "handclaps, a happy rainbow feeling, ends with a cute final note. Instrumental."),
    "intro_treasure": (10.0, "Short adventurous playful jingle for little kids hunting treasure around the house: "
                       "pizzicato strings, marimba and a tiny swashbuckling flourish, ends with a cute final note. "
                       "Instrumental."),
    "intro_simon": (10.0, "Short bouncy funky jingle for a kids' copy-the-pose game: bass, claps and a silly "
                    "kazoo-like synth, ends with a cute final note. Instrumental."),
    "hunt": (30.0, "Light playful searching music for a toddler hunt game, about 110 bpm: pizzicato strings, "
             "marimba, soft glockenspiel and brushed percussion, curious and a little sneaky, steady, no big "
             "changes, loops smoothly, background level. Instrumental."),
    "hunt_hurry": (20.0, "Faster, more exciting playful searching music for young kids, about 150 bpm: ticking "
                   "woodblock, pizzicato and marimba, a hurry-up feeling that is fun, not stressful, steady, loops "
                   "smoothly, background level. Instrumental."),
    "pose": (20.0, "Bouncy freeze-dance music for young kids holding a pose, about 120 bpm: ukulele, claps and a "
             "light synth bass, cheerful and silly, steady, loops smoothly, background level. Instrumental."),
    "intro_gallina": (10.0, "Short silly barnyard jingle for a kids' hot-potato game with a crazy hen: bouncy "
                      "banjo, clucking woodblocks and a cartoon slide whistle, ends with a cute final note. "
                      "Instrumental."),
    "gallina": (30.0, "Bouncy silly barnyard music for a kids' hot-potato game, about 120 bpm: banjo, pizzicato, "
                "clucking woodblocks and claps, playful and a little sneaky, steady, loops smoothly, background "
                "level. Original, not the chicken dance. Instrumental."),
    "gallina_hurry": (20.0, "Faster, more frantic silly barnyard music for a kids' hot-potato game, about 160 bpm: "
                      "banjo, fast woodblocks and a ticking clock, a fun hurry-up feeling, not stressful, steady, "
                      "loops smoothly, background level. Instrumental."),
    "intro_race": (10.0, "Short bright racing jingle for a kids' counting race with two cartoon cars: bouncy "
                   "ukulele, a toy horn and a revving motor rhythm, ends with a cute final note. Instrumental."),
    "celebrate": (10.0, "Short joyful celebration for young kids finishing a game: glockenspiel and ukulele "
                  "fanfare with handclaps and a little ta-da ending. Instrumental."),
}  # fmt: skip
SETS: dict[str, tuple[dict[str, tuple[float, str]], dict[str, tuple[float, str]], int]] = {
    "big-brain": (SOUNDS, MUSIC, TAKES),
    "kids": (KIDS_SOUNDS, KIDS_MUSIC, 2),
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
    parser.add_argument("--set", default="big-brain", choices=SETS, help="which game's sounds")
    args = parser.parse_args()
    load_dotenv(find_dotenv(usecwd=True))
    out: Path = args.out.expanduser()
    gate = asyncio.Semaphore(CONCURRENCY)
    sounds, musics, takes = SETS[args.set]
    jobs = [(name, "sound", seconds, prompt) for name, (seconds, prompt) in sounds.items()]
    jobs += [(name, "music", seconds, prompt) for name, (seconds, prompt) in musics.items()]
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
            *(take(*job, number) for job in jobs for number in range(1, takes + 1)), return_exceptions=True
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
