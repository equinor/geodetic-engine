"""Checks on resources required in the published project wheel."""

import hashlib
import io
import json
import re
import sqlite3
import tarfile
import zipfile
from contextlib import closing
from pathlib import Path
from typing import Any

import pyproj
import pytest

from scripts.check_package_dist import (
    _CATALOG,
    _DATABASE,
    _DATABASE_DIRECTORY,
    _PINS,
    _PROVENANCE,
    _REQUIRED,
    check_sdist,
    check_wheel,
    installed_proj_db,
)
from tests.support import installed_proj_db as stock_database

CATALOG = b"official catalogue"
COMMIT = "e173a56b7e4e7957e402bf14c274b84fcd19352b"
PROVENANCE = {"sha256": hashlib.sha256(CATALOG).hexdigest(), "commit": COMMIT}
WHEEL_BASE = "/venv/lib/python3.13/site-packages/pyproj/proj_dir/share/proj/proj.db"
# What the pinned PROJ's own proj.db records, as far as these tests need.
METADATA = {
    "PROJ.VERSION": "9.9.0",
    "DATABASE.LAYOUT.VERSION.MAJOR": "1",
    "DATABASE.LAYOUT.VERSION.MINOR": "7",
    "EPSG.VERSION": "v13.102",
}


def _pins(proj_version: str) -> bytes:
    """The geodetic_engine/_pins.env a wheel pinning a PROJ release ships."""
    return (
        f"PROJ_VERSION={proj_version}\n"
        f"PROJ_SHA256={'0' * 64}\n"
        "PROJ_DATA_VERSION=1.25\n"
        f"PROJ_DATA_SHA256={'0' * 64}\n"
        "PYPROJ_VERSION=3.8.0\n"
        "PYPROJ_WHEEL_INDEX=https://example.org/simple/\n"
    ).encode()


def _metadata_table(connection: sqlite3.Connection, metadata: dict[str, str]) -> None:
    connection.execute("CREATE TABLE metadata (key TEXT, value TEXT)")
    connection.executemany("INSERT INTO metadata VALUES (?, ?)", metadata.items())


def _reference(path: Path, metadata: dict[str, str] = METADATA) -> Path:
    """A stand-in for the pinned PROJ release's own proj.db."""
    with closing(sqlite3.connect(path)) as connection, connection:
        _metadata_table(connection, metadata)
    return path


def _database(path: Path, **changes: Any) -> bytes:
    """A proj.db holding what a build of the official catalogue writes."""
    metadata = changes.get("metadata", METADATA)
    settings: dict[str, Any] = {
        "metadata": metadata,
        "osdu_crs": True,
        "reference_table": True,
        "builds": 1,
        "journal": "delete",
        "report": {
            "source_version": f"osdu/data/data-definitions@{COMMIT}",
            "authorities": ["OSDU"],
            "proj_version": metadata["PROJ.VERSION"],
            "database_layout_version": (
                f"{metadata['DATABASE.LAYOUT.VERSION.MAJOR']}."
                f"{metadata['DATABASE.LAYOUT.VERSION.MINOR']}"
            ),
            "base_proj_db": WHEEL_BASE,
        },
    } | changes
    with closing(sqlite3.connect(path)) as connection, connection:
        connection.execute(f"PRAGMA journal_mode = {settings['journal']}")
        _metadata_table(connection, settings["metadata"])
        for table in ("geodetic_crs", "projected_crs"):
            connection.execute(f"CREATE TABLE {table} (auth_name TEXT, code TEXT)")
            connection.execute(
                f"INSERT INTO {table} VALUES (?, '1')",
                ("OSDU" if settings["osdu_crs"] else "EPSG",),
            )
        tables = ["geodetic_engine_osdu_object_identifier"]
        if settings["reference_table"]:
            tables.append("geodetic_engine_osdu_persistable_reference")
        for table in tables:
            connection.execute(f"CREATE TABLE {table} (scheme TEXT)")
            connection.execute(f"INSERT INTO {table} VALUES ('OSDU')")
        connection.execute(
            "CREATE TABLE geodetic_engine_build_history "
            "(sequence INTEGER PRIMARY KEY, report TEXT)"
        )
        for _ in range(settings["builds"]):
            connection.execute(
                "INSERT INTO geodetic_engine_build_history (report) VALUES (?)",
                (json.dumps(settings["report"]),),
            )
    return path.read_bytes()


