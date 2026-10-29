"""The family WhatsApp groups Reachy may send messages and photos to, and the names people use for them.

``WHATSAPP_CONTACTS_FILE`` (JSON) lists each group with a spoken name and the names people say:

    {"contacts": [{"name": "Tío Sergio", "chat": "120363...@g.us",
                   "say": ["tio sergio", "zio sergio", "uncle sergio", "sergio"]}]}

Only group ids are accepted: each group holds the family member and both parents, who see every message.
The file is read on each request, so an edit needs no restart.
"""

import json
import logging
from typing import Final, Callable
from pathlib import Path
from dataclasses import dataclass


logger = logging.getLogger(__name__)

GROUP_SUFFIX: Final[str] = "@g.us"
# Leading words before a name: "a Sergio", "al abuelo", "to uncle Sergio", "para el tío Sergio", "ai nonni".
_LEADING_WORDS: Final[frozenset[str]] = frozenset(
    {"a", "al", "ao", "aos", "as", "ai", "alla", "alle", "allo", "agli", "to", "for", "para", "per", "the", "my",
     "mi", "mis", "el", "la", "los", "las", "il", "lo", "i", "gli", "le", "o", "os", "meu", "minha", "mio", "mia"}
)  # fmt: skip


@dataclass(frozen=True)
class Contact:
    """One family group: what Reachy calls it, its WhatsApp id, and the folded names that mean it."""

    name: str
    chat: str
    say: tuple[str, ...]


def load_contacts(path: str | Path, fold: Callable[[str], str]) -> list[Contact]:
    """Return the valid contacts in ``path`` (none when it is missing or unreadable), names folded by ``fold``."""
    file = Path(path)
    if not file.is_file():
        return []
    try:
        raw = json.loads(file.read_text())
    except (OSError, ValueError) as error:
        logger.warning("WhatsApp contacts unreadable: %s", type(error).__name__)
        return []
    entries = raw.get("contacts") if isinstance(raw, dict) else None
    contacts = []
    for index, entry in enumerate(entries if isinstance(entries, list) else []):
        name = str(entry.get("name") or "").strip() if isinstance(entry, dict) else ""
        chat = str(entry.get("chat") or "").strip() if isinstance(entry, dict) else ""
        if not name or not chat.endswith(GROUP_SUFFIX):
            logger.warning("WhatsApp contact %d skipped: it needs a name and a group id", index)
            continue
        names = [fold(str(said)) for said in entry.get("say") or [] if str(said).strip()]
        contacts.append(Contact(name, chat, tuple(dict.fromkeys([*names, fold(name)]))))
    return contacts


def find_contact(phrase: str, contacts: list[Contact]) -> Contact | None:
    """Return the one contact ``phrase`` (already folded) names, or None when it names none or several."""
    words = phrase.split()
    while words and words[0] in _LEADING_WORDS:
        words = words[1:]
    if not words:
        return None
    spoken = " ".join(words)
    best: dict[str, tuple[int, Contact]] = {}
    for contact in contacts:
        for alias in contact.say:
            alias_words = alias.split()
            if alias == spoken or (alias_words and all(word in words for word in alias_words)):
                length = len(alias_words)
                if length > best.get(contact.chat, (0, contact))[0]:
                    best[contact.chat] = (length, contact)
    if not best:
        return None
    longest = max(length for length, _ in best.values())
    matches = [contact for length, contact in best.values() if length == longest]
    return matches[0] if len(matches) == 1 else None
