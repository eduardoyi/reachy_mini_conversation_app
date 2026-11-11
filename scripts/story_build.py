"""Make story mode's audio ahead of time with ElevenLabs (docs/plans/story-mode.md, "Build").

Every story line, every choice's branches and the shared lines are recorded once, in each role's voice from
``stories/cast.yaml``, as ``<out>/<story>/voice/<clip id>.wav`` (24 kHz mono, what Reachy plays). A clip is named by
its voice, language and tagged text, so a rerun records only lines that are new or changed. Sounds and music from
``stories/sounds.yaml`` go to ``<out>/sounds/<name>/take-1.wav`` with ``chosen.json`` (made from mp3 with ffmpeg, so
run this on the laptop, then copy ``<out>`` to the Mac mini's game-data/stories).

Usage:
  uv run python scripts/story_build.py --design narrator            # 3 voice previews per role to listen to
  uv run python scripts/story_build.py --save narrator 2            # keep preview 2 as the role's voice
  uv run python scripts/story_build.py tres_cerditos --cost         # what recording would cost, nothing made
  uv run python scripts/story_build.py tres_cerditos                # record what is missing, make the sounds
  uv run python scripts/story_build.py tres_cerditos --play es      # one wav of the whole story to listen to
  uv run python scripts/story_build.py --sheets                     # character sheets (stories/cast/) to approve
  uv run python scripts/story_build.py tres_cerditos --pictures     # the scenes, drawn from the sheets

Pictures (story_pictures.py): each character's sheet is drawn once from its look in cast.yaml and kept in the repo
(``stories/cast/<role>.png``); every scene is an edit with the sheets of the characters in it as references. Drawings
go to ``<out>/<story>/pictures/``, the screens Reachy shows to ``<out>/<story>/screens/``, and ``contact.png`` shows
them all as the TRMNL will. A picture is drawn again only when its prompt or a sheet changes (``--redo ID`` forces it).
"""

import io
import os
import re
import json
import base64
import asyncio
import hashlib
import argparse
import subprocess
from pathlib import Path

import yaml
import httpx
import numpy as np
from PIL import Image, ImageDraw
from dotenv import find_dotenv, load_dotenv
from openai import AsyncOpenAI

from reachy_mini_conversation_app.games.story import StoryVoice
from reachy_mini_conversation_app.games.kids_audio import MUSIC_DB, SOUND_DB, leveled
from reachy_mini_conversation_app.games.story_file import (
    MODEL,
    LANGUAGES,
    STORIES_DIR,
    Line,
    Story,
    load_cast,
    load_story,
    load_common,
    load_sounds,
)
from reachy_mini_conversation_app.games.story_pictures import (
    STYLES,
    SHEET_SIZE,
    DRAWING_SIZE,
    DEFAULT_STYLE,
    styled,
    screen_png,
    scene_prompt,
    sheet_prompt,
    turnaround_prompt,
)
from reachy_mini_conversation_app.games.big_brain_audio import RATE, join, silence, read_wav, write_wav
from reachy_mini_conversation_app.games.big_brain_lines import shown


API = "https://api.elevenlabs.io/v1"
DESIGN_MODEL = "eleven_ttv_v3"
DEFAULT_OUT = Path("~/.local/share/reachy/game-data/stories")
PREVIEWS = Path("~/.local/share/reachy/story-voice-previews")
# The Creator plan allows five requests at a time.
CONCURRENCY = 4
# Sounds used by every story (story.py plays them at the start and the end).
SHARED_SOUNDS = ("story_intro", "story_end")
# Rough ElevenLabs credits: one per character for v4 Turbo speech; per generated sound or music piece.
SOUND_CREDITS, MUSIC_CREDITS_PER_S = 200, 30
NOT_A_COPY = "Original, not imitating any existing song, show or brand."


def _story(key: str) -> Story:
    return load_story(STORIES_DIR / key / "story.yaml")


def _role_sample(role: str) -> str:
    """Up to ~600 characters the role says in the stories, in Spanish then Italian, for its voice previews."""
    lines = [line for path in sorted(STORIES_DIR.glob("*/story.yaml")) for line in load_story(path).lines()]
    lines += list(load_common().lines.values())
    mine = [line for line in lines if line.who == role] or lines
    text = ""
    for language in LANGUAGES:
        for line in mine:
            if len(text) > 300 * (LANGUAGES.index(language) + 1):
                break
            text += line.tagged(language) + " "
    return text.strip()[:1000]


