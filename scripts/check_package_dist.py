"""Verify that a release carries the official OSDU catalogue and its database.

    python scripts/check_package_dist.py dist/*.whl [dist/*.tar.gz]

The wheel must hold the catalogue with matching provenance, and the proj.db
built from it, alone in its directory, built from the pinned pyproj wheel's
own database for the pinned PROJ release. An sdist given as well must carry
the same database.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sqlite3
import tarfile
import tempfile
import zipfile
from contextlib import closing
from pathlib import Path
from typing import Any

_DATA = "geodetic_engine/osdudb/_data/"
_CATALOG = _DATA + "CRS_CT_osdu_official_crs_catalog.json"
_PROVENANCE = _DATA + "CRS_CT_osdu_official_crs_catalog.source.json"
_DATABASE_DIRECTORY = _DATA + "proj_data/"
_DATABASE = _DATABASE_DIRECTORY + "proj.db"
_ENVIRONMENT = "geodetic_engine/_environment.py"
_REQUIRED = (
    _ENVIRONMENT,
    "geodetic_engine/_bundled_database.py",
    "geodetic_engine/osdudb/__main__.py",
    "geodetic_engine/osdudb/official.py",
    _CATALOG,
    _PROVENANCE,
    _DATABASE,
)
# The proj.db layout of each PROJ release this project has pinned. PROJ cannot
# open a database of another layout, so add the new release's when the pin
# moves.
_DATABASE_LAYOUT = {"9.9.0": "1.7"}
# What a build of the official catalogue records its source_version against.
_OFFICIAL_SOURCE = "osdu/data/data-definitions"
_HISTORY_TABLE = "geodetic_engine_build_history"
_IDENTIFIER_TABLE = "geodetic_engine_osdu_object_identifier"
_REFERENCE_TABLE = "geodetic_engine_osdu_persistable_reference"
_PINNED_PROJ = re.compile(r'^PINNED_PROJ_VERSION: Final = "([^"]+)"$', re.MULTILINE)
# Where the pinned pyproj wheel keeps its own database.
_WHEEL_BASE = re.compile(r"pyproj[\\/]proj_dir[\\/]share[\\/]proj[\\/]proj\.db$")


def pinned_proj_version(environment: str) -> str:
    """The PROJ release ``geodetic_engine/_environment.py`` pins."""
    match = _PINNED_PROJ.search(environment)
    if match is None:
        raise ValueError(f"{_ENVIRONMENT} pins no PROJ version")
    return match.group(1)


def official_source_version(provenance: dict[str, Any]) -> str:
    """The source_version a build of the official catalogue records."""
    commit = provenance.get("commit")
    return f"{_OFFICIAL_SOURCE}@{commit or 'sha256:' + str(provenance['sha256'])}"


def check_database(
    database: Path, *, provenance: dict[str, Any], proj_version: str
) -> dict[str, Any]:
    """Check a bundled OSDU proj.db, opened read-only as PROJ opens it.

    Returns:
        A summary of what it holds.

    Raises:
        ValueError: If it is not the official catalogue built from the pinned
            PROJ release's own database, or cannot be read where installed.
    """
    try:
        with closing(
            sqlite3.connect(f"{database.resolve().as_uri()}?mode=ro", uri=True)
        ) as connection:
            return _check_contents(
                connection, provenance=provenance, proj_version=proj_version
            )
    except sqlite3.Error as error:
        raise ValueError(f"the bundled proj.db cannot be read: {error}") from error


def _check_contents(
    connection: sqlite3.Connection, *, provenance: dict[str, Any], proj_version: str
) -> dict[str, Any]:
    # A WAL database needs a writable directory even to be read, and
    # site-packages often is not one.
    (journal,) = connection.execute("PRAGMA journal_mode").fetchone()
    if str(journal).lower() == "wal":
        raise ValueError("the bundled proj.db is in WAL mode")
    if connection.execute("PRAGMA quick_check").fetchone() != ("ok",):
        raise ValueError("the bundled proj.db fails SQLite's integrity check")

    metadata = dict(connection.execute("SELECT key, value FROM metadata"))
    if metadata.get("PROJ.VERSION") != proj_version:
        raise ValueError(
            f"the bundled proj.db is for PROJ {metadata.get('PROJ.VERSION')}, "
            f"not the pinned {proj_version}"
        )
    layout = (
        f"{metadata.get('DATABASE.LAYOUT.VERSION.MAJOR')}."
        f"{metadata.get('DATABASE.LAYOUT.VERSION.MINOR')}"
    )
    if proj_version not in _DATABASE_LAYOUT:
        raise ValueError(f"no database layout is known for PROJ {proj_version}")
    if layout != _DATABASE_LAYOUT[proj_version]:
        raise ValueError(
            f"the bundled proj.db has layout {layout}, not PROJ {proj_version}'s "
            f"{_DATABASE_LAYOUT[proj_version]}"
        )

    tables = {
        name
        for (name,) in connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'"
        )
    }
    for table in (_HISTORY_TABLE, _IDENTIFIER_TABLE, _REFERENCE_TABLE):
        if table not in tables:
            raise ValueError(f"the bundled proj.db has no {table} table")
    counts = {
        table: connection.execute(
            f"SELECT count(*) FROM {table} WHERE auth_name = 'OSDU'"
        ).fetchone()[0]
        for table in ("geodetic_crs", "projected_crs")
    }
    for table in (_IDENTIFIER_TABLE, _REFERENCE_TABLE):
        counts[table] = connection.execute(
            f"SELECT count(*) FROM {table} WHERE scheme = 'OSDU'"
        ).fetchone()[0]
    empty = sorted(table for table, count in counts.items() if not count)
    if empty:
        raise ValueError(f"the bundled proj.db has no OSDU rows in {', '.join(empty)}")

    reports = [
        json.loads(report)
        for (report,) in connection.execute(
            f"SELECT report FROM {_HISTORY_TABLE} ORDER BY sequence"
        )
    ]
    if len(reports) != 1:
        raise ValueError(
            f"the bundled proj.db records {len(reports)} builds, not one fresh build"
        )
    report = reports[0]
    expected = official_source_version(provenance)
    if report.get("source_version") != expected:
        raise ValueError(
            f"the bundled proj.db was built from {report.get('source_version')}, "
            f"not the bundled catalogue {expected}"
        )
    if report.get("authorities") != ["OSDU"]:
        raise ValueError(
            f"the bundled proj.db imports {report.get('authorities')}, not OSDU only"
        )
    if (report.get("proj_version"), report.get("database_layout_version")) != (
        proj_version,
        layout,
    ):
        raise ValueError("the bundled proj.db's build report names another PROJ")
    if not _WHEEL_BASE.search(str(report.get("base_proj_db"))):
        raise ValueError(
            f"the bundled proj.db was built on {report.get('base_proj_db')}, not "
            "the pinned pyproj wheel's own proj.db"
        )
    return {
        "proj_version": proj_version,
        "layout": layout,
        "source_version": expected,
        "osdu_counts": counts,
    }


def check_wheel(path: Path) -> dict[str, Any]:
    """Reject missing, duplicated, or mismatched release resources.

    Returns:
        A summary of the bundled database, with its SHA-256 and size.
    """
    with zipfile.ZipFile(path) as wheel:
        names = wheel.namelist()
        for name in _REQUIRED:
            if names.count(name) != 1:
                raise ValueError(f"{name} must appear exactly once")
        beside = sorted(
            name
            for name in names
            if name.startswith(_DATABASE_DIRECTORY) and name != _DATABASE
        )
        if beside:
            raise ValueError(
                f"{_DATABASE_DIRECTORY} must hold proj.db alone, not also {beside}"
            )
        for name in ("LICENSE", "NOTICE"):
            matches = [
                entry
                for entry in names
                if entry.endswith((f".dist-info/licenses/{name}", f".dist-info/{name}"))
            ]
            if len(matches) != 1:
                raise ValueError(f"the wheel must include exactly one {name}")
        catalog = wheel.read(_CATALOG)
        metadata = json.loads(wheel.read(_PROVENANCE))
        proj_version = pinned_proj_version(wheel.read(_ENVIRONMENT).decode("utf-8"))
        database = wheel.read(_DATABASE)
    digest = hashlib.sha256(catalog).hexdigest()
    if not isinstance(metadata, dict) or metadata.get("sha256") != digest:
        raise ValueError("the bundled OSDU catalogue does not match its provenance")
    with tempfile.TemporaryDirectory() as directory:
        extracted = Path(directory) / "proj.db"
        extracted.write_bytes(database)
        # As read-only as an installation in a system site-packages.
        extracted.chmod(0o444)
        try:
            summary = check_database(
                extracted, provenance=metadata, proj_version=proj_version
            )
        finally:
            extracted.chmod(0o644)
    return summary | {
        "sha256": hashlib.sha256(database).hexdigest(),
        "size": len(database),
    }


def check_sdist(path: Path, database_sha256: str) -> None:
    """Reject an sdist that lacks the wheel's bundled database."""
    with tarfile.open(path) as sdist:
        members = [
            member
            for member in sdist.getmembers()
            if member.name.partition("/")[2] == _DATABASE
        ]
        if len(members) != 1:
            raise ValueError(f"{_DATABASE} must appear exactly once")
        stream = sdist.extractfile(members[0])
        if stream is None:
            raise ValueError(f"{_DATABASE} is not a file")
        with stream:
            digest = hashlib.file_digest(stream, "sha256").hexdigest()
    if digest != database_sha256:
        raise ValueError(f"{_DATABASE} differs from the wheel's")


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__.splitlines()[0] if __doc__ else None
    )
    parser.add_argument("wheel", type=Path, help="the built project wheel")
    parser.add_argument("sdist", type=Path, nargs="?", help="the built sdist")
    args = parser.parse_args()
    try:
        summary = check_wheel(args.wheel)
    except (OSError, ValueError, KeyError, zipfile.BadZipFile) as error:
        raise SystemExit(f"{args.wheel}: {error}") from error
    counts = summary["osdu_counts"]
    print(
        f"{args.wheel}: bundled OSDU catalogue and database verified: "
        f"{os.path.getsize(args.wheel) / 1e6:.1f} MB wheel, "
        f"proj.db {summary['size'] / 1e6:.1f} MB for PROJ "
        f"{summary['proj_version']} (layout {summary['layout']}), "
        f"{summary['source_version']}, {counts['geodetic_crs']} geodetic and "
        f"{counts['projected_crs']} projected OSDU CRSs, "
        f"{counts[_IDENTIFIER_TABLE]} OSDU ids, "
        f"{counts[_REFERENCE_TABLE]} persistable references"
    )
    if args.sdist is not None:
        try:
            check_sdist(args.sdist, summary["sha256"])
        except (OSError, ValueError, tarfile.TarError) as error:
            raise SystemExit(f"{args.sdist}: {error}") from error
        print(f"{args.sdist}: carries the same proj.db")


if __name__ == "__main__":
    main()
