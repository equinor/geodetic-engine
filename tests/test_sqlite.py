"""Opening a database read-only, whatever characters its path holds."""

from __future__ import annotations

import os
import sqlite3
from contextlib import closing
from pathlib import Path

import pytest

from geodetic_engine._sqlite import read_only

_POSIX_ONLY = pytest.mark.skipif(os.name == "nt", reason="not a Windows file name")
_WINDOWS_ONLY = pytest.mark.skipif(os.name != "nt", reason="a Windows path")


def _database(directory: Path) -> Path:
    """A database in a new directory, holding the directory's name."""
    directory.mkdir()
    database = directory / "proj.db"
    with closing(sqlite3.connect(database)) as connection, connection:
        connection.execute("CREATE TABLE named (name TEXT)")
        connection.execute("INSERT INTO named VALUES (?)", (directory.name,))
    return database


def _assert_read_only(path: str | Path, name: str) -> None:
    with closing(read_only(path)) as connection:
        assert connection.execute("SELECT name FROM named").fetchone() == (name,)
        with pytest.raises(sqlite3.OperationalError, match="readonly"):
            connection.execute("DELETE FROM named")


@pytest.mark.parametrize(
    "name",
    [
        "#",
        "%41",
        "%zz",
        "%",
        "&mode=rw",
        " ",
        "é",
        pytest.param("?mode=rwc", marks=_POSIX_ONLY),
        pytest.param("file:", marks=_POSIX_ONLY),
    ],
)
def test_the_named_file_is_opened_for_reading_only(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, name: str
) -> None:
    database = _database(tmp_path / f"a{name}b")
    monkeypatch.chdir(tmp_path)
    for path in (database, database.relative_to(tmp_path)):
        _assert_read_only(path, f"a{name}b")
    assert [entry.name for entry in tmp_path.iterdir()] == [f"a{name}b"]


@_POSIX_ONLY
def test_a_path_starting_with_two_slashes_is_not_read_as_an_authority(
    tmp_path: Path,
) -> None:
    database = _database(tmp_path / "a#b")
    _assert_read_only(f"/{database}", "a#b")


@_WINDOWS_ONLY
def test_an_extended_length_path_keeps_its_question_mark(tmp_path: Path) -> None:
    database = _database(tmp_path / "a#b")
    _assert_read_only(f"\\\\?\\{database}", "a#b")


@_WINDOWS_ONLY
def test_a_unc_path_is_opened_with_either_separator(tmp_path: Path) -> None:
    """With "/", the path starts with "//", which is not a URI authority."""
    database = _database(tmp_path / "a#b")
    drive, rest = os.path.splitdrive(database)
    share = f"\\\\localhost\\{drive[0]}${rest}"
    if not os.path.exists(share):
        pytest.skip(f"no administrative share reaches {database}")
    for path in (share, share.replace("\\", "/")):
        _assert_read_only(path, "a#b")


def test_a_missing_database_is_an_error_and_is_not_created(tmp_path: Path) -> None:
    missing = tmp_path / "a#b.db"
    with pytest.raises(sqlite3.OperationalError, match="unable to open"):
        read_only(missing)
    assert not any(tmp_path.iterdir())
