"""The records wake-word learning shares between the app, the nightly job and the admin app, all owner-only.

- ``wake-clips/<day>/`` (the app): clips, and ``events.jsonl`` with one line per wake-model fire, near miss,
  shadow fire, transcript wake, antenna wake or "missed me" mark and what came of it. No transcripts.
- ``wake-features/<day>/`` (the app): the wake model's input features while it listens; not audio.
- ``wake-learning/`` (the nightly job and the admin app): the clips to check and the answers, the scoreboard,
  the wake heads (live, shadow, a proposed switch, history) and the household's voice recordings.
"""

import io
import os
import json
import wave
import logging
import threading
from typing import Any, Final
from pathlib import Path
from datetime import datetime, timezone, timedelta
from dataclasses import asdict, dataclass

from reachy_mini_conversation_app.wake_clips import EVENTS_FILE, wake_clips_directory


logger = logging.getLogger(__name__)

ANSWERS: Final[frozenset[str]] = frozenset({"call", "not_call", "unsure"})
# A switch can be undone for this long.
UNDO_DAYS: Final[int] = 7
HISTORY_KEPT: Final[int] = 5


def wake_features_directory() -> Path:
    """Return the feature log's folder, beside the wake clips."""
    return wake_clips_directory().with_name("wake-features")


def wake_learning_directory() -> Path:
    """Return the nightly job's and the admin app's folder, beside the wake clips."""
    return wake_clips_directory().with_name("wake-learning")


def write_private(path: Path, raw: bytes) -> None:
    """Replace ``path`` atomically with an owner-only file."""
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary = path.with_name(f".{path.name}.tmp")
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(descriptor, "wb") as file:
        file.write(raw)
    os.replace(temporary, path)


def append_line(path: Path, entry: dict[str, Any]) -> None:
    """Append ``entry`` as one JSON line to an owner-only file."""
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    with os.fdopen(descriptor, "a", encoding="utf-8") as file:
        file.write(json.dumps(entry, ensure_ascii=False) + "\n")


def read_lines(path: Path) -> list[dict[str, Any]]:
    """Return every JSON object line of ``path`` in order, skipping unreadable lines."""
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return []
    entries = []
    for line in text.splitlines():
        try:
            entry = json.loads(line)
        except ValueError:
            continue
        if isinstance(entry, dict):
            entries.append(entry)
    return entries


def _read_json(path: Path) -> dict[str, Any] | None:
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except (OSError, ValueError) as error:
        logger.warning("%s unreadable: %s", path.name, type(error).__name__)
        return None
    return document if isinstance(document, dict) else None


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@dataclass(frozen=True)
class HeadRecord:
    """A trained wake head: its file under ``models/heads``, its threshold and how it scored."""

    name: str
    threshold: float
    sha256: str
    created: str
    metrics: dict[str, float]

    @classmethod
    def from_json(cls, document: dict[str, Any]) -> "HeadRecord":
        """Read a record written by ``asdict``."""
        metrics = document.get("metrics") or {}
        return cls(
            name=str(document["name"]),
            threshold=float(document["threshold"]),
            sha256=str(document.get("sha256", "")),
            created=str(document.get("created", "")),
            metrics={str(key): float(value) for key, value in metrics.items()},
        )


@dataclass(frozen=True)
class ReviewItem:
    """One clip to check on the phone: which file, which seconds of it, and the question."""

    id: str
    clip: str
    start: float
    end: float
    question: str


