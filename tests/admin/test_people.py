"""People cards across the family-groups file, the approved-callers file and the face store."""

import json
from pathlib import Path

import pytest

from reachy_mini_conversation_app.admin.people import (
    MAX_PEOPLE,
    MAX_NAME_CHARS,
    MAX_SPOKEN_NAMES,
    Person,
    PeopleFiles,
    PersonError,
    validate,
    person_from_json,
    shared_spoken_names,
)
from reachy_mini_conversation_app.admin.records import StaleVersion, read_json
from reachy_mini_conversation_app.quick_commands import normalize
from reachy_mini_conversation_app.whatsapp_calls import load_callees
from reachy_mini_conversation_app.whatsapp_contacts import load_contacts


GROUP = "120363000000000001@g.us"


def _write(path: Path, document: object) -> None:
    path.write_text(json.dumps(document, ensure_ascii=False))


@pytest.fixture
def files(tmp_path: Path) -> tuple[PeopleFiles, Path, Path]:
    """Both files in a temp folder; neither exists yet."""
    contacts, calls = tmp_path / "contacts.json", tmp_path / "calls.json"
    return PeopleFiles(contacts, calls), contacts, calls


def test_load_with_no_files_is_empty(files: tuple[PeopleFiles, Path, Path]) -> None:
    """No files yet: no cards and empty versions."""
    people_files, _, _ = files
    assert people_files.load({}) == ([], {"contacts": "", "calls": ""})


def test_load_joins_both_files_by_name_ignoring_accents_and_case(files: tuple[PeopleFiles, Path, Path]) -> None:
    """One person in both files is one card with their group, phone and every spoken name."""
    people_files, contacts, calls = files
    _write(contacts, {"contacts": [{"name": "Abuela Rosí", "chat": GROUP, "say": ["abuela", "yaya"]}]})
    _write(calls, {"approved": [{"name": "abuela rosi", "phone": "+34600111222", "say": ["abuela", "rosi"]}]})
    people, versions = people_files.load({})
    assert [p.as_json() for p in people] == [
        {"name": "Abuela Rosí", "say": ["abuela", "yaya", "rosi"], "chat": GROUP, "phone": "+34600111222", "face": ""}
    ]
    assert versions == {"contacts": read_json(contacts)[1], "calls": read_json(calls)[1]}
    assert all(versions.values())


def test_load_keeps_people_found_in_only_one_file(files: tuple[PeopleFiles, Path, Path]) -> None:
    """A group without a phone and a caller without a group are both cards."""
    people_files, contacts, calls = files
    _write(contacts, {"contacts": [{"name": "Primos", "chat": GROUP}]})
    _write(calls, {"approved": [{"name": "Tío Sergio", "phone": 34600111222}]})
    people, _ = people_files.load({})
    assert [(p.name, p.chat, p.phone) for p in people] == [("Primos", GROUP, ""), ("Tío Sergio", "", "34600111222")]


def test_load_skips_malformed_entries(files: tuple[PeopleFiles, Path, Path]) -> None:
    """Entries that are not objects or have no name are ignored, as is a non-list section."""
    people_files, contacts, calls = files
    _write(contacts, {"contacts": ["junk", {"chat": GROUP}, {"name": "  "}, {"name": "Ana", "chat": GROUP}]})
    _write(calls, {"approved": {"name": "not a list"}})
    people, _ = people_files.load({})
    assert [p.name for p in people] == ["Ana"]


def test_load_joins_faces_by_face_field_or_by_name(files: tuple[PeopleFiles, Path, Path]) -> None:
    """A ``face`` field naming an enrolled face wins; otherwise an enrolled face with the same name joins."""
    people_files, contacts, calls = files
    _write(
        contacts,
        {
            "contacts": [
                {"name": "Mamá", "chat": GROUP, "face": "adriana"},
                {"name": "Papá", "chat": GROUP},
                {"name": "Ghost", "chat": GROUP, "face": "deleted-face"},
            ]
        },
    )
    _write(calls, {"approved": [{"name": "Lucia", "phone": "+34600111222"}]})
    faces = {"adriana": "Adriana", "papa": "papa", "lucia": "Lucía"}
    people, _ = people_files.load(faces)
    assert {p.name: p.face for p in people} == {"Mamá": "adriana", "Papá": "papa", "Ghost": "", "Lucia": "lucia"}


