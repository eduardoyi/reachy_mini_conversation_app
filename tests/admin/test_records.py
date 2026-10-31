"""Versioned JSON reads and atomic owner-only writes for the files the admin app edits."""

import os
import sys
import json
import stat
from pathlib import Path

import pytest

from reachy_mini_conversation_app.admin.records import (
    BACKUPS_KEPT,
    RecordError,
    StaleVersion,
    read_json,
    write_json,
    write_atomic,
)


posix_only = pytest.mark.skipif(sys.platform == "win32", reason="POSIX file modes")


def _mode(path: Path) -> int:
    return stat.S_IMODE(path.stat().st_mode)


def test_missing_file_reads_as_empty_with_no_version(tmp_path: Path) -> None:
    """A file that does not exist yet is an empty document at version ""."""
    assert read_json(tmp_path / "nope.json") == ({}, "")


def test_read_returns_document_and_a_version_that_tracks_content(tmp_path: Path) -> None:
    """The version is stable for the same bytes and changes when the file changes."""
    path = tmp_path / "people.json"
    path.write_text('{"a": 1}')
    document, version = read_json(path)
    assert document == {"a": 1} and version
    assert read_json(path)[1] == version
    path.write_text('{"a": 2}')
    assert read_json(path)[1] != version


@pytest.mark.parametrize("raw", ["{not json", "[1, 2]", '"text"', "3"])
def test_invalid_json_or_non_object_is_a_record_error(tmp_path: Path, raw: str) -> None:
    """Only a JSON object is a record; anything else is refused rather than overwritten."""
    path = tmp_path / "bad.json"
    path.write_text(raw)
    with pytest.raises(RecordError):
        read_json(path)


def test_unreadable_path_is_a_record_error(tmp_path: Path) -> None:
    """A folder where the file should be cannot be read."""
    with pytest.raises(RecordError):
        read_json(tmp_path)


def test_write_creates_a_missing_file_based_on_no_version(tmp_path: Path) -> None:
    """A new file is written when the write is based on "" and its version is returned."""
    path = tmp_path / "new.json"
    version = write_json(path, {"name": "Ana Lucía"}, "")
    document, read_version = read_json(path)
    assert document == {"name": "Ana Lucía"} and read_version == version
    assert "Ana Lucía" in path.read_text(encoding="utf-8")  # kept readable, not \u-escaped
    assert not list(tmp_path.glob("new.json.bak*"))


def test_write_refuses_a_stale_version_and_leaves_the_file(tmp_path: Path) -> None:
    """Two phones editing at once: the second write is told the file changed."""
    path = tmp_path / "people.json"
    first = write_json(path, {"v": 1}, "")
    write_json(path, {"v": 2}, first)
    with pytest.raises(StaleVersion):
        write_json(path, {"v": 3}, first)
    assert read_json(path)[0] == {"v": 2}


def test_write_based_on_nothing_refuses_an_existing_file(tmp_path: Path) -> None:
    """A write that thinks the file is new does not overwrite one that appeared meanwhile."""
    path = tmp_path / "people.json"
    path.write_text("{}")
    with pytest.raises(StaleVersion):
        write_json(path, {"v": 1}, "")


def test_write_to_a_file_that_vanished_is_stale(tmp_path: Path) -> None:
    """A write based on a version whose file was deleted is stale too."""
    path = tmp_path / "people.json"
    version = write_json(path, {"v": 1}, "")
    path.unlink()
    with pytest.raises(StaleVersion):
        write_json(path, {"v": 2}, version)


def test_backups_rotate_and_keep_only_the_newest(tmp_path: Path) -> None:
    """Each write keeps the previous file as .bak1, shifting older ones, up to BACKUPS_KEPT."""
    path = tmp_path / "people.json"
    version = write_json(path, {"v": 0}, "")
    for value in range(1, 6):
        version = write_json(path, {"v": value}, version)
    assert BACKUPS_KEPT == 3
    backups = sorted(p.name for p in tmp_path.glob("people.json.bak*"))
    assert backups == ["people.json.bak1", "people.json.bak2", "people.json.bak3"]
    assert [json.loads(tmp_path.joinpath(f"people.json.bak{i}").read_text())["v"] for i in (1, 2, 3)] == [4, 3, 2]
    assert read_json(path)[0] == {"v": 5}


@posix_only
def test_written_files_and_new_folder_are_owner_only(tmp_path: Path) -> None:
    """Household records are 0600 inside a 0700 folder, backups included."""
    folder = tmp_path / "private"
    path = folder / "people.json"
    version = write_json(path, {"v": 1}, "")
    write_json(path, {"v": 2}, version)
    assert _mode(folder) == 0o700
    assert _mode(path) == 0o600
    assert _mode(folder / "people.json.bak1") == 0o600


def test_write_atomic_replaces_content_and_leaves_no_temporary_files(tmp_path: Path) -> None:
    """The file is replaced in one step; nothing else is left in the folder."""
    path = tmp_path / "sub" / "file.txt"
    write_atomic(path, b"one")
    write_atomic(path, b"two")
    assert path.read_bytes() == b"two"
    assert os.listdir(path.parent) == ["file.txt"]


@posix_only
def test_write_atomic_is_owner_only(tmp_path: Path) -> None:
    """Even a file that was world-readable becomes owner-only when replaced."""
    path = tmp_path / "file.txt"
    path.write_text("old")
    os.chmod(path, 0o644)
    write_atomic(path, b"new")
    assert _mode(path) == 0o600


def test_write_atomic_failure_cleans_up_and_raises(tmp_path: Path) -> None:
    """When the replace fails the temporary file is removed and the error surfaces."""
    target = tmp_path / "taken"
    target.mkdir()
    (target / "child").write_text("x")  # a non-empty folder cannot be replaced by a file
    with pytest.raises(OSError):
        write_atomic(target, b"data")
    assert sorted(p.name for p in tmp_path.iterdir()) == ["taken"]