def _wheel(
    path: Path,
    *,
    catalog: bytes = CATALOG,
    metadata: dict[str, Any] | None = None,
    database: bytes = b"",
    missing: str = "",
    extra: tuple[str, ...] = (),
    legacy_license_paths: bool = False,
    pinned: str = "9.9.0",
    pins: bytes | None = None,
) -> Path:
    contents = {
        _CATALOG: catalog,
        _PROVENANCE: json.dumps(PROVENANCE if metadata is None else metadata).encode(),
        _PINS: _pins(pinned) if pins is None else pins,
        _DATABASE: database,
    }
    with zipfile.ZipFile(path, "w") as archive:
        for name in (*_REQUIRED, *extra):
            if name != missing:
                archive.writestr(name, contents.get(name, b""))
        for name in ("LICENSE", "NOTICE"):
            if name != missing:
                directory = "pygeodetic_engine-0.1.0.dist-info"
                if not legacy_license_paths:
                    directory += "/licenses"
                archive.writestr(f"{directory}/{name}", "")
    return path


@pytest.fixture
def database(tmp_path: Path) -> bytes:
    return _database(tmp_path / "proj.db")


@pytest.fixture
def reference(tmp_path: Path) -> Path:
    return _reference(tmp_path / "reference.db")


@pytest.mark.parametrize("legacy_license_paths", [False, True])
def test_release_wheel_contains_the_verified_catalogue_and_database(
    tmp_path: Path, database: bytes, reference: Path, legacy_license_paths: bool
) -> None:
    wheel = _wheel(
        tmp_path / "package.whl",
        database=database,
        legacy_license_paths=legacy_license_paths,
    )
    summary = check_wheel(wheel, reference)
    assert summary["source_version"] == f"osdu/data/data-definitions@{COMMIT}"
    assert summary["sha256"] == hashlib.sha256(database).hexdigest()


def test_a_new_proj_release_needs_no_change_to_the_check(tmp_path: Path) -> None:
    """What a release is checked against comes from the pins and PROJ itself."""
    metadata = METADATA | {
        "PROJ.VERSION": "9.9.1",
        "DATABASE.LAYOUT.VERSION.MINOR": "8",
        "EPSG.VERSION": "v13.200",
    }
    wheel = _wheel(
        tmp_path / "package.whl",
        database=_database(tmp_path / "proj.db", metadata=metadata),
        pinned="9.9.1",
    )
    summary = check_wheel(wheel, _reference(tmp_path / "reference.db", metadata))
    assert (summary["proj_version"], summary["layout"]) == ("9.9.1", "1.8")


def test_the_reference_must_be_the_pinned_releases_database(
    tmp_path: Path, database: bytes
) -> None:
    wheel = _wheel(tmp_path / "package.whl", database=database)
    reference = _reference(
        tmp_path / "reference.db", METADATA | {"PROJ.VERSION": "9.8.1"}
    )
    with pytest.raises(
        ValueError, match=re.escape("is for PROJ 9.8.1, not the pinned 9.9.0")
    ):
        check_wheel(wheel, reference)


def test_release_wheel_rejects_malformed_pins(
    tmp_path: Path, database: bytes, reference: Path
) -> None:
    wheel = _wheel(
        tmp_path / "package.whl", database=database, pins=b"PROJ_VERSION=9.9\n"
    )
    with pytest.raises(ValueError, match=re.escape("PROJ_VERSION='9.9' is malformed")):
        check_wheel(wheel, reference)


def test_the_reference_defaults_to_the_installed_pyprojs_own_database() -> None:
    found = installed_proj_db(pyproj.proj_version_str)
    assert found == stock_database().resolve()
    with pytest.raises(ValueError, match=re.escape("not the pinned 0.0.0")):
        installed_proj_db("0.0.0")


@pytest.mark.parametrize("missing", [_CATALOG, _DATABASE])
def test_release_wheel_rejects_a_missing_resource(
    tmp_path: Path, database: bytes, reference: Path, missing: str
) -> None:
    wheel = _wheel(tmp_path / "package.whl", database=database, missing=missing)
    with pytest.raises(ValueError, match="exactly once"):
        check_wheel(wheel, reference)


