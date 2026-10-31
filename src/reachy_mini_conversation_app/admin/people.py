"""People cards: one person across the family-groups file, the call service's approved list and the face store.

The two files keep their formats (``whatsapp_contacts.py``, ``tools/whatsapp-calls``). Entries may carry a
``face`` field with the face store's person id so a card joins them; without it, cards join by name.
Fields the admin app does not know are kept as they were.
"""

import re
import logging
from typing import Any, Final
from pathlib import Path
from dataclasses import field, dataclass

from reachy_mini_conversation_app.admin.records import RecordError, StaleVersion, read_json, write_json
from reachy_mini_conversation_app.quick_commands import normalize
from reachy_mini_conversation_app.whatsapp_contacts import GROUP_SUFFIX


logger = logging.getLogger(__name__)

MAX_NAME_CHARS: Final[int] = 60
MAX_SPOKEN_NAMES: Final[int] = 12
MAX_PEOPLE: Final[int] = 100
_PHONE = re.compile(r"\+?[0-9][0-9 ()-]{5,22}")
_CONTROL = re.compile(r"[\x00-\x1f\x7f]")


class PersonError(ValueError):
    """A person the files cannot hold: a missing or duplicate name, a bad group id or phone number."""


@dataclass
class Person:
    """One card: what Reachy calls them, what people say, their group, their phone, their enrolled face."""

    name: str
    say: list[str] = field(default_factory=list)
    chat: str = ""
    phone: str = ""
    face: str = ""

    def as_json(self) -> dict[str, Any]:
        """Return the card as the admin app shows it."""
        return {"name": self.name, "say": self.say, "chat": self.chat, "phone": self.phone, "face": self.face}


def _entries(document: dict[str, Any], key: str) -> list[dict[str, Any]]:
    entries = document.get(key)
    return [entry for entry in entries if isinstance(entry, dict)] if isinstance(entries, list) else []


def _text(value: object) -> str:
    return str(value).strip() if isinstance(value, (str, int, float)) else ""


class PeopleFiles:
    """Read and write people across ``contacts_path`` and ``calls_path``."""

    def __init__(self, contacts_path: Path, calls_path: Path) -> None:
        """Use the family-groups file and the approved-callers file at these paths."""
        self._contacts_path = contacts_path
        self._calls_path = calls_path

    def load(self, faces: dict[str, str] | None) -> tuple[list[Person], dict[str, str]]:
        """Return the cards and the files' versions; ``faces`` maps face ids to names (None: store unreachable)."""
        contacts, contacts_version = read_json(self._contacts_path)
        calls, calls_version = read_json(self._calls_path)
        people: dict[str, Person] = {}
        face_by_name = {normalize(name): face_id for face_id, name in (faces or {}).items()}

        def card(entry: dict[str, Any]) -> Person | None:
            name = _text(entry.get("name"))
            if not name:
                return None
            key = normalize(name)
            person = people.setdefault(key, Person(name=name))
            face = _text(entry.get("face"))
            # With the face store unreachable a stored link is kept, so a save then never drops it.
            known = face if faces is None or face in faces else ""
            person.face = person.face or known or face_by_name.get(key, "")
            for said in entry.get("say") or []:
                said = _text(said)
                if said and said not in person.say:
                    person.say.append(said)
            return person

        for entry in _entries(contacts, "contacts"):
            person = card(entry)
            if person is not None:
                person.chat = _text(entry.get("chat"))
        for entry in _entries(calls, "approved"):
            person = card(entry)
            if person is not None:
                person.phone = _text(entry.get("phone"))
        return list(people.values()), {"contacts": contacts_version, "calls": calls_version}

    def save(self, people: list[Person], versions: dict[str, str]) -> dict[str, str]:
        """Write ``people`` to both files if neither changed since ``versions``; returns the new versions."""
        validate(people)
        contacts, contacts_version = read_json(self._contacts_path)
        calls, calls_version = read_json(self._calls_path)
        if versions.get("contacts") != contacts_version or versions.get("calls") != calls_version:
            raise StaleVersion("people changed elsewhere")
        old_contacts = {normalize(_text(e.get("name"))): e for e in _entries(contacts, "contacts")}
        old_calls = {normalize(_text(e.get("name"))): e for e in _entries(calls, "approved")}

        def entry(old: dict[str, Any] | None, person: Person, extra: dict[str, str]) -> dict[str, Any]:
            kept = {k: v for k, v in (old or {}).items() if k not in {"name", "say", "face", *extra}}
            spoken = {"say": person.say} if person.say else {}
            face = {"face": person.face} if person.face else {}
            return {"name": person.name, **extra, **spoken, **face, **kept}

        new_contacts = [entry(old_contacts.get(normalize(p.name)), p, {"chat": p.chat}) for p in people if p.chat]
        new_calls = [entry(old_calls.get(normalize(p.name)), p, {"phone": p.phone}) for p in people if p.phone]
        written_calls = calls_version
        if new_calls or calls_version:
            written_calls = write_json(self._calls_path, {**calls, "approved": new_calls}, calls_version)
        try:
            written_contacts = write_json(
                self._contacts_path, {**contacts, "contacts": new_contacts}, contacts_version
            )
        except RecordError:
            logger.warning("Approved callers were saved but family groups were not")
            raise
        return {"contacts": written_contacts, "calls": written_calls}