def test_save_writes_both_files_and_keeps_unknown_fields(files: tuple[PeopleFiles, Path, Path]) -> None:
    """Saving rewrites the cards but keeps fields and top-level keys the admin app does not know."""
    people_files, contacts, calls = files
    _write(contacts, {"version": 2, "contacts": [{"name": "Ana", "chat": GROUP, "note": "keep me"}]})
    _write(calls, {"service": "x", "approved": [{"name": "ana", "phone": "+34600111222", "ring": 30}]})
    people, versions = people_files.load({})
    people[0].say = ["anita"]
    people.append(Person(name="Ben", chat="999@g.us", face="ben"))
    new_versions = people_files.save(people, versions)

    saved_contacts, contacts_version = read_json(contacts)
    saved_calls, calls_version = read_json(calls)
    assert new_versions == {"contacts": contacts_version, "calls": calls_version}
    assert saved_contacts == {
        "version": 2,
        "contacts": [
            {"name": "Ana", "chat": GROUP, "say": ["anita"], "note": "keep me"},
            {"name": "Ben", "chat": "999@g.us", "face": "ben"},
        ],
    }
    assert saved_calls == {
        "service": "x",
        "approved": [{"name": "Ana", "phone": "+34600111222", "say": ["anita"], "ring": 30}],
    }


def test_save_drops_people_without_chat_or_phone_from_that_file(files: tuple[PeopleFiles, Path, Path]) -> None:
    """Contacts hold only people with a group, calls only people with a phone."""
    people_files, contacts, calls = files
    _write(contacts, {"contacts": [{"name": "Ana", "chat": GROUP}]})
    _write(calls, {"approved": [{"name": "Ana", "phone": "+34600111222"}]})
    _, versions = people_files.load({})
    people_files.save([Person(name="Ana", chat=GROUP), Person(name="Ben", phone="+34600333444")], versions)
    assert [e["name"] for e in read_json(contacts)[0]["contacts"]] == ["Ana"]
    assert [e["name"] for e in read_json(calls)[0]["approved"]] == ["Ben"]


def test_save_does_not_create_calls_file_without_phones(files: tuple[PeopleFiles, Path, Path]) -> None:
    """A household without the call service does not get an empty approved list."""
    people_files, contacts, calls = files
    versions = people_files.save([Person(name="Ana", chat=GROUP)], {"contacts": "", "calls": ""})
    assert contacts.exists() and not calls.exists()
    assert versions["calls"] == ""
    assert people_files.load({})[1] == versions


def test_save_empties_an_existing_calls_file(files: tuple[PeopleFiles, Path, Path]) -> None:
    """Removing the last phone empties the approved list rather than leaving stale callers."""
    people_files, _, calls = files
    _write(calls, {"approved": [{"name": "Ana", "phone": "+34600111222"}]})
    _, versions = people_files.load({})
    people_files.save([Person(name="Ana", chat=GROUP)], versions)
    assert read_json(calls)[0] == {"approved": []}


@pytest.mark.parametrize("stale", ["contacts", "calls"])
def test_save_refuses_stale_versions_and_writes_nothing(files: tuple[PeopleFiles, Path, Path], stale: str) -> None:
    """If either file changed since it was loaded, neither is written."""
    people_files, contacts, calls = files
    _write(contacts, {"contacts": [{"name": "Ana", "chat": GROUP}]})
    _write(calls, {"approved": [{"name": "Ana", "phone": "+34600111222"}]})
    _, versions = people_files.load({})
    changed = {"contacts": contacts, "calls": calls}[stale]
    changed.write_text(changed.read_text() + "\n")
    before = (contacts.read_bytes(), calls.read_bytes())
    with pytest.raises(StaleVersion):
        people_files.save([Person(name="Ben", chat=GROUP, phone="+34600333444")], versions)
    assert (contacts.read_bytes(), calls.read_bytes()) == before


def test_save_validates_before_writing(files: tuple[PeopleFiles, Path, Path]) -> None:
    """An invalid list is refused without touching the files."""
    people_files, contacts, _ = files
    with pytest.raises(PersonError):
        people_files.save([Person(name="Ana", chat="not-a-group")], {"contacts": "", "calls": ""})
    assert not contacts.exists()