def design(role: str) -> None:
    """Make three previews of ``role``'s voice from its description, to listen to."""
    cast = load_cast()
    description = cast[role]["description"]
    with httpx.Client(headers={"xi-api-key": os.environ["ELEVENLABS_API_KEY"]}, timeout=180) as client:
        response = client.post(
            f"{API}/text-to-voice/design",
            json={"voice_description": description, "model_id": DESIGN_MODEL, "text": _role_sample(role)},
        )
        response.raise_for_status()
    folder = PREVIEWS.expanduser()
    folder.mkdir(parents=True, exist_ok=True)
    ids = []
    for number, preview in enumerate(response.json()["previews"], 1):
        path = folder / f"{role}-{number}.mp3"
        path.write_bytes(base64.b64decode(preview["audio_base_64"]))
        ids.append(preview["generated_voice_id"])
        print(path)
    (folder / f"{role}.json").write_text(json.dumps({"description": description, "previews": ids}, indent=1))
    print(f"Listen, then: uv run python scripts/story_build.py --save {role} N")


def save(role: str, number: int) -> None:
    """Keep preview ``number`` of ``role`` as an ElevenLabs voice and put its id in cast.yaml."""
    info = json.loads((PREVIEWS.expanduser() / f"{role}.json").read_text())
    with httpx.Client(headers={"xi-api-key": os.environ["ELEVENLABS_API_KEY"]}, timeout=60) as client:
        response = client.post(
            f"{API}/text-to-voice",
            json={
                "voice_name": f"Richie stories · {role}",
                "voice_description": info["description"],
                "generated_voice_id": info["previews"][number - 1],
            },
        )
        response.raise_for_status()
    voice = str(response.json()["voice_id"])
    path = STORIES_DIR / "cast.yaml"
    lines = path.read_text(encoding="utf-8").splitlines(keepends=True)
    inside = False
    for index, text in enumerate(lines):
        if not text.startswith(" ") and text.strip():
            inside = text.split(":")[0] == role
        elif inside and text.strip().startswith("voice:"):
            lines[index] = f'  voice: "{voice}"\n'
            break
    path.write_text("".join(lines), encoding="utf-8")
    yaml.safe_load(path.read_text(encoding="utf-8"))  # still valid
    print(f"{role}: voice {voice} saved in cast.yaml")


def _jobs(story: Story, out: Path) -> list[tuple[Line, str, Path, str]]:
    """Every (line, language, path, voice) of the story and the shared lines whose recording is missing."""
    cast = load_cast()
    voices = [
        (story.lines(), StoryVoice(out / story.key, cast)),
        (load_common().lines.values(), StoryVoice(out / "common", cast)),
    ]  # noqa: E501
    jobs, seen = [], set()
    for lines, voice in voices:
        for line in lines:
            for language in LANGUAGES:
                path = voice.path(line, language)
                if path in seen or path.exists():
                    continue
                seen.add(path)
                jobs.append((line, language, path, cast[line.who]["voice"]))
    return jobs


def _sound_jobs(story: Story, out: Path) -> list[tuple[str, dict[str, object]]]:
    sounds = load_sounds()
    names = sorted(story.sounds() | set(SHARED_SOUNDS))
    return [(name, sounds[name]) for name in names if not (out / "sounds" / name / "take-1.wav").exists()]


def cost(story: Story, out: Path) -> None:
    """Print what recording the missing lines and sounds would take, without calling anything."""
    jobs = _jobs(story, out)
    characters = sum(len(line.tagged(language)) for line, language, _, _ in jobs)
    sounds = _sound_jobs(story, out)
    credits = sum(
        SOUND_CREDITS if info["kind"] == "sound" else MUSIC_CREDITS_PER_S * float(info["seconds"])  # type: ignore[arg-type]
        for _, info in sounds
        if not info.get("source")  # real recordings are downloaded, not made
    )
    print(f"{len(jobs)} clips to record, {characters} characters (about {characters} credits)")
    print(f"{len(sounds)} sounds and music to make (about {credits:.0f} credits)")
    missing = sorted({line.who for line, _, _, voice in jobs if not voice})
    if missing:
        print("No voice yet for:", ", ".join(missing), "(--design ROLE, then --save ROLE N)")


