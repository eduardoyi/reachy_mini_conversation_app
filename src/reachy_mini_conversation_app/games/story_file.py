"""Story files: a story as data, so adding one means writing a folder, not code (docs/plans/story-mode.md).

A story is ``stories/<key>/story.yaml`` in this package: its names and pitch in every language it is told in, and
its steps in order. The cast's voices are in ``stories/cast.yaml`` (a role's ElevenLabs voice, the same in every
story and language), the sounds and music in ``stories/sounds.yaml``, and the lines every story shares (the
opening, "¿Otro cuento?", goodbye) in ``stories/common.yaml``.

A line is ``{who: role, es: "...", it: "..."}`` with audio tags ("[whispers]") in its text; ``who`` is the
narrator when left out. Each line's recording is named by a hash of its voice, its language and its tagged text
(``clip_id``), so changing a line, or a role's voice, makes a new recording and leaves the rest alone.

Steps, each a mapping with one of these keys:

- ``say``: a line (``say: wolf`` with the text beside it, or ``say: {who:, es:, it:}``); with ``about: <choice
  id>`` and ``options: {<option>: line}``, the line of the option that was picked.
- ``sound``: a sound from sounds.yaml, played before the next line.
- ``scene``: ``{id:, picture: <what the drawing shows>, with: [<roles in it>], move: <emotion>}``, a new picture on
  the screen (made ahead of time from the characters' sheets, see story_pictures.py); with ``about: <choice id>`` and
  ``options: {<option>: <what the drawing shows>}``, the picture of the option that was picked. A story's ``cover``
  is a scene too, shown as it starts.
- ``move``: an emotion move ("happy", "scared", "dance").
- ``music``: ``{name:, seconds:}``, music from sounds.yaml for a while (a dance).
- ``noise``: the kids make a noise (blow, roar, cheer): lines ``ask``, ``done``, ``louder``, ``without``; ``sound``
  plays after ``done`` or ``without``.
- ``chorus``: the kids say a line everybody knows: ``ask``, ``done``, ``without`` (someone in the story says it).
- ``pat``: the kids pat Richie's head (knock on a door): ``ask``, ``done``, ``without``; ``sound`` after either.
- ``copy``: the kids copy a move: ``ask``, ``done``, with ``move`` (Richie's) and ``seconds``; ``music`` under it.
- ``choice``: the kids pick one of two: ``id``, ``ask``, and ``options: {<option>: {words: {es: [...]},
  picked: line, then: line}}``; ``picked`` is what Richie says when he picks it himself, ``then`` follows either way.
"""

import hashlib
from typing import Any, Final
from pathlib import Path
from dataclasses import field, dataclass
from collections.abc import Mapping, Iterator, Sequence

import yaml  # PyYAML, installed with huggingface-hub (locking it here fails on pygobject)

from reachy_mini_conversation_app.quick_commands import normalize


STORIES_DIR: Final[Path] = Path(__file__).resolve().parent.parent / "stories"
LANGUAGES: Final[tuple[str, ...]] = ("es", "it")
NARRATOR: Final[str] = "narrator"
# Interactions and the lines each one needs.
INTERACTIONS: Final[dict[str, tuple[str, ...]]] = {
    "noise": ("ask", "done", "louder", "without"),
    "chorus": ("ask", "done", "without"),
    "pat": ("ask", "done", "without"),
    "copy": ("ask", "done"),
    "choice": ("ask",),
}
_PLAIN: Final[tuple[str, ...]] = ("say", "sound", "scene", "move", "music")
KINDS: Final[tuple[str, ...]] = _PLAIN + tuple(INTERACTIONS)
# Recorded with Eleven v4 Turbo, like the games (the model is part of each clip's name, so a new model records everything again).
MODEL: Final[str] = "eleven_v4_turbo"


class StoryError(ValueError):
    """A story file that cannot be told: the message says where and why."""


@dataclass(frozen=True)
class Line:
    """Something one role says, in every language."""

    who: str
    text: Mapping[str, str]

    def tagged(self, language: str) -> str:
        """Return the text with its audio tags, as it is recorded."""
        return self.text.get(language, "")


@dataclass(frozen=True)
class Option:
    """One answer to a choice: the words that pick it, Richie's line when he picks it, and what follows."""

    key: str
    words: Mapping[str, tuple[str, ...]]
    picked: Line
    then: Line