def test_saved_files_are_readable_by_the_apps_own_loaders(files: tuple[PeopleFiles, Path, Path]) -> None:
    """What the admin app writes, the conversation app and the call command can use."""
    people_files, contacts, calls = files
    people = [
        Person(name="Abuela Rosí", say=["abuela", "yaya"], chat=GROUP, phone="+34600111222", face="rosi"),
        Person(name="Tío Sergio", phone="+39 333 1234567"),
        Person(name="Primos", chat="555@g.us"),
    ]
    people_files.save(people, {"contacts": "", "calls": ""})

    groups = load_contacts(contacts, normalize)
    callees = load_callees(calls, normalize)
    assert [(c.name, c.chat, c.say) for c in groups] == [
        ("Abuela Rosí", GROUP, ("abuela", "yaya", "abuela rosi")),
        ("Primos", "555@g.us", ("primos",)),
    ]
    assert [(c.name, c.say) for c in callees] == [
        ("Abuela Rosí", ("abuela", "yaya", "abuela rosi")),
        ("Tío Sergio", ("tio sergio",)),
    ]


def test_round_trip_keeps_cards_as_they_were(files: tuple[PeopleFiles, Path, Path]) -> None:
    """Load after save shows the same cards."""
    people_files, _, _ = files
    people = [Person(name="Ana", say=["anita"], chat=GROUP, phone="+34600111222", face="ana")]
    people_files.save(people, {"contacts": "", "calls": ""})
    assert people_files.load({"ana": "Ana"})[0] == people


def test_saving_while_faces_are_unavailable_keeps_face_links(files: tuple[PeopleFiles, Path, Path]) -> None:
    """Editing people while the app (and so the face list) is unavailable must not unlink faces."""
    people_files, contacts, _ = files
    _write(contacts, {"contacts": [{"name": "Ana", "chat": GROUP, "face": "ana"}]})
    people, versions = people_files.load(None)
    people_files.save(people, versions)
    assert read_json(contacts)[0]["contacts"][0].get("face") == "ana"
    people, versions = people_files.load({})
    assert people[0].face == ""


# person_from_json -----------------------------------------------------------------------------------


def test_person_from_json_trims_and_normalizes() -> None:
    """Text is trimmed, spoken names deduplicated, and the phone kept as + and digits."""
    person = person_from_json(
        {"name": "  Ana ", "say": [" anita", "anita", "", "  "], "chat": f" {GROUP} ", "phone": " +34 (600) 111-222 "}
    )
    assert person == Person(name="Ana", say=["anita"], chat=GROUP, phone="+34600111222", face="")


def test_person_from_json_defaults_missing_fields() -> None:
    """Only a name is needed; the rest default to empty."""
    assert person_from_json({"name": "Ana"}) == Person(name="Ana")


def test_person_from_json_phone_without_plus_stays_without() -> None:
    """A number written without + is not given one."""
    assert person_from_json({"name": "Ana", "phone": "600 111 222"}).phone == "600111222"


@pytest.mark.parametrize(
    "raw",
    [
        "Ana",
        ["Ana"],
        None,
        {"name": "Ana", "say": "anita"},
        {"name": "Ana", "say": ["anita", 3]},
        {"name": 3},
        {"name": "Ana", "chat": None},
        {"name": "Ana", "phone": 34600111222},
        {"name": "Ana", "face": ["ana"]},
    ],
)
def test_person_from_json_rejects_wrong_types(raw: object) -> None:
    """Anything but an object of text fields and a list of text is refused."""
    with pytest.raises(PersonError):
        person_from_json(raw)


def test_phone_with_no_digits_is_refused_not_erased() -> None:
    """A phone the user typed but that holds no number must be an error, not quietly removed."""
    with pytest.raises(PersonError):
        validate([person_from_json({"name": "Ana", "phone": "call me"})])


# validate --------------------------------------------------------------------------------------------


def test_validate_accepts_a_normal_household() -> None:
    """Valid people pass without error."""
    validate(
        [
            Person(name="Ana", say=["anita"], chat=GROUP, phone="+34600111222"),
            Person(name="Ben", phone="600111222"),
            Person(name="x" * MAX_NAME_CHARS, say=["s"] * MAX_SPOKEN_NAMES),
        ]
    )
    validate([])


@pytest.mark.parametrize(
    "people",
    [
        [Person(name="Ana"), Person(name="ANA")],
        [Person(name="Lucía"), Person(name="lucia")],
        [Person(name="Tío  Sergio"), Person(name="tio sergio!")],
    ],
)
def test_validate_rejects_duplicate_names_after_normalizing(people: list[Person]) -> None:
    """Two cards that Reachy would hear as the same name are refused."""
    with pytest.raises(PersonError, match="twice"):
        validate(people)