async def _record(
    client: httpx.AsyncClient, gate: asyncio.Semaphore, line: Line, language: str, path: Path, voice: str
) -> str | None:  # noqa: E501
    async with gate:
        for attempt in range(8):
            response = await client.post(
                f"{API}/text-to-speech/{voice}",
                params={"output_format": "pcm_24000"},
                json={"text": line.tagged(language), "model_id": MODEL, "language_code": language},
            )
            if response.status_code != 429:
                break
            await asyncio.sleep(5 * (attempt + 1))
        if response.status_code != 200:
            return f"{line.who} {language} {shown(line.tagged(language))[:40]!r}: {response.status_code} {response.text[:200]}"  # noqa: E501
    write_wav(path, np.frombuffer(response.content, dtype=np.int16))
    return None


async def _sound(
    client: httpx.AsyncClient, gate: asyncio.Semaphore, name: str, info: dict[str, object], out: Path
) -> str | None:  # noqa: E501
    if info.get("source"):
        return await _cut_recording(name, info, out)
    seconds, prompt = float(info["seconds"]), f"{info['prompt']} {NOT_A_COPY}"  # type: ignore[arg-type]
    async with gate:
        for attempt in range(8):  # ElevenLabs is busy, or the plan's two requests at a time are in use
            if info["kind"] == "sound":
                response = await client.post(
                    f"{API}/sound-generation",
                    params={"output_format": "mp3_44100_128"},
                    json={"text": prompt, "duration_seconds": seconds, "prompt_influence": 0.5},
                )
            else:
                response = await client.post(
                    f"{API}/music",
                    params={"output_format": "mp3_44100_128"},
                    json={"prompt": prompt, "music_length_ms": int(seconds * 1000)},
                )
            if response.status_code != 429:
                break
            await asyncio.sleep(10 * (attempt + 1))
    if response.status_code != 200:
        return f"{name}: {response.status_code} {response.text[:200]}"
    mp3 = out / "sounds" / name / "take-1.mp3"
    mp3.parent.mkdir(parents=True, exist_ok=True)
    mp3.write_bytes(response.content)
    subprocess.run(
        ["ffmpeg", "-v", "error", "-y", "-i", str(mp3), "-ar", str(RATE), "-ac", "1", str(mp3.with_suffix(".wav"))],
        check=True,
    )
    return None


async def _cut_recording(name: str, info: dict[str, object], out: Path) -> str | None:
    """Download a real recording (public domain or Creative Commons) and cut ``seconds`` of it from ``start``.

    The cut fades in and out and is evened to ``loudness`` (LUFS) like the generated music.
    """
    seconds, start = float(info["seconds"]), float(info.get("start", 0.0))  # type: ignore[arg-type]
    async with httpx.AsyncClient(
        headers={"User-Agent": "ReachyStoryBuilder/1.0 (private home robot)"}, timeout=300, follow_redirects=True
    ) as client:
        response = await client.get(str(info["source"]))
    if response.status_code != 200:
        return f"{name}: {response.status_code} from {info['source']}"
    folder = out / "sounds" / name
    folder.mkdir(parents=True, exist_ok=True)
    original = folder / f"original{Path(str(info['source'])).suffix or '.audio'}"
    original.write_bytes(response.content)
    fade_out = min(2.0, seconds / 4)
    cut = folder / "cut.wav"
    filters = (
        f"atrim=start={start}:duration={seconds},asetpts=PTS-STARTPTS,afade=t=in:d=0.5,"
        f"afade=t=out:st={seconds - fade_out}:d={fade_out}"
    )
    subprocess.run(
        ["ffmpeg", "-v", "error", "-y", "-i", str(original), "-af", filters, "-ar", str(RATE), "-ac", "1", str(cut)],
        check=True,
    )
    # loudnorm leaves a short cut several dB quiet, so measure it and turn it up (a limiter catches the peaks).
    measured = subprocess.run(
        ["ffmpeg", "-i", str(cut), "-af", "ebur128", "-f", "null", "-"], capture_output=True, text=True, check=True
    ).stderr
    loudness = float(re.findall(r"I:\s+(-?[\d.]+) LUFS", measured)[-1])
    gain = float(info.get("loudness", -17.0)) - loudness  # type: ignore[arg-type]
    subprocess.run(
        ["ffmpeg", "-v", "error", "-y", "-i", str(cut), "-af", f"volume={gain:.1f}dB,alimiter=limit=0.89",
         str(folder / "take-1.wav")],
        check=True,
    )  # fmt: skip
    cut.unlink()
    original.unlink()
    return None