@dataclass(frozen=True)
class Picture:
    """One picture of a story: its name (the file it is kept in), what it shows, and the characters in it."""

    id: str
    prompt: str
    cast: tuple[str, ...]


@dataclass(frozen=True)
class Step:
    """One step of a story; ``kind`` says which of the fields mean something."""

    kind: str
    # say: the line (or, with ``choice``, one line per option in ``variants``).
    line: Line | None = None
    choice: str = ""
    variants: Mapping[str, Line] = field(default_factory=dict)
    # sound, music, move; a scene's picture ("" key), or one per option of the choice it is ``about``.
    name: str = ""
    pictures: Mapping[str, Picture] = field(default_factory=dict)
    move: str = ""
    seconds: float = 0.0
    music: str = ""
    # Interactions: their lines by part ("ask", "done", ...), and a choice's options.
    lines: Mapping[str, Line] = field(default_factory=dict)
    options: tuple[Option, ...] = ()
    sound: str = ""


@dataclass(frozen=True)
class Story:
    """A story file, read and checked."""

    key: str
    title: Mapping[str, str]
    names: Mapping[str, tuple[str, ...]]
    pitch: Mapping[str, str]
    rules: str
    cover: Picture | None
    steps: tuple[Step, ...]

    def lines(self) -> Iterator[Line]:
        """Every line the story can say, every choice's branches included."""
        for step in self.steps:
            if step.line is not None:
                yield step.line
            yield from step.variants.values()
            yield from step.lines.values()
            for option in step.options:
                yield option.picked
                yield option.then

    def sounds(self) -> set[str]:
        """Every sound and music the story plays."""
        names = {step.name for step in self.steps if step.kind in ("sound", "music")}
        names |= {step.sound for step in self.steps if step.sound}
        names |= {step.music for step in self.steps if step.music}
        return names

    def pictures(self) -> list[Picture]:
        """Every picture the story shows, its cover first, every option's included."""
        pictures = [self.cover] if self.cover is not None else []
        return pictures + [picture for step in self.steps for picture in step.pictures.values()]


@dataclass(frozen=True)
class Common:
    """The lines every story shares, by moment ("again", "goodbye", ...)."""

    lines: Mapping[str, Line]


def clip_id(voice: str, language: str, tagged: str) -> str:
    """Name the recording of ``tagged`` (text with tags) in ``voice`` and ``language``."""
    digest = hashlib.sha256(f"{MODEL}\n{voice}\n{language}\n{tagged.strip()}".encode()).hexdigest()
    return digest[:16]


def _where(story: str, index: int | None) -> str:
    return f"{story}, step {index + 1}" if index is not None else story


def _line(raw: Any, where: str, cast: Mapping[str, Any], who: str = NARRATOR) -> Line:
    """Read a line: a mapping with a text per language and an optional ``who``."""
    if not isinstance(raw, Mapping):
        raise StoryError(f"{where}: a line needs a text per language, got {raw!r}")
    role = str(raw.get("who", who))
    if role not in cast:
        raise StoryError(f"{where}: {role!r} is not in cast.yaml")
    text = {language: str(raw.get(language, "")).strip() for language in LANGUAGES}
    missing = [language for language, said in text.items() if not said]
    if missing:
        raise StoryError(f"{where}: no {', '.join(missing)} text")
    return Line(role, text)


def _words(raw: Any, where: str) -> dict[str, tuple[str, ...]]:
    if not isinstance(raw, Mapping):
        raise StoryError(f"{where}: words need a list per language")
    words = {language: tuple(normalize(str(word)) for word in raw.get(language) or ()) for language in LANGUAGES}
    if not all(words.values()):
        raise StoryError(f"{where}: words in every language are needed")
    return words