@pytest.mark.parametrize(
    "person",
    [
        Person(name=""),
        Person(name="!!!"),
        Person(name="x" * (MAX_NAME_CHARS + 1)),
        Person(name="Ana", say=["y" * (MAX_NAME_CHARS + 1)]),
        Person(name="Ana\nBen"),
        Person(name="Ana", say=["an\x00ita"]),
        Person(name="Ana\x7f"),
        Person(name="Ana", say=["s"] * (MAX_SPOKEN_NAMES + 1)),
        Person(name="Ana", chat="34600111222@s.whatsapp.net"),
        Person(name="Ana", chat="123@g.us\n"),
        Person(name="Ana", chat="123@g.us\x00@g.us"),
        Person(name="Ana", phone="+"),
        Person(name="Ana", phone="12345"),
        Person(name="Ana", phone="+34-600-abc"),
        Person(name="Ana", phone="1" * 30),
    ],
)
def test_validate_rejects_bad_people(person: Person) -> None:
    """Empty, too long or control-character names, a non-group chat, a bad phone and too many names are refused."""
    with pytest.raises(PersonError):
        validate([person])


def test_validate_limits_the_number_of_people() -> None:
    """At most MAX_PEOPLE cards."""
    validate([Person(name=f"p{i}") for i in range(MAX_PEOPLE)])
    with pytest.raises(PersonError, match=str(MAX_PEOPLE)):
        validate([Person(name=f"p{i}") for i in range(MAX_PEOPLE + 1)])


# shared_spoken_names ---------------------------------------------------------------------------------


def test_shared_spoken_names_lists_names_more_than_one_person_answers_to() -> None:
    """A spoken name two cards share is reported with both owners; a name repeated on one card is not."""
    people = [
        Person(name="Abuela Rosa", say=["abuela", "Abuela"]),
        Person(name="Abuela Carmen", say=["Abuéla"]),
        Person(name="Ana", say=["ana"]),
        Person(name="Ben", say=["Ana"]),
    ]
    assert shared_spoken_names(people) == [
        {"said": "abuela", "people": ["Abuela Rosa", "Abuela Carmen"]},
        {"said": "ana", "people": ["Ana", "Ben"]},
    ]
    assert shared_spoken_names([Person(name="Ana", say=["anita"])]) == []


def test_faces_nobody_is_linked_to_become_their_own_cards(files: tuple[PeopleFiles, Path, Path]) -> None:
    """A face with no card (Enzo) is a card of its own; a face linked by name (Eduardo) is not repeated."""
    people_files, _, calls = files
    _write(calls, {"approved": [{"name": "Eduardo", "phone": "+351910000001"}]})
    people, _ = people_files.load({"eduardo": "Eduardo", "enzo": "Enzo"})
    assert [(p.name, p.face, p.phone) for p in people] == [
        ("Eduardo", "eduardo", "+351910000001"),
        ("Enzo", "enzo", ""),
    ]


def test_people_with_neither_group_nor_phone_are_kept_aside(files: tuple[PeopleFiles, Path, Path]) -> None:
    """Someone with only a face and spoken names survives a save, outside the app's own contact list."""
    people_files, contacts, _ = files
    people_files.save([Person(name="Enzo", say=["enzito"], face="enzo")], {"contacts": "", "calls": ""})
    document, _ = read_json(contacts)
    assert document["contacts"] == [] and document["others"] == [{"name": "Enzo", "say": ["enzito"], "face": "enzo"}]
    assert load_contacts(contacts, normalize) == []
    people, _ = people_files.load({"enzo": "Enzo"})
    assert [p.as_json() for p in people] == [
        {"name": "Enzo", "say": ["enzito"], "chat": "", "phone": "", "face": "enzo"}
    ]


def test_a_face_linked_to_another_name_is_not_its_own_card(files: tuple[PeopleFiles, Path, Path]) -> None:
    """The Martin face linked to Abuelo Martín shows only on his card."""
    people_files, _, calls = files
    _write(calls, {"approved": [{"name": "Abuelo Martín", "phone": "+584140000003", "face": "martin"}]})
    people, _ = people_files.load({"martin": "Martin"})
    assert [(p.name, p.face) for p in people] == [("Abuelo Martín", "martin")]
