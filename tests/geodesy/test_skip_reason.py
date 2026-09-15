"""A CRS the build left out must say so, rather than looking like a typo.

PROJ answers "unrecognized format / unknown name" for both a misspelled CRS and
one the build deliberately did not write, which are very different problems. The
build records the second case; these tests check it reaches the caller.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from geodetic_engine.geodesy import database

SKIPPED_NAME = "ST_Example_LambertZoneI_T8094"
SKIPPED_REASON = (
    "step 1 of EPSG:8094 applies 'Longitude rotation', which is not a plain "
    "Helmert and so cannot be composed into a single step"
)


def _database_with_history(path: Path, skipped: list[dict[str, str]]) -> None:
    with sqlite3.connect(path) as connection:
        connection.execute(
            "CREATE TABLE geodetic_engine_build_history "
            "(sequence INTEGER PRIMARY KEY, report TEXT NOT NULL)"
        )
        connection.execute(
            "INSERT INTO geodetic_engine_build_history (report) VALUES (?)",
            (json.dumps({"skipped": skipped}),),
        )


@pytest.fixture
def identity(tmp_path: Path) -> database.DatabaseIdentity:
    """A database identity pointing at a throwaway proj.db with a build history."""
    path = tmp_path / "proj.db"
    _database_with_history(
        path,
        [
            {
                "table": "geodetic_crs",
                "auth_name": "Example",
                "code": "2100468",
                "name": SKIPPED_NAME,
                "reason": SKIPPED_REASON,
            }
        ],
    )
    database._skips.cache_clear()
    stat = path.stat()
    return ((str(path), stat.st_dev, stat.st_ino, stat.st_size, 0, 0),)


def test_a_skipped_crs_is_explained_by_name(
    identity: database.DatabaseIdentity,
) -> None:
    explanation = database._skips(identity)[SKIPPED_NAME.casefold()]

    assert "Example:2100468" in explanation
    assert SKIPPED_REASON in explanation


def test_a_skipped_crs_is_explained_by_code(
    identity: database.DatabaseIdentity,
) -> None:
    assert "example:2100468" in database._skips(identity)


def test_lookup_is_case_and_whitespace_insensitive(
    identity: database.DatabaseIdentity,
) -> None:
    skips = database._skips(identity)

    assert f"  {SKIPPED_NAME.upper()}  ".strip().casefold() in skips


def test_a_database_without_a_build_history_explains_nothing(
    tmp_path: Path,
) -> None:
    """Stock proj.db has no history, and that must not stop a CRS resolving."""
    path = tmp_path / "proj.db"
    sqlite3.connect(path).close()
    database._skips.cache_clear()
    stat = path.stat()

    assert (
        database._skips(((str(path), stat.st_dev, stat.st_ino, stat.st_size, 0, 0),))
        == {}
    )


def test_an_unreadable_history_explains_nothing(tmp_path: Path) -> None:
    path = tmp_path / "proj.db"
    with sqlite3.connect(path) as connection:
        connection.execute(
            "CREATE TABLE geodetic_engine_build_history "
            "(sequence INTEGER PRIMARY KEY, report TEXT NOT NULL)"
        )
        connection.execute(
            "INSERT INTO geodetic_engine_build_history (report) VALUES ('not json')"
        )
    database._skips.cache_clear()
    stat = path.stat()

    assert (
        database._skips(((str(path), stat.st_dev, stat.st_ino, stat.st_size, 0, 0),))
        == {}
    )


def test_an_entry_without_a_reason_is_ignored(tmp_path: Path) -> None:
    path = tmp_path / "proj.db"
    _database_with_history(path, [{"auth_name": "Example", "code": "1", "name": "X"}])
    database._skips.cache_clear()
    stat = path.stat()

    assert (
        database._skips(((str(path), stat.st_dev, stat.st_ino, stat.st_size, 0, 0),))
        == {}
    )
