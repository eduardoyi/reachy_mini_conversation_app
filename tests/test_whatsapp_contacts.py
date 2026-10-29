import json
import logging
from pathlib import Path

import pytest

from reachy_mini_conversation_app.quick_commands import normalize
from reachy_mini_conversation_app.whatsapp_contacts import Contact, find_contact, load_contacts


SERGIO = {"name": "Tío Sergio", "chat": "120363413010779824@g.us", "say": ["tio sergio", "zio sergio", "sergio"]}
NONNI = {"name": "los nonni", "chat": "120363000000000001@g.us", "say": ["nonna", "nonno", "nonni"]}


def _contacts(tmp_path: Path, *entries: object) -> list[Contact]:
    path = tmp_path / "contacts.json"
    path.write_text(json.dumps({"contacts": list(entries)}))
    return load_contacts(path, normalize)


def test_contacts_are_read_with_their_names_folded(tmp_path: Path) -> None:
    """Each group keeps its spoken name; the names people say are folded like quick commands."""
    (sergio,) = _contacts(tmp_path, SERGIO)
    assert sergio.name == "Tío Sergio" and sergio.chat == SERGIO["chat"]
    assert set(sergio.say) == {"tio sergio", "zio sergio", "sergio"}


def test_only_named_groups_are_contacts(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    """A person's own number, a missing id or a missing name is skipped, with a warning."""
    caplog.set_level(logging.WARNING)
    person = {**SERGIO, "chat": "351912345678@s.whatsapp.net"}
    contacts = _contacts(tmp_path, person, {"chat": NONNI["chat"]}, {"name": "x"}, "junk", NONNI)
    assert [contact.name for contact in contacts] == ["los nonni"]
    assert caplog.text.count("skipped") == 4
    assert "351912345678" not in caplog.text


def test_a_missing_or_broken_file_has_no_contacts(tmp_path: Path) -> None:
    """No file (or a broken one) means no family groups, never an error."""
    assert load_contacts(tmp_path / "missing.json", normalize) == []
    broken = tmp_path / "broken.json"
    broken.write_text("{not json")
    assert load_contacts(broken, normalize) == []


@pytest.mark.parametrize(
    ("phrase", "name"),
    [
        ("sergio", "Tío Sergio"),
        ("Tío Sergio", "Tío Sergio"),
        ("a sergio", "Tío Sergio"),
        ("para el tío Sergio", "Tío Sergio"),
        ("to uncle sergio", "Tío Sergio"),
        ("mi tio sergio", "Tío Sergio"),
        ("ai nonni", "los nonni"),
        ("la nonna", "los nonni"),
    ],
)
def test_names_with_articles_and_accents_find_their_group(tmp_path: Path, phrase: str, name: str) -> None:
    """Leading words like "a", "al", "para el", "to" and accents do not matter."""
    contact = find_contact(normalize(phrase), _contacts(tmp_path, SERGIO, NONNI))
    assert contact is not None and contact.name == name


@pytest.mark.parametrize("phrase", ["abuelo", "mario", "", "to", "sergio and the nonni"])
def test_an_unknown_or_ambiguous_name_finds_nobody(tmp_path: Path, phrase: str) -> None:
    """Nothing is guessed: no match, or two groups named at once, is no contact."""
    assert find_contact(normalize(phrase), _contacts(tmp_path, SERGIO, NONNI)) is None