def person_from_json(raw: object) -> Person:
    """Return the card the admin app sent; raises PersonError when it is not one."""
    if not isinstance(raw, dict):
        raise PersonError("not a person")
    say = raw.get("say", [])
    if not isinstance(say, list) or not all(isinstance(said, str) for said in say):
        raise PersonError("spoken names must be a list of text")
    values = {key: raw.get(key, "") for key in ("name", "chat", "phone", "face")}
    if not all(isinstance(value, str) for value in values.values()):
        raise PersonError("name, group, phone and face must be text")
    spoken = list(dict.fromkeys(said.strip() for said in say if said.strip()))
    phone = values["phone"].strip()
    if phone:
        digits = re.sub(r"\D", "", phone)
        if not digits:
            raise PersonError(f"{values['name'].strip() or 'This person'}'s phone number is not valid")
        phone = ("+" if phone.startswith("+") else "") + digits
    return Person(
        name=values["name"].strip(), say=spoken, chat=values["chat"].strip(), phone=phone, face=values["face"].strip()
    )


def validate(people: list[Person]) -> None:
    """Raise PersonError for a list the files cannot hold."""
    if len(people) > MAX_PEOPLE:
        raise PersonError(f"at most {MAX_PEOPLE} people")
    seen: set[str] = set()
    for person in people:
        for text in (person.name, *person.say):
            if not text or len(text) > MAX_NAME_CHARS or _CONTROL.search(text):
                raise PersonError(f"names must be 1 to {MAX_NAME_CHARS} characters")
        if len(person.say) > MAX_SPOKEN_NAMES:
            raise PersonError(f"at most {MAX_SPOKEN_NAMES} spoken names for {person.name}")
        key = normalize(person.name)
        if not key or key in seen:
            raise PersonError(f"{person.name} is listed twice")
        seen.add(key)
        if person.chat and (not person.chat.endswith(GROUP_SUFFIX) or _CONTROL.search(person.chat)):
            raise PersonError(f"{person.name}'s WhatsApp group must be a group id")
        if person.phone and not _PHONE.fullmatch(person.phone):
            raise PersonError(f"{person.name}'s phone number is not valid")


def shared_spoken_names(people: list[Person]) -> list[dict[str, Any]]:
    """Return each spoken name more than one person answers to, with whose it is."""
    owners: dict[str, list[str]] = {}
    for person in people:
        for said in {normalize(text) for text in (person.name, *person.say)}:
            owners.setdefault(said, []).append(person.name)
    return [{"said": said, "people": names} for said, names in sorted(owners.items()) if len(names) > 1]