async def build(story: Story, out: Path) -> None:
    """Record the missing lines and make the missing sounds; write chosen.json."""
    jobs = _jobs(story, out)
    missing = sorted({line.who for line, _, _, voice in jobs if not voice})
    if missing:
        raise SystemExit(f"No voice yet for {', '.join(missing)}: --design ROLE, then --save ROLE N")
    gate = asyncio.Semaphore(CONCURRENCY)
    async with httpx.AsyncClient(headers={"xi-api-key": os.environ["ELEVENLABS_API_KEY"]}, timeout=300) as client:
        errors = await asyncio.gather(
            *(_record(client, gate, *job) for job in jobs),
            *(_sound(client, gate, name, info, out) for name, info in _sound_jobs(story, out)),
        )
    for error in errors:
        if error:
            print("failed:", error)
    chosen_path = out / "sounds" / "chosen.json"
    chosen = json.loads(chosen_path.read_text()) if chosen_path.exists() else {}
    for folder in sorted((out / "sounds").glob("*/")) if (out / "sounds").exists() else []:
        if (folder / "take-1.wav").exists():
            chosen.setdefault(folder.name, "take-1.wav")
    chosen_path.parent.mkdir(parents=True, exist_ok=True)
    chosen_path.write_text(json.dumps(chosen, indent=1))
    print(
        f"{len(jobs) - sum(bool(error) for error in errors[: len(jobs)])} clips recorded; {len(chosen)} sounds ready"
    )


def play(story: Story, out: Path, language: str) -> None:
    """Write one wav of the whole story as it plays when nobody joins in (and the first option of each choice)."""
    cast = load_cast()
    voice, common_voice = StoryVoice(out / story.key, cast), StoryVoice(out / "common", cast)
    sounds = load_sounds()

    def sound(name: str) -> np.ndarray:
        path = out / "sounds" / name / "take-1.wav"
        if not name or not path.exists():
            return silence(0.1)
        db = MUSIC_DB if sounds.get(name, {}).get("kind") == "music" else SOUND_DB
        return leveled(read_wav(path), db)

    def said(line: Line, common: bool = False) -> np.ndarray:
        clip = (common_voice if common else voice).clip(line, language)
        if clip is None:
            print("missing:", shown(line.tagged(language)))
            return silence(1.0)
        return clip

    pieces, chosen = [sound("story_intro")], {}
    for step in story.steps:
        if step.kind == "say":
            pieces.append(said(step.line or step.variants[chosen[step.choice]]))
        elif step.kind in ("sound", "music"):
            pieces.append(sound(step.name)[: int(RATE * (step.seconds or 30))])
        elif step.kind == "noise":
            pieces += [
                said(step.lines["ask"]),
                silence(3),
                said(step.lines["louder"]),
                silence(3),
                said(step.lines["without"]),
                sound(step.sound),
            ]  # noqa: E501
        elif step.kind in ("chorus",):
            pieces += [said(step.lines["ask"]), silence(3), said(step.lines["without"])]
        elif step.kind == "pat":
            pieces += [said(step.lines["ask"]), silence(4), sound(step.sound), said(step.lines["without"])]
        elif step.kind == "copy":
            pieces += [
                said(step.lines["ask"]),
                sound(step.music)[: int(RATE * (step.seconds or 5))],
                said(step.lines["done"]),
            ]  # noqa: E501
        elif step.kind == "choice":
            option = step.options[0]
            chosen[step.choice] = option.key
            pieces += [said(step.lines["ask"]), silence(4), said(option.picked), said(option.then)]
    common = load_common().lines
    pieces += [said(common["again"], True), silence(4), said(common["goodbye"], True), sound("story_end")]
    path = out / story.key / f"preview-{language}.wav"
    write_wav(path, join(*pieces, gap_s=0.35))
    print(path, f"{sum(len(piece) for piece in pieces) / RATE / 60:.1f} min")


IMAGE_MODELS = ("gpt-image-2.6-flare", "gpt-image-2.5-flare")  # the newest first; the next if it is not available