def test_release_wheel_rejects_modified_catalogue(
    tmp_path: Path, database: bytes, reference: Path
) -> None:
    wheel = _wheel(
        tmp_path / "package.whl",
        catalog=b"changed",
        metadata={"sha256": "0"},
        database=database,
    )
    with pytest.raises(ValueError, match="does not match"):
        check_wheel(wheel, reference)


def test_release_wheel_rejects_missing_attribution(
    tmp_path: Path, database: bytes, reference: Path
) -> None:
    wheel = _wheel(tmp_path / "package.whl", database=database, missing="NOTICE")
    with pytest.raises(ValueError, match="NOTICE"):
        check_wheel(wheel, reference)


def test_release_wheel_keeps_the_database_alone_in_its_directory(
    tmp_path: Path, database: bytes, reference: Path
) -> None:
    """PROJ reads the proj.db of every directory on its search path."""
    wheel = _wheel(
        tmp_path / "package.whl",
        database=database,
        extra=(_DATABASE_DIRECTORY + "us_noaa_conus.tif",),
    )
    with pytest.raises(ValueError, match="alone"):
        check_wheel(wheel, reference)


@pytest.mark.parametrize(
    ("changes", "message"),
    [
        (
            {"metadata": METADATA | {"PROJ.VERSION": "9.8.1"}},
            "PROJ.VERSION='9.8.1', not '9.9.0'",
        ),
        (
            {"metadata": METADATA | {"DATABASE.LAYOUT.VERSION.MINOR": "6"}},
            "DATABASE.LAYOUT.VERSION.MINOR='6', not '7'",
        ),
        (
            {"metadata": METADATA | {"EPSG.VERSION": "v12.029"}},
            "EPSG.VERSION='v12.029', not 'v13.102'",
        ),
        ({"osdu_crs": False}, "no OSDU rows in geodetic_crs, projected_crs"),
        ({"reference_table": False}, "no geodetic_engine_osdu_persistable_reference"),
        ({"builds": 2}, "records 2 builds"),
        (
            {
                "report": {
                    "source_version": "osdu/data/data-definitions@0000",
                    "authorities": ["OSDU"],
                }
            },
            "not the bundled catalogue",
        ),
        (
            {
                "report": {
                    "source_version": f"osdu/data/data-definitions@{COMMIT}",
                    "authorities": ["OSDU"],
                    "proj_version": "9.9.0",
                    "database_layout_version": "1.7",
                    "base_proj_db": "/usr/local/share/proj/proj.db",
                }
            },
            "pinned pyproj wheel's own proj.db",
        ),
        ({"journal": "wal"}, "WAL|cannot be read"),
    ],
)
def test_release_wheel_rejects_a_database_not_built_from_the_pins(
    tmp_path: Path, reference: Path, changes: dict[str, Any], message: str
) -> None:
    database = _database(tmp_path / "proj.db", **changes)
    wheel = _wheel(tmp_path / "package.whl", database=database)
    with pytest.raises(ValueError, match=message):
        check_wheel(wheel, reference)


def test_release_wheel_rejects_a_catalogue_without_commit_built_otherwise(
    tmp_path: Path, database: bytes, reference: Path
) -> None:
    """Without a commit, the build records the catalogue's SHA-256 instead."""
    wheel = _wheel(
        tmp_path / "package.whl",
        metadata={"sha256": PROVENANCE["sha256"]},
        database=database,
    )
    with pytest.raises(ValueError, match="@sha256:"):
        check_wheel(wheel, reference)


def _sdist(path: Path, database: bytes | None) -> Path:
    with tarfile.open(path, "w:gz") as archive:
        if database is not None:
            member = tarfile.TarInfo(f"pygeodetic_engine-0.1.0/{_DATABASE}")
            member.size = len(database)
            archive.addfile(member, io.BytesIO(database))
    return path


def test_sdist_carries_the_wheels_database(tmp_path: Path, database: bytes) -> None:
    digest = hashlib.sha256(database).hexdigest()
    check_sdist(_sdist(tmp_path / "package.tar.gz", database), digest)
    with pytest.raises(ValueError, match="exactly once"):
        check_sdist(_sdist(tmp_path / "missing.tar.gz", None), digest)
    with pytest.raises(ValueError, match="differs"):
        check_sdist(_sdist(tmp_path / "other.tar.gz", b"other"), digest)