class WakeLearningStore:
    """The wake-learning folder: clips to check, answers, the scoreboard and the wake heads."""

    def __init__(self, root: Path, clips: Path | None = None) -> None:
        """Keep records in ``root``; clips are read from ``clips`` (the app's wake-clip folder)."""
        self.root = root
        self.clips = clips or wake_clips_directory()
        self.heads = root / "models" / "heads"
        self.recordings = root / "recordings"
        self._lock = threading.Lock()

    # ---- the app's record of wake events -------------------------------------------------------------------
    def days(self) -> list[str]:
        """Return the days that have a wake event record, oldest first."""
        return sorted(path.parent.name for path in self.clips.glob(f"*/{EVENTS_FILE}"))

    def events(self, day: str) -> list[dict[str, Any]]:
        """Return the day's wake events in time order, each with what came of it (its notes) merged in."""
        merged: dict[str, dict[str, Any]] = {}
        for entry in read_lines(self.clips / day / EVENTS_FILE):
            event_id = entry.get("id")
            if not isinstance(event_id, str):
                continue
            if "kind" in entry:
                merged[event_id] = {**entry, **merged.get(event_id, {})}
            else:
                merged.setdefault(event_id, {}).update({key: value for key, value in entry.items() if key != "t"})
        return sorted((event for event in merged.values() if "kind" in event), key=lambda event: event["t"])

    # ---- clips to check ------------------------------------------------------------------------------------
    def write_queue(self, items: list[ReviewItem]) -> None:
        """Replace the clips to check (the nightly job)."""
        write_private(
            self.root / "queue.json", json.dumps({"made": _now(), "items": [asdict(i) for i in items]}).encode()
        )

    def queue(self) -> list[ReviewItem]:
        """Return the clips still to check, in order."""
        document = _read_json(self.root / "queue.json") or {}
        answered = self.answers()
        items = []
        for entry in document.get("items") or []:
            try:
                item = ReviewItem(
                    id=str(entry["id"]),
                    clip=str(entry["clip"]),
                    start=float(entry["start"]),
                    end=float(entry["end"]),
                    question=str(entry["question"]),
                )
            except (KeyError, TypeError, ValueError):
                continue
            if item.id not in answered:
                items.append(item)
        return items

    def answer(self, item_id: str, answer: str, who: str) -> None:
        """Keep an answer to a clip ("call", "not_call" or "unsure"); a later answer to the same clip wins."""
        if answer not in ANSWERS:
            raise ValueError("Unknown answer")
        with self._lock:
            append_line(self.root / "answers.jsonl", {"item": item_id, "answer": answer, "who": who, "at": _now()})

    def answers(self) -> dict[str, str]:
        """Return the latest answer per clip id."""
        return {
            str(entry["item"]): str(entry["answer"])
            for entry in read_lines(self.root / "answers.jsonl")
            if entry.get("answer") in ANSWERS and "item" in entry
        }

    def clip_audio(self, item: ReviewItem) -> bytes:
        """Return the item's seconds of its clip as a WAV file."""
        path = (self.clips / item.clip).resolve()
        if self.clips.resolve() not in path.parents or path.suffix != ".wav":
            raise FileNotFoundError(item.clip)
        with wave.open(str(path), "rb") as source:
            rate = source.getframerate()
            source.setpos(min(source.getnframes(), max(0, int(item.start * rate))))
            frames = source.readframes(max(0, int((item.end - item.start) * rate)))
            channels, width = source.getnchannels(), source.getsampwidth()
        out = io.BytesIO()
        with wave.open(out, "wb") as clip:
            clip.setnchannels(channels)
            clip.setsampwidth(width)
            clip.setframerate(rate)
            clip.writeframes(frames)
        return out.getvalue()

    # ---- the household's recordings of the name ------------------------------------------------------------
    def save_recording(self, person: str, prompt: str, sample_rate: int, pcm: bytes) -> str:
        """Keep one recording of ``person`` calling Reachy (``prompt`` says how) and return its file name."""
        slug = "".join(char if char.isalnum() else "-" for char in person.strip().lower()).strip("-") or "someone"
        stamp = datetime.now()
        name = f"{slug}/{stamp:%Y%m%d-%H%M%S}{stamp.microsecond // 1000:03d}.wav"
        out = io.BytesIO()
        with wave.open(out, "wb") as clip:
            clip.setnchannels(1)
            clip.setsampwidth(2)
            clip.setframerate(sample_rate)
            clip.writeframes(pcm)
        write_private(self.recordings / name, out.getvalue())
        with self._lock:
            append_line(
                self.recordings / "recordings.jsonl",
                {"person": person, "prompt": prompt, "file": name, "seconds": round(len(pcm) / 2 / sample_rate, 2),
                 "at": _now()},
            )  # fmt: skip
        return name

    def recordings_by_person(self) -> dict[str, dict[str, Any]]:
        """Return how many recordings each person has and when they last recorded."""
        people: dict[str, dict[str, Any]] = {}
        for entry in read_lines(self.recordings / "recordings.jsonl"):
            person = str(entry.get("person", ""))
            if not person or not (self.recordings / str(entry.get("file", ""))).is_file():
                continue
            summary = people.setdefault(person, {"count": 0, "last": ""})
            summary["count"] += 1
            summary["last"] = max(summary["last"], str(entry.get("at", "")))
        return people

    def all_recordings(self) -> list[dict[str, Any]]:
        """Return every recording still on disk: person, prompt, file, seconds and time."""
        return [
            entry
            for entry in read_lines(self.recordings / "recordings.jsonl")
            if (self.recordings / str(entry.get("file", ""))).is_file()
        ]

    # ---- scoreboard ----------------------------------------------------------------------------------------
    def write_scoreboard(self, scoreboard: dict[str, Any]) -> None:
        """Replace the scoreboard (the nightly job)."""
        write_private(self.root / "scoreboard.json", json.dumps({"made": _now(), **scoreboard}).encode())

    def scoreboard(self) -> dict[str, Any]:
        """Return the latest scoreboard, empty before the first night."""
        return _read_json(self.root / "scoreboard.json") or {}

    # ---- wake heads ----------------------------------------------------------------------------------------
    def head_path(self, record: HeadRecord) -> Path:
        """Return where ``record``'s ONNX file lives."""
        return self.heads / f"{record.name}.onnx"

    def _record(self, name: str) -> HeadRecord | None:
        document = _read_json(self.root / "models" / f"{name}.json")
        if document is None:
            return None
        try:
            return HeadRecord.from_json(document)
        except (KeyError, TypeError, ValueError):
            logger.warning("Wake head record %s unreadable", name)
            return None

    def _write_record(self, name: str, record: HeadRecord | None) -> None:
        path = self.root / "models" / f"{name}.json"
        if record is None:
            path.unlink(missing_ok=True)
        else:
            write_private(path, json.dumps(asdict(record)).encode())

    def live(self) -> HeadRecord | None:
        """Return the household head that wakes Reachy next to the main model, if there is one."""
        return self._record("live")

    def shadow(self) -> HeadRecord | None:
        """Return the head that only logs where it would have woken him, if there is one."""
        return self._record("shadow")

    def set_live(self, record: HeadRecord) -> None:
        """Make ``record`` the live head without a switch to undo (the first night, from the app's settings)."""
        self._write_record("live", record)

    def set_shadow(self, record: HeadRecord | None) -> None:
        """Run ``record`` in shadow from the next start, or none."""
        self._write_record("shadow", record)

    def proposal(self) -> dict[str, Any] | None:
        """Return the switch waiting for Eduardo: the shadow head's numbers against the live head's."""
        return _read_json(self.root / "models" / "proposal.json")

    def propose(self, summary: dict[str, Any]) -> None:
        """Offer switching to the shadow head, with the numbers that justify it."""
        shadow = self.shadow()
        if shadow is None:
            raise ValueError("No shadow head to propose")
        previous = self.proposal() or {}
        if previous.get("head") == shadow.name and previous.get("summary") == summary:
            return  # unchanged: a "not now" stands and nobody is asked again
        write_private(
            self.root / "models" / "proposal.json",
            json.dumps({"head": shadow.name, "summary": summary, "created": _now(), "notified": False}).encode(),
        )

    def mark_proposal_notified(self) -> None:
        """Remember that the phones were told about the current proposal."""
        proposal = self.proposal()
        if proposal is not None:
            proposal["notified"] = True
            write_private(self.root / "models" / "proposal.json", json.dumps(proposal).encode())

    def withdraw_proposal(self) -> None:
        """Drop the proposal (the shadow head changed or stopped winning)."""
        (self.root / "models" / "proposal.json").unlink(missing_ok=True)

    def switch(self, who: str) -> HeadRecord:
        """Make the proposed shadow head live; the old live head can come back with ``undo``."""
        with self._lock:
            proposal, shadow, live = self.proposal(), self.shadow(), self.live()
            if proposal is None or shadow is None or proposal.get("head") != shadow.name:
                raise ValueError("Nothing to switch to")
            append_line(
                self.root / "models" / "history.jsonl",
                {
                    "at": _now(),
                    "who": who,
                    "from": asdict(live) if live else None,
                    "to": asdict(shadow),
                    "summary": proposal.get("summary"),
                },
            )
            self._write_record("live", shadow)
            self._write_record("shadow", None)
            self.withdraw_proposal()
            self._prune_heads()
            return shadow

    def decline(self, who: str) -> None:
        """Keep the live head; the shadow keeps running and asks again only when its numbers change."""
        proposal = self.proposal()
        if proposal is not None:
            proposal.update(declined=True, declined_by=who, notified=True)
            write_private(self.root / "models" / "proposal.json", json.dumps(proposal).encode())

    def history(self) -> list[dict[str, Any]]:
        """Return past switches and undos, newest first."""
        return list(reversed(read_lines(self.root / "models" / "history.jsonl")))

    def undoable(self) -> dict[str, Any] | None:
        """Return the last switch while it can still be undone."""
        history = self.history()
        if not history or history[0].get("undo") or history[0].get("from") is None:
            return None
        try:
            at = datetime.fromisoformat(str(history[0]["at"]))
        except (KeyError, ValueError):
            return None
        return history[0] if datetime.now(timezone.utc) - at <= timedelta(days=UNDO_DAYS) else None

    def undo(self, who: str) -> HeadRecord:
        """Put back the live head the last switch replaced."""
        with self._lock:
            last = self.undoable()
            if last is None:
                raise ValueError("Nothing to undo")
            previous = HeadRecord.from_json(last["from"])
            append_line(
                self.root / "models" / "history.jsonl",
                {"at": _now(), "who": who, "from": last["to"], "to": asdict(previous), "undo": True},
            )
            self._write_record("live", previous)
            return previous

    def _prune_heads(self) -> None:
        """Keep the live and shadow heads and those of the last few switches; delete older head files."""
        keep = {record.name for record in (self.live(), self.shadow()) if record is not None}
        for entry in self.history()[:HISTORY_KEPT]:
            for side in ("from", "to"):
                if isinstance(entry.get(side), dict):
                    keep.add(str(entry[side].get("name")))
        for path in self.heads.glob("*.onnx"):
            if path.stem not in keep:
                path.unlink(missing_ok=True)
