"""An organisation's register is built to share, and checked before it is.

scripts/build_organisation_projdb.py builds a Georepository register and the
official OSDU catalogue into the pinned pyproj wheel's own proj.db, so that
the register's users need no credentials of their own. Of the caller's
secrets only the credentials reach the build, and only the register import; a
database not fit to share is refused; and the wheel written installs it where
geodetic_engine looks.
"""

from __future__ import annotations

import base64
import csv
import hashlib
import io
import json
import os
import re
import sqlite3
import subprocess
import sys
import venv
import zipfile
from contextlib import closing
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from geodetic_engine._bundled_database import ENTRY_POINT_GROUP
from geodetic_engine.projdb.settings import find_env_file
from scripts import build_organisation_projdb as script
from scripts import check_package_dist
from scripts.build_bundled_osdudb import clean_environment, pip_environment

METADATA = {
    "PROJ.VERSION": "9.9.0",
    "DATABASE.LAYOUT.VERSION.MAJOR": "1",
    "DATABASE.LAYOUT.VERSION.MINOR": "7",
    "EPSG.VERSION": "v13.102",
}


def _metadata_table(connection: sqlite3.Connection, metadata: dict[str, str]) -> None:
    connection.execute("CREATE TABLE metadata (key TEXT, value TEXT)")
    connection.executemany("INSERT INTO metadata VALUES (?, ?)", metadata.items())


def _reference(path: Path) -> Path:
    """A stand-in for the pinned pyproj wheel's own proj.db."""
    with closing(sqlite3.connect(path)) as connection, connection:
        _metadata_table(connection, METADATA)
    return path


def _report(authorities: list[str], base: Path, **changes: Any) -> dict[str, Any]:
    return {
        "status": "passed",
        "authorities": authorities,
        "base_proj_db": str(base),
        "source_version": "1.106",
        "built_at": "2026-11-03T04:00:00+00:00",
    } | changes


def _database(
    path: Path,
    reference: Path,
    *,
    metadata: dict[str, str] = METADATA,
    reports: list[dict[str, Any]] | None = None,
    crs: tuple[str, ...] = ("Example", "OSDU"),
    journal: str = "delete",
) -> Path:
    """A proj.db holding what the register's and the OSDU build write."""
    if reports is None:
        reports = [_report(["Example"], reference), _report(["OSDU"], reference)]
    with closing(sqlite3.connect(path)) as connection, connection:
        connection.execute(f"PRAGMA journal_mode = {journal}")
        _metadata_table(connection, metadata)
        for view in ("crs_view", "coordinate_operation_view"):
            connection.execute(f"CREATE TABLE {view} (auth_name TEXT, code TEXT)")
        connection.executemany(
            "INSERT INTO crs_view VALUES (?, '1')", [(name,) for name in crs]
        )
        connection.execute("INSERT INTO coordinate_operation_view VALUES ('OSDU', '1')")
        connection.execute(
            "CREATE TABLE geodetic_engine_build_history "
            "(sequence INTEGER PRIMARY KEY, report TEXT)"
        )
        connection.executemany(
            "INSERT INTO geodetic_engine_build_history (report) VALUES (?)",
            [(json.dumps(report),) for report in reports],
        )
    return path


@pytest.fixture
def reference(tmp_path: Path) -> Path:
    return _reference(tmp_path / "reference.db")