async def _image(ai: AsyncOpenAI, prompt: str, size: str, references: list[Path], quality: str) -> tuple[bytes, str]:
    """Return a drawing (PNG) and the model that made it: from ``references`` (an edit) or from the prompt alone."""
    last: Exception | None = None
    for model in IMAGE_MODELS:
        try:
            if references:
                files = [(path.name, path.read_bytes(), "image/png") for path in references]
                response = await ai.images.edit(model=model, image=files, prompt=prompt, size=size, quality=quality)  # type: ignore[arg-type]  # noqa: E501
            else:
                response = await ai.images.generate(model=model, prompt=prompt, size=size, quality=quality)  # type: ignore[arg-type]  # noqa: E501
        except Exception as error:  # an unknown model: try the next
            if "model" in str(error).lower() and (
                "not found" in str(error).lower()
                or "does not exist" in str(error).lower()
                or "invalid" in str(error).lower()
            ):  # noqa: E501
                last = error
                continue
            raise
        encoded = response.data[0].b64_json if response.data else None
        if not encoded:
            raise RuntimeError("no image came back")
        return base64.b64decode(encoded), model
    raise RuntimeError(f"no image model available: {last}")


def _png(data: bytes) -> bytes:
    with Image.open(io.BytesIO(data)) as image:
        out = io.BytesIO()
        image.convert("RGB").save(out, format="PNG")
        return out.getvalue()