def _step(raw: Any, where: str, cast: Mapping[str, Any], sounds: Mapping[str, Any], choices: set[str]) -> Step:
    if not isinstance(raw, Mapping):
        raise StoryError(f"{where}: a step is a mapping")
    kinds = [kind for kind in KINDS if kind in raw]
    if len(kinds) != 1:
        raise StoryError(f"{where}: a step needs exactly one of {', '.join(KINDS)}, got {sorted(raw)}")
    kind = kinds[0]
    value = raw[kind]

    def sound_name(given: Any, what: str = "sound") -> str:
        name = str(given or "")
        if name and name not in sounds:
            raise StoryError(f"{where}: {what} {name!r} is not in sounds.yaml")
        return name

    if kind == "say":
        if "about" in raw:
            choice = str(raw["about"])
            if choice not in choices:
                raise StoryError(f"{where}: choice {choice!r} is not asked before this line")
            options = raw.get("options")
            if not isinstance(options, Mapping) or not options:
                raise StoryError(f"{where}: a line about a choice needs its options")
            who = str(value) if isinstance(value, str) else NARRATOR
            variants = {str(key): _line(line, f"{where} ({key})", cast, who) for key, line in options.items()}
            return Step(kind, choice=choice, variants=variants)
        if isinstance(value, str) or value is None:  # "say: wolf" with the texts beside it
            line = _line({**raw, "who": str(value or NARRATOR)}, where, cast)
        else:
            line = _line(value, where, cast)
        return Step(kind, line=line)
    if kind == "sound":
        return Step(kind, name=sound_name(value))
    if kind == "move":
        return Step(kind, move=str(value))
    if kind == "music":
        if not isinstance(value, Mapping):
            raise StoryError(f"{where}: music needs a name and seconds")
        return Step(kind, name=sound_name(value.get("name"), "music"), seconds=float(value.get("seconds", 8)))
    if kind == "scene":
        if not isinstance(value, Mapping):
            raise StoryError(f"{where}: a scene needs an id and a picture")
        about = str(value.get("about") or "")
        if about and about not in choices:
            raise StoryError(f"{where}: choice {about!r} is not asked before this scene")
        return Step(kind, choice=about, pictures=_pictures(value, where, cast), move=str(value.get("move", "")))
    if not isinstance(value, Mapping):
        raise StoryError(f"{where}: {kind} needs its lines")
    lines = {}
    for part in INTERACTIONS[kind]:
        if part not in value:
            raise StoryError(f"{where}: {kind} needs {part!r}")
        lines[part] = _line(value[part], f"{where} {part}", cast)
    common = {
        "lines": lines,
        "sound": sound_name(value.get("sound")),
        "music": sound_name(value.get("music"), "music"),
        "move": str(value.get("move", "")),
        "seconds": float(value.get("seconds", 0) or 0),
    }
    if kind != "choice":
        return Step(kind, **common)  # type: ignore[arg-type]
    choice = str(value.get("id", ""))
    if not choice or choice in choices:
        raise StoryError(f"{where}: a choice needs an id of its own")
    raw_options = value.get("options")
    if not isinstance(raw_options, Mapping) or len(raw_options) < 2:
        raise StoryError(f"{where}: a choice needs two options or more")
    options = []
    for key, option in raw_options.items():
        if not isinstance(option, Mapping):
            raise StoryError(f"{where} ({key}): an option needs words, picked and then")
        options.append(
            Option(
                str(key),
                _words(option.get("words"), f"{where} ({key}) words"),
                _line(option.get("picked"), f"{where} ({key}) picked", cast),
                _line(option.get("then"), f"{where} ({key}) then", cast),
            )
        )
    choices.add(choice)
    return Step(kind, choice=choice, options=tuple(options), **common)  # type: ignore[arg-type]


def _pictures(raw: Mapping[str, Any], where: str, cast: Mapping[str, Mapping[str, str]]) -> dict[str, Picture]:
    """Read a scene's picture, or its picture per option ("" key for a single one)."""
    key = str(raw.get("id") or "")
    if not key or not key.replace("_", "").isalnum():
        raise StoryError(f"{where}: a scene needs an id of letters, digits and _")
    roles = tuple(str(role) for role in raw.get("with") or ())
    for role in roles:
        if not cast.get(role, {}).get("look"):
            raise StoryError(f"{where}: {role!r} has no look in cast.yaml")
    options = raw.get("options")
    if "about" in raw:
        if not isinstance(options, Mapping) or not options:
            raise StoryError(f"{where}: a scene about a choice needs a picture per option")
        return {str(option): Picture(f"{key}-{option}", str(text), roles) for option, text in options.items()}
    if not raw.get("picture"):
        raise StoryError(f"{where}: a scene needs a picture")
    return {"": Picture(key, str(raw["picture"]), roles)}