def test_only_the_credentials_reach_the_register_import(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Nothing else of the caller's steers the build, and the OSDU build and
    the grid patch get no credentials at all."""
    calls: list[tuple[list[str], dict[str, str]]] = []

    def run(command: list[str], *, env: dict[str, str], **_: Any) -> str:
        calls.append((command, env))
        return ""

    monkeypatch.setattr(script, "pinned_environment", lambda *_: Path("python"))
    monkeypatch.setattr(script, "run", run)
    monkeypatch.setattr(script, "own_proj_db", lambda _: tmp_path / "reference.db")
    credentials = dict.fromkeys(script.CREDENTIALS, "the caller's")
    steering = {
        "PROJ_DATA": "the caller's",
        "PROJ_AUX_DB": "the caller's",
        "GEODETIC_ENGINE_GEOREP_URL": "https://elsewhere.example",
        "GEODETIC_ENGINE_BASE_PROJ_DB": "the caller's",
        "GEODETIC_ENGINE_OSDU_DB": "1",
    }
    # Another secret, and pip's index, which may hold one: no process here
    # needs either.
    unrelated = {
        "TWINE_PASSWORD": "the caller's",
        "PIP_INDEX_URL": "https://token@mirror.example/simple",
    }
    for name, value in (credentials | steering | unrelated).items():
        monkeypatch.setenv(name, value)

    (tmp_path / ".env").write_text(
        "GEODETIC_ENGINE_OSDU_DB=1\nGEODETIC_ENGINE_CONFIG=untrusted.toml\n"
    )
    database, base = script.build(
        tmp_path, None, tmp_path / "settings.toml", credentials, grid_patch=True
    )

    monkeypatch.chdir(database.parent)
    assert find_env_file() == database.parent / ".env"
    assert (database.parent / ".env").read_text() == ""
    assert (database, base) == (
        tmp_path / "output" / "proj.db",
        tmp_path / "reference.db",
    )
    (register, register_env), (osdu, osdu_env), (patch, patch_env) = calls
    assert register[1:4] == ["-m", "geodetic_engine.projdb", "build"]
    assert "--no-cache" in register
    assert osdu[1:] == [
        "-m",
        "geodetic_engine.osdudb",
        "build",
        "--official",
        "--output",
        str(database),
        "--append",
    ]
    assert patch == ["bash", str(script.GRID_PATCH), "--db", str(database)]
    assert register_env.items() >= credentials.items()
    for env in (register_env, osdu_env, patch_env):
        assert env["GEODETIC_ENGINE_OSDU_DB"] == "0"
        assert not (steering.keys() - {"GEODETIC_ENGINE_OSDU_DB"}) & env.keys()
        assert not unrelated.keys() & env.keys()
    for env in (osdu_env, patch_env):
        assert not set(script.CREDENTIALS) & env.keys()


def test_the_grid_patch_is_left_out_when_asked(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    commands: list[list[str]] = []
    monkeypatch.setattr(script, "pinned_environment", lambda *_: Path("python"))
    monkeypatch.setattr(
        script, "run", lambda command, **_: commands.append(command) or ""
    )
    monkeypatch.setattr(script, "own_proj_db", lambda _: tmp_path / "reference.db")
    script.build(tmp_path, None, tmp_path / "settings.toml", {}, grid_patch=False)
    assert [command[2] for command in commands] == [
        "geodetic_engine.projdb",
        "geodetic_engine.osdudb",
    ]


@pytest.mark.parametrize(
    "unset", [script.CREDENTIALS, script.CREDENTIALS[1:]], ids=["both", "secret"]
)
def test_missing_credentials_are_refused(
    monkeypatch: pytest.MonkeyPatch, unset: tuple[str, ...]
) -> None:
    for name in script.CREDENTIALS:
        monkeypatch.setenv(name, "set")
    for name in unset:
        monkeypatch.delenv(name)
    with pytest.raises(SystemExit, match=" and ".join(unset)):
        script.read_credentials()


def test_a_database_fit_to_share_is_described(tmp_path: Path, reference: Path) -> None:
    summary = script.check_database(
        _database(tmp_path / "proj.db", reference), reference
    )
    assert summary["proj_version"] == "9.9.0"
    assert summary["epsg_version"] == "v13.102"
    assert summary["database_layout"] == "1.7"
    assert summary["authorities"] == {
        "Example": {"crs": 1, "operations": 0},
        "OSDU": {"crs": 1, "operations": 1},
    }
    assert [build["authorities"] for build in summary["builds"]] == [
        ["Example"],
        ["OSDU"],
    ]


@pytest.mark.parametrize(
    ("changes", "message"),
    [
        (
            {"metadata": METADATA | {"EPSG.VERSION": "v12.029"}},
            "EPSG.VERSION='v12.029', not 'v13.102'",
        ),
        ({"journal": "wal"}, "WAL mode"),
        ({"crs": ("OSDU",)}, "holds no CRS or operation of Example"),
    ],
)
def test_a_database_unfit_to_share_is_refused(
    tmp_path: Path, reference: Path, changes: dict[str, Any], message: str
) -> None:
    database = _database(tmp_path / "proj.db", reference, **changes)
    with pytest.raises(ValueError, match=re.escape(message)):
        script.check_database(database, reference)


@pytest.mark.parametrize(
    ("reports", "message"),
    [
        (lambda base: [_report(["Example"], base)], "records 1 builds"),
        (
            lambda base: [_report(["OSDU"], base), _report(["Example"], base)],
            "not a register's and then the official OSDU catalogue's",
        ),
        (
            lambda base: [
                _report(["Example"], base, status="skipped"),
                _report(["OSDU"], base),
            ],
            "the build of Example is 'skipped', not 'passed'",
        ),
        (
            lambda base: [
                _report(["Example"], base),
                _report(["OSDU"], Path("/usr/local/share/proj/proj.db")),
            ],
            "the build of OSDU started from /usr/local/share/proj/proj.db",
        ),
    ],
)
def test_only_a_validated_register_and_osdu_build_on_the_wheel_is_shared(
    tmp_path: Path, reference: Path, reports: Any, message: str
) -> None:
    database = _database(tmp_path / "proj.db", reference, reports=reports(reference))
    with pytest.raises(ValueError, match=re.escape(message)):
        script.check_database(database, reference)


def test_the_manifest_names_the_database_and_the_pyproj_it_is_for(
    tmp_path: Path, reference: Path
) -> None:
    database = _database(tmp_path / "proj.db", reference)
    summary = script.check_database(database, reference)
    pins = SimpleNamespace(pyproj_requirement="pyproj==3.8.0+proj9.9.0")
    record = script.manifest(summary, database, pins, grid_patch=True)
    assert record["sha256"] == hashlib.sha256(database.read_bytes()).hexdigest()
    assert record["pyproj_requirement"] == "pyproj==3.8.0+proj9.9.0"
    assert record["grid_alternatives_patched"] is True
    assert record["authorities"] == summary["authorities"]
    json.dumps(record)


VERSION = "9.9.0.202611030405"
INFO = f"example_projdb-{VERSION}.dist-info"


def _wheel(tmp_path: Path) -> tuple[Path, Path]:
    """A wheel of a database whose builds left reports and a log beside it."""
    built = tmp_path / "built"
    built.mkdir()
    for name in ("proj.db.report.json", "proj.db.osdudb.report.json", "proj.db.log"):
        (built / name).write_text(name)
    database = _database(built / "proj.db", _reference(tmp_path / "reference.db"))
    record = {
        "proj_version": "9.9.0",
        "authorities": {"Example": {"crs": 1, "operations": 0}, "OSDU": {}},
    }
    output = tmp_path / "dist"
    output.mkdir()
    wheel = script.write_wheel(
        output, name="example-projdb", version=VERSION, database=database, record=record
    )
    return wheel, database


def test_the_wheel_records_every_file_it_installs(tmp_path: Path) -> None:
    wheel, _ = _wheel(tmp_path)
    assert wheel.name == f"example_projdb-{VERSION}-py3-none-any.whl"
    with zipfile.ZipFile(wheel) as archive:
        contents = {name: archive.read(name) for name in archive.namelist()}
    record = contents.pop(f"{INFO}/RECORD").decode()
    rows = {
        path: (digest, size) for path, digest, size in csv.reader(io.StringIO(record))
    }
    assert rows.pop(f"{INFO}/RECORD") == ("", "")
    assert rows == {
        name: (
            "sha256="
            + base64.urlsafe_b64encode(hashlib.sha256(data).digest())
            .rstrip(b"=")
            .decode(),
            str(len(data)),
        )
        for name, data in contents.items()
    }
    # The database alone in its directory, as PROJ reads one per directory.
    assert sorted(contents) == [
        f"{INFO}/METADATA",
        f"{INFO}/WHEEL",
        f"{INFO}/entry_points.txt",
        "example_projdb/__init__.py",
        "example_projdb/proj.db.manifest.json",
        "example_projdb/proj.db.osdudb.report.json",
        "example_projdb/proj.db.report.json",
        "example_projdb/proj_data/proj.db",
    ]
    assert (
        f"Name: example-projdb\nVersion: {VERSION}\n"
        in contents[f"{INFO}/METADATA"].decode()
    )
    assert contents[f"{INFO}/entry_points.txt"].decode() == (
        f"[{ENTRY_POINT_GROUP}]\nexample-projdb = example_projdb:PROJ_DATA\n"
    )


def test_pip_installs_the_database_where_geodetic_engine_looks(
    tmp_path: Path,
) -> None:
    wheel, database = _wheel(tmp_path)
    venv.create(tmp_path / "venv", with_pip=True)
    python = (
        tmp_path / "venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    )
    target = tmp_path / "site"
    install = ["install", "--quiet", "--no-deps", "--no-index", "--target"]
    subprocess.run(
        [str(python), "-m", "pip", *install, str(target), str(wheel)],
        env=pip_environment(),
        check=True,
    )
    found = subprocess.run(
        [
            str(python),
            "-c",
            "from importlib.metadata import entry_points\n"
            f"[point] = entry_points(group={ENTRY_POINT_GROUP!r})\n"
            "print(point.dist.name, point.dist.version, point.load(), sep='\\n')",
        ],
        env=clean_environment() | {"PYTHONPATH": str(target)},
        capture_output=True,
        text=True,
        check=True,
    ).stdout.splitlines()
    assert found[:2] == ["example-projdb", VERSION]
    assert (Path(found[2]) / "proj.db").read_bytes() == database.read_bytes()


def test_the_commit_is_read_with_nothing_more_of_the_callers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """git needs neither the credentials nor any other secret of the caller's."""
    for name in (*script.CREDENTIALS, "TWINE_PASSWORD"):
        monkeypatch.setenv(name, "the caller's")
    calls: list[tuple[list[str], dict[str, str] | None]] = []

    def run(command: list[str], **options: Any) -> subprocess.CompletedProcess[str]:
        calls.append((command, options.get("env")))
        stdout = "0123abc\n" if "rev-parse" in command else ""
        return subprocess.CompletedProcess(command, 0, stdout=stdout, stderr="")

    monkeypatch.setattr(script.subprocess, "run", run)
    assert script._commit() == "0123abc"
    assert [command[3] for command, _ in calls] == ["rev-parse", "status"]
    assert all(env == clean_environment() for _, env in calls)


def test_databases_are_opened_as_geodetic_engine_opens_them() -> None:
    """Path.as_uri() made a UNC path's server a URI authority, which SQLite
    refuses; tests/test_package_dist.py opens one on Windows."""
    assert script.read_only is check_package_dist.read_only
    assert check_package_dist.read_only.__code__.co_filename == str(
        Path(script.REPO_ROOT, "geodetic_engine", "_sqlite.py")
    )


@pytest.mark.parametrize("name", ["-projdb", "1projdb", "example projdb", "class"])
def test_a_name_no_package_can_be_imported_by_is_refused(name: str) -> None:
    with pytest.raises(SystemExit, match="names no package that can be imported"):
        script.module_name(name)


@pytest.mark.parametrize(
    "name", ["geodetic-engine", "pygeodetic-engine", "PyGeodetic.Engine"]
)
def test_geodetic_engines_own_names_are_refused(name: str) -> None:
    """Installing the wheel would replace geodetic-engine, which reads it."""
    with pytest.raises(SystemExit, match="is geodetic-engine's own name"):
        script.module_name(name)


def test_a_name_is_normalised_as_installers_do() -> None:
    assert script.module_name("Example.ProjDB") == "example_projdb"


def test_nothing_is_built_for_a_name_that_would_be_refused(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The build takes long, so what can be refused is refused first."""
    settings = tmp_path / "settings.toml"
    settings.write_text("[projdb]\n")
    for name in script.CREDENTIALS:
        monkeypatch.setenv(name, "set")

    def build(*_: Any, **__: Any) -> None:
        raise AssertionError("built anyway")

    monkeypatch.setattr(script, "build", build)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "build_organisation_projdb.py",
            "--config",
            str(settings),
            "--name",
            "1projdb",
            "--output",
            str(tmp_path / "dist"),
        ],
    )
    with pytest.raises(SystemExit, match="names no package that can be imported"):
        script.main()
    assert not (tmp_path / "dist").exists()