def _contact(images: list[tuple[str, Image.Image]], path: Path, columns: int = 3) -> None:
    """Write one image of ``images`` in a grid with their names under them, to look over at once."""
    cell_w, cell_h = 400, 240 + 28
    rows = (len(images) + columns - 1) // columns
    sheet = Image.new("L", (columns * cell_w + (columns + 1) * 8, rows * cell_h + (rows + 1) * 8), 255)
    draw = ImageDraw.Draw(sheet)
    for index, (name, image) in enumerate(images):
        x, y = 8 + (index % columns) * (cell_w + 8), 8 + (index // columns) * (cell_h + 8)
        thumb = image.convert("L").copy()
        thumb.thumbnail((cell_w, 240))
        sheet.paste(thumb, (x + (cell_w - thumb.width) // 2, y))
        draw.text((x + 4, y + 244), name, fill=0)
    path.parent.mkdir(parents=True, exist_ok=True)
    sheet.save(path)
    print(path)


async def sheets(roles: list[str], redo: set[str], style: str = DEFAULT_STYLE, turnaround: bool = False) -> None:
    """Draw the missing character sheets (``stories/cast/<role>.png``) and a contact sheet of them all.

    ``turnaround``: draw each role's turnaround (``stories/cast-turnaround/``) from its single sheet instead.
    """
    cast = load_cast()
    single = STORIES_DIR / styled("cast", style)
    folder = STORIES_DIR / styled("cast-turnaround" if turnaround else "cast", style)
    drawn = [role for role, info in cast.items() if info["look"]]
    wanted = [role for role in (roles or drawn) if role in drawn]
    ai = AsyncOpenAI()

    async def one(role: str) -> None:
        path = folder / f"{role}.png"
        if path.exists() and role not in redo:
            return
        if turnaround:
            if not (single / f"{role}.png").exists():
                print(f"{role}: no single sheet yet, draw it first")
                return
            data, model = await _image(
                ai, turnaround_prompt(role, cast[role]["look"], style), DRAWING_SIZE, [single / f"{role}.png"], "high"
            )
        else:
            data, model = await _image(ai, sheet_prompt(role, cast[role]["look"], style), SHEET_SIZE, [], "high")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(_png(data))
        print(f"{role}: drawn with {model}")

    await asyncio.gather(*(one(role) for role in wanted))
    _contact(
        [(role, Image.open(folder / f"{role}.png")) for role in drawn if (folder / f"{role}.png").exists()],
        DEFAULT_OUT.expanduser() / f"{styled('cast-turnaround-contact' if turnaround else 'cast-contact', style)}.png",
        columns=3,
    )


def _picture_key(prompt: str, references: list[Path]) -> str:
    digest = hashlib.sha256(prompt.encode())
    for path in references:
        digest.update(hashlib.sha256(path.read_bytes()).digest())
    return digest.hexdigest()[:16]


async def pictures(
    story: Story, out: Path, redo: set[str], style: str = DEFAULT_STYLE, turnaround: bool = False
) -> None:
    """Draw the story's missing scenes from the character sheets, make their screens and a contact sheet.

    ``turnaround``: draw from the turnaround sheets with free poses, into ``pictures-turnaround/`` and
    ``screens-turnaround/`` beside the ones in use, to compare (the story plays ``screens/``). A story whose file
    says ``turnaround: true`` is always drawn that way, into the folders it plays.
    """
    looks = {role: info["look"] for role, info in load_cast().items()}
    folder = out / story.key
    # A story with ``turnaround: true`` keeps its turnaround pictures in the folders it plays.
    variant = "-turnaround" if turnaround and not story.turnaround else ""
    turnaround = turnaround or story.turnaround
    sheets_dir = STORIES_DIR / styled(f"cast{variant}", style)
    drawings, screens = styled(f"pictures{variant}", style), styled(f"screens{variant}", style)
    manifest_path = folder / drawings / "manifest.json"
    manifest = json.loads(manifest_path.read_text()) if manifest_path.exists() else {}
    missing_sheets = sorted(
        {role for picture in story.pictures() for role in picture.cast if not (sheets_dir / f"{role}.png").exists()}
    )  # noqa: E501
    if missing_sheets:
        raise SystemExit(f"No character sheet yet for {', '.join(missing_sheets)}: --sheets first")
    ai = AsyncOpenAI()
    gate = asyncio.Semaphore(4)

    async def one(picture: object) -> str | None:
        from reachy_mini_conversation_app.games.story_file import Picture

        assert isinstance(picture, Picture)
        references = [sheets_dir / f"{role}.png" for role in picture.cast]
        prompt = scene_prompt(picture.prompt, picture.cast, looks, style, turnaround=turnaround)
        key = _picture_key(prompt, references)
        drawing = folder / drawings / f"{picture.id}.png"
        if drawing.exists() and manifest.get(picture.id) == key and picture.id not in redo:
            return None
        async with gate:
            try:
                data, model = await _image(ai, prompt, DRAWING_SIZE, references, "high")
            except Exception as error:
                return f"{picture.id}: {type(error).__name__}: {str(error)[:200]}"
        drawing.parent.mkdir(parents=True, exist_ok=True)
        drawing.write_bytes(_png(data))
        manifest[picture.id] = key
        print(f"{picture.id}: drawn with {model}")
        return None

    errors = await asyncio.gather(*(one(picture) for picture in story.pictures()))
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(json.dumps(manifest, indent=1))
    for error in errors:
        if error:
            print("failed:", error)
    shown_screens = []
    for picture in story.pictures():
        drawing = folder / drawings / f"{picture.id}.png"
        if not drawing.exists():
            continue
        screen = screen_png(drawing.read_bytes(), style)
        (folder / screens).mkdir(parents=True, exist_ok=True)
        (folder / screens / f"{picture.id}.png").write_bytes(screen)
        shown_screens.append((picture.id, Image.open(io.BytesIO(screen))))
    _contact(shown_screens, folder / f"{styled(f'contact{variant}', style)}.png")


def main() -> None:
    """Design voices, estimate, record, or play a story."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("story", nargs="?", help="the story's key (its folder in stories/)")
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--design", metavar="ROLE", help="make voice previews for a role in cast.yaml")
    parser.add_argument("--save", nargs=2, metavar=("ROLE", "N"), help="keep preview N as the role's voice")
    parser.add_argument("--cost", action="store_true", help="say what recording would cost; make nothing")
    parser.add_argument("--play", metavar="LANG", choices=LANGUAGES, help="write one wav of the whole story")
    parser.add_argument("--sheets", nargs="*", metavar="ROLE", help="draw the character sheets (all, or these)")
    parser.add_argument("--pictures", action="store_true", help="draw the story's scenes from the sheets")
    parser.add_argument("--style", default=DEFAULT_STYLE, choices=sorted(STYLES), help="the drawing style")
    parser.add_argument(
        "--turnaround", action="store_true", help="sheets: turnarounds from the single sheets; pictures: free poses"
    )
    parser.add_argument("--redo", nargs="*", default=[], metavar="ID", help="draw these sheets or scenes again")
    args = parser.parse_args()
    load_dotenv(find_dotenv(usecwd=True))
    out = args.out.expanduser()
    if args.design:
        return design(args.design)
    if args.save:
        return save(args.save[0], int(args.save[1]))
    if args.sheets is not None:
        return asyncio.run(sheets(args.sheets, set(args.redo), args.style, args.turnaround))
    if not args.story:
        parser.error("name a story, or use --design / --save")
    story = _story(args.story)
    if args.cost:
        return cost(story, out)
    if args.play:
        return play(story, out, args.play)
    if args.pictures:
        return asyncio.run(pictures(story, out, set(args.redo), args.style, args.turnaround))
    asyncio.run(build(story, out))


if __name__ == "__main__":
    main()