def _check_variants(story: str, steps: Sequence[Step]) -> None:
    """Check that a line about a choice has a line for each of the choice's options."""
    asked = {step.choice: {option.key for option in step.options} for step in steps if step.kind == "choice"}
    for index, step in enumerate(steps):
        if step.kind == "say" and step.choice and set(step.variants) != asked[step.choice]:
            raise StoryError(f"{_where(story, index)}: the options must be {sorted(asked[step.choice])}")
        if step.kind == "scene" and step.choice and set(step.pictures) != asked[step.choice]:
            raise StoryError(f"{_where(story, index)}: the options must be {sorted(asked[step.choice])}")


def _yaml(path: Path) -> Any:
    try:
        return yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as error:
        raise StoryError(f"{path.name}: {error}") from error


def load_cast(folder: Path = STORIES_DIR) -> dict[str, dict[str, str]]:
    """Return the cast: role to ``{"voice": ElevenLabs voice id ("" until designed), "description": ..., "look": ...}``.

    ``look`` is how the character is drawn ("" for the narrator, who is never drawn).
    """
    raw = _yaml(folder / "cast.yaml") or {}
    if not isinstance(raw, Mapping) or NARRATOR not in raw:
        raise StoryError("cast.yaml: needs the narrator")
    return {
        str(role): {
            "voice": str((info or {}).get("voice") or ""),
            "description": str((info or {}).get("description", "")),
            "look": " ".join(str((info or {}).get("look") or "").split()),
        }
        for role, info in raw.items()
    }


def load_sounds(folder: Path = STORIES_DIR) -> dict[str, dict[str, Any]]:
    """Return the sounds and music: name to ``{"kind": "sound" | "music", "seconds": ..., "prompt": ...}``."""
    raw = _yaml(folder / "sounds.yaml") or {}
    sounds: dict[str, dict[str, Any]] = {}
    for kind in ("sound", "music"):
        for name, info in (raw.get(kind) or {}).items():
            sounds[str(name)] = {"kind": kind, "seconds": float(info["seconds"]), "prompt": str(info["prompt"])}
    return sounds


def load_common(folder: Path = STORIES_DIR) -> Common:
    """Return the lines every story shares."""
    cast = load_cast(folder)
    raw = _yaml(folder / "common.yaml") or {}
    return Common({str(moment): _line(line, f"common.yaml {moment}", cast) for moment, line in raw.items()})


def load_story(path: Path, folder: Path = STORIES_DIR) -> Story:
    """Read and check ``path`` (a story.yaml); raise StoryError saying what is wrong."""
    cast, sounds = load_cast(folder), load_sounds(folder)
    raw = _yaml(path)
    name = path.parent.name
    if not isinstance(raw, Mapping):
        raise StoryError(f"{name}: not a story")
    for part in ("title", "names", "pitch"):
        if not isinstance(raw.get(part), Mapping) or not all(raw[part].get(language) for language in LANGUAGES):
            raise StoryError(f"{name}: {part} needs every language ({', '.join(LANGUAGES)})")
    choices: set[str] = set()
    steps = tuple(
        _step(step, _where(name, index), cast, sounds, choices) for index, step in enumerate(raw.get("steps") or ())
    )
    if not steps:
        raise StoryError(f"{name}: no steps")
    _check_variants(name, steps)
    cover = _pictures(raw["cover"], f"{name} cover", cast)[""] if isinstance(raw.get("cover"), Mapping) else None
    ids = [
        picture.id for picture in ([cover] if cover else []) + [p for step in steps for p in step.pictures.values()]
    ]
    if len(ids) != len(set(ids)):
        raise StoryError(f"{name}: two scenes share an id")
    return Story(
        key=str(raw.get("key") or name),
        title={language: str(raw["title"][language]) for language in LANGUAGES},
        names={language: tuple(normalize(str(each)) for each in raw["names"][language]) for language in LANGUAGES},
        pitch={language: str(raw["pitch"][language]) for language in LANGUAGES},
        rules=str(raw.get("rules", "")),
        cover=cover,
        steps=steps,
    )


def load_stories(folder: Path = STORIES_DIR) -> dict[str, Story]:
    """Return every story under ``folder``, by key; raise StoryError on the first that is wrong."""
    stories = [load_story(path, folder) for path in sorted(folder.glob("*/story.yaml"))]
    return {story.key: story for story in stories}
