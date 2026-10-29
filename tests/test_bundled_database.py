"""The OSDU database shipped in the wheel, and when PROJ reads it.

The rules run once, on ``import geodetic_engine``, so every case runs in a
fresh interpreter. It imports a copy of the package that ships a database, as
an installed wheel does: a copy of PROJ's own, marked, standing in for the
OSDU build, so the checks need no build and say which file PROJ opened.
"""

from __future__ import annotations

import json
import os
import shutil
import sqlite3
import struct
import subprocess
import sys
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pyproj
import pytest

from geodetic_engine.projdb.schema import (
    BUILD_HISTORY_TABLE,
    IDENTIFIER_DDL,
    IDENTIFIER_TABLE,
)
from tests.support import URI_SYNTAX_DIRECTORIES

REPO_ROOT = Path(__file__).resolve().parents[1]
CATALOG = REPO_ROOT / "catalogs/osdu/CRS_CT_osdu_official_crs_catalog.json"
GRID = "geodetic_engine_test.gtx"
# Adds the grid's 10 m to a height at 60N 10E, or gives inf without the grid.
PIPELINE = (
    "+proj=pipeline +step +proj=unitconvert +xy_in=deg +xy_out=rad "
    f"+step +proj=vgridshift +grids={GRID} +multiplier=1 "
    "+step +proj=unitconvert +xy_in=rad +xy_out=deg"
)
_CLEARED = ("PROJ_DATA", "PROJ_LIB", "GEODETIC_ENGINE_OSDU_DB", "PYTHONPATH")

_PROBE = """
import json
import logging
import os

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

import pyproj

# BEFORE
import geodetic_engine
from geodetic_engine.geodesy.database import database_fingerprints, database_identity
from geodetic_engine.osdudb import active_database, use_bundled_database

states = []
extra = {}


def record():
    active = active_database()
    try:
        pipeline = pyproj.Transformer.from_pipeline(PIPELINE)
    except pyproj.exceptions.ProjError:
        height = None
    else:
        height = pipeline.transform(10.0, 60.0, 0.0)[2]
    states.append(
        {
            "path": str(active.path),
            "bundled": active.bundled,
            "reason": active.reason,
            "search_path": [str(entry.resolve()) for entry in active.search_path],
            "epsg": pyproj.database.get_database_metadata("EPSG.VERSION"),
            "grid": height == 10.0,
            "provenance": [path for path, _ in database_fingerprints(database_identity())],
            "extra": dict(extra),
        }
    )


# AFTER
record()
print(json.dumps(states))
"""


@dataclass(frozen=True)
class Installation:
    """A package copy on ``sys.path``, and the databases around it."""

    root: Path
    bundled: Path
    stock: Path
    user: Path
    grids: Path

    def paths(self) -> dict[str, str]:
        return {
            "stock": str(self.stock.parent),
            "user": str(self.user.parent),
            "grids": str(self.grids),
            "catalog": str(CATALOG),
            "sep": os.pathsep,
        }


def _clean_environment(**variables: str) -> dict[str, str]:
    environment = {
        key: value for key, value in os.environ.items() if key not in _CLEARED
    }
    return environment | variables


def _copy_package(destination: Path) -> Path:
    """The package as a wheel installs it, without the shipped database."""
    shutil.copytree(
        REPO_ROOT / "geodetic_engine",
        destination / "geodetic_engine",
        ignore=shutil.ignore_patterns("__pycache__", "proj_data"),
    )
    shutil.copyfile(
        REPO_ROOT / ".devcontainer" / "pins.env",
        destination / "geodetic_engine" / "_pins.env",
    )
    return destination


def _marked_copy(stock: Path, destination: Path, marker: str) -> Path:
    destination.parent.mkdir(parents=True)
    shutil.copyfile(stock, destination)
    with closing(sqlite3.connect(destination)) as connection, connection:
        connection.execute(
            "UPDATE metadata SET value = ? WHERE key = 'EPSG.VERSION'", (marker,)
        )
    return destination


@pytest.fixture(scope="session")
def installation(tmp_path_factory: pytest.TempPathFactory) -> Installation:
    root = tmp_path_factory.mktemp("installation")
    # pyproj's own data directory, as resolved with nothing chosen.
    own = subprocess.run(
        [sys.executable, "-c", "import pyproj; print(pyproj.datadir.get_data_dir())"],
        env=_clean_environment(),
        cwd=root,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    stock = next(
        Path(entry) / "proj.db"
        for entry in own.split(os.pathsep)
        if (Path(entry) / "proj.db").is_file()
    ).resolve()
    site = _copy_package(root / "site")
    bundled = _marked_copy(
        stock, site / "geodetic_engine/osdudb/_data/proj_data/proj.db", "bundled"
    )
    # A build of this package's records its history; see projdb.settings.
    with closing(sqlite3.connect(bundled)) as connection, connection:
        connection.execute(
            f"CREATE TABLE {BUILD_HISTORY_TABLE} "
            "(sequence INTEGER PRIMARY KEY, report TEXT NOT NULL)"
        )
    user = _marked_copy(stock, root / "user" / "proj.db", "user")
    grids = root / "grids"
    grids.mkdir()
    with (grids / GRID).open("wb") as stream:
        stream.write(struct.pack(">4d2i", 59.0, 9.0, 2.0, 2.0, 2, 2))
        stream.write(struct.pack(">4f", 10.0, 10.0, 10.0, 10.0))
    return Installation(site, bundled.resolve(), stock, user.resolve(), grids)


def _probe(
    installation: Installation,
    *,
    variables: dict[str, str] | None = None,
    before: str = "",
    after: str = "",
    root: Path | None = None,
    timeout: float | None = None,
) -> tuple[list[dict[str, Any]], str]:
    paths = installation.paths()
    code = (
        _PROBE.replace("# BEFORE", before.format(**paths))
        .replace("# AFTER", after.format(**paths))
        .replace("PIPELINE", repr(PIPELINE), 1)
    )
    environment = _clean_environment(
        PYTHONPATH=str(root or installation.root),
        **{name: value.format(**paths) for name, value in (variables or {}).items()},
    )
    result = subprocess.run(
        [sys.executable, "-c", code],
        env=environment,
        cwd=installation.root.parent,
        capture_output=True,
        text=True,
        check=False,
        timeout=timeout,
    )
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout), result.stderr


# The EPSG.VERSION each marked copy carries is the name of the copy.
_MARKED = frozenset({"bundled", "user"})


@pytest.mark.parametrize(
    ("variables", "before", "expected", "reason", "grid"),
    [
        pytest.param(
            {},
            "",
            "bundled",
            "nothing chose another database",
            False,
            id="nothing set",
        ),
        pytest.param(
            {"PROJ_DATA": "{user}{sep}{grids}"},
            "",
            "user",
            "PROJ_DATA lists {user}, which holds a proj.db",
            True,
            id="PROJ_DATA with a proj.db",
        ),
        pytest.param(
            {"PROJ_DATA": "{grids}"},
            "",
            "bundled",
            "PROJ_DATA names no proj.db",
            True,
            id="PROJ_DATA with grids only",
        ),
        pytest.param(
            {"PROJ_LIB": "{grids}"},
            "",
            "bundled",
            "PROJ_LIB names no proj.db",
            True,
            id="PROJ_LIB with grids only",
        ),
        pytest.param(
            {"PROJ_LIB": "{user}{sep}{grids}"},
            "",
            "user",
            "PROJ_LIB lists {user}, which holds a proj.db",
            True,
            id="PROJ_LIB with a proj.db",
        ),
        pytest.param(
            {"PROJ_DATA": "{grids}", "PROJ_LIB": "{user}"},
            "",
            "bundled",
            "PROJ_DATA names no proj.db",
            True,
            id="PROJ_LIB ignored under PROJ_DATA",
        ),
        pytest.param(
            {},
            "pyproj.datadir.set_data_dir(os.pathsep.join([{stock!r}, {grids!r}]))",
            "stock",
            "set_data_dir() or append_data_dir() was called before",
            True,
            id="set_data_dir before import",
        ),
        pytest.param(
            {},
            "pyproj.datadir.append_data_dir({grids!r})",
            "stock",
            "set_data_dir() or append_data_dir() was called before",
            True,
            id="append_data_dir before import",
        ),
        pytest.param(
            {"GEODETIC_ENGINE_OSDU_DB": "0", "PROJ_DATA": "{stock}{sep}{grids}"},
            "",
            "stock",
            "GEODETIC_ENGINE_OSDU_DB=0 turns it off",
            True,
            id="GEODETIC_ENGINE_OSDU_DB=0",
        ),
        # pyproj reads the variables when it is imported: below, before this
        # package is. The import applies them as a new interpreter reads them.
        pytest.param(
            {},
            "os.environ['PROJ_DATA'] = os.pathsep.join([{user!r}, {grids!r}])",
            "user",
            "PROJ_DATA lists {user}, which holds a proj.db",
            True,
            id="PROJ_DATA set after pyproj was imported",
        ),
        pytest.param(
            {},
            "os.environ['PROJ_LIB'] = {user!r}",
            "user",
            "PROJ_LIB={user} holds a proj.db",
            False,
            id="PROJ_LIB set after pyproj was imported",
        ),
        pytest.param(
            {"PROJ_DATA": "{stock}{sep}{grids}"},
            "os.environ['PROJ_DATA'] = {user!r}",
            "user",
            "PROJ_DATA={user} holds a proj.db",
            False,
            id="PROJ_DATA changed after pyproj was imported",
        ),
        pytest.param(
            {},
            "os.environ['PROJ_DATA'] = os.pathsep.join([{stock!r}, {grids!r}])",
            "stock",
            "PROJ_DATA lists {stock}, which holds a proj.db",
            True,
            id="PROJ_DATA adding grids to pyproj's database after it was imported",
        ),
        pytest.param(
            {"PROJ_LIB": "{user}"},
            "os.environ['PROJ_DATA'] = os.pathsep.join([{stock!r}, {grids!r}])",
            "stock",
            "PROJ_DATA lists {stock}, which holds a proj.db",
            True,
            id="PROJ_DATA set after pyproj read PROJ_LIB",
        ),
        # pyproj gives PROJ the proj.db of the first directory only.
        pytest.param(
            {},
            "os.environ['PROJ_DATA'] = os.pathsep.join([{grids!r}, {user!r}])",
            "user",
            "PROJ_DATA lists {user}, which holds a proj.db",
            True,
            id="PROJ_DATA set later, with no proj.db in its first directory",
        ),
        pytest.param(
            {"PROJ_DATA": "{grids}{sep}{user}"},
            "",
            "user",
            "PROJ_DATA lists {user}, which holds a proj.db",
            True,
            marks=pytest.mark.skipif(
                "+proj" in pyproj.__version__,
                reason="the project's pyproj wheels refuse this environment",
            ),
            id="PROJ_DATA with no proj.db in its first directory",
        ),
    ],
)
def test_import_reads_the_bundled_database_unless_another_was_chosen(
    installation: Installation,
    variables: dict[str, str],
    before: str,
    expected: str,
    reason: str,
    grid: bool,
) -> None:
    [state], log = _probe(installation, variables=variables, before=before)
    assert state["path"] == str(getattr(installation, expected))
    assert state["bundled"] is (expected == "bundled")
    # PROJ itself opened that file, not only the search path naming it.
    if expected in _MARKED:
        assert state["epsg"] == expected
    else:
        assert state["epsg"] not in _MARKED
    assert reason.format(**installation.paths()) in state["reason"]
    assert state["reason"] in log
    # Grids are still found where the environment put them, and, unless the
    # environment named a database of its own, in pyproj's own data directory.
    assert state["grid"] is grid
    if expected != "user":
        assert str(installation.stock.parent) in state["search_path"]
    # Provenance names the database that answered.
    assert state["provenance"][0] == state["path"]


def test_switching_explicitly_overrides_the_choice_made_on_import(
    installation: Installation,
) -> None:
    states, _ = _probe(
        installation,
        variables={"GEODETIC_ENGINE_OSDU_DB": "0", "PROJ_DATA": "{stock}{sep}{grids}"},
        after="record(); use_bundled_database(); record(); use_bundled_database(False)",
    )
    assert [state["path"] for state in states] == [
        str(installation.stock),
        str(installation.bundled),
        str(installation.stock),
    ]
    assert [state["reason"] for state in states] == [
        "GEODETIC_ENGINE_OSDU_DB=0 turns it off",
        "use_bundled_database() switched it on",
        "use_bundled_database(False) switched it off",
    ]
    assert all(state["grid"] for state in states)
    assert states[1]["provenance"][0] == str(installation.bundled)


def test_switching_on_overrides_a_database_the_environment_chose(
    installation: Installation,
) -> None:
    states, log = _probe(
        installation,
        variables={"PROJ_DATA": "{user}{sep}{grids}"},
        after="use_bundled_database(); record(); use_bundled_database(False)",
    )
    assert [state["path"] for state in states] == [
        str(installation.bundled),
        str(installation.user),
    ]
    assert all(state["grid"] for state in states)
    # pyproj read the variable, so the import left PROJ's search path alone.
    assert "PROJ's search path becomes" not in log


def test_switching_off_returns_to_pyprojs_own_search_path(
    installation: Installation,
) -> None:
    [state], _ = _probe(installation, after="use_bundled_database(False)")
    assert state["path"] == str(installation.stock)
    assert state["search_path"] == [str(installation.stock.parent)]


def test_a_variable_set_after_pyproj_was_imported_is_applied_with_a_warning(
    installation: Installation,
) -> None:
    states, log = _probe(
        installation,
        before="os.environ['PROJ_DATA'] = os.pathsep.join([{user!r}, {grids!r}])",
        after="record(); use_bundled_database(); record(); use_bundled_database(False)",
    )
    assert [state["path"] for state in states] == [
        str(installation.user),
        str(installation.bundled),
        str(installation.user),
    ]
    assert "PROJ's search path becomes" in log
    # Switching off returns to the variable's directories, not to pyproj's.
    variable = [str(installation.user.parent), str(installation.grids.resolve())]
    assert states[0]["search_path"] == states[2]["search_path"] == variable
    assert all(state["grid"] for state in states)


@pytest.mark.parametrize("layout", ["older", "none"])
def test_a_database_proj_cannot_read_is_not_applied(
    installation: Installation, tmp_path: Path, layout: str
) -> None:
    """Such as another PROJ release's, named after pyproj was imported.

    PROJ refuses to switch a thread's context to it, and new threads would
    find no database, so applying it would report one that no thread reads.
    """
    refused = tmp_path / "refused" / "proj.db"
    if layout == "older":
        _marked_copy(installation.stock, refused, "refused")
        with closing(sqlite3.connect(refused)) as connection, connection:
            connection.execute(
                "UPDATE metadata SET value = CAST(CAST(value AS INTEGER) - 1 AS TEXT) "
                "WHERE key = 'DATABASE.LAYOUT.VERSION.MINOR'"
            )
    else:
        refused.parent.mkdir()
        with closing(sqlite3.connect(refused)) as connection, connection:
            connection.execute("CREATE TABLE unrelated (value TEXT)")
    [state], log = _probe(
        installation,
        before=f"os.environ['PROJ_LIB'] = {str(refused.parent)!r}",
        after=(
            "from concurrent.futures import ThreadPoolExecutor\n"
            "def opened():\n"
            "    try:\n"
            "        return pyproj.database.get_database_metadata('EPSG.VERSION')\n"
            "    except Exception as error:\n"
            "        return repr(error)\n"
            "with ThreadPoolExecutor(1) as pool:\n"
            "    extra['worker'] = pool.submit(opened).result()\n"
        ),
    )
    # active_database(), the main thread and a new one agree on the database.
    assert state["path"] == str(installation.stock)
    assert state["epsg"] == state["extra"]["worker"] not in {*_MARKED, "refused"}
    assert str(refused) in state["reason"]
    assert state["path"] in state["reason"]
    assert f"WARNING geodetic_engine._bundled_database: {state['reason']}" in log


def test_a_reused_worker_thread_reads_the_database_it_reports(
    installation: Installation,
) -> None:
    """pyproj keeps a PROJ context per thread, and a switch updates only its own."""
    [state], _ = _probe(
        installation,
        after=(
            "from concurrent.futures import ThreadPoolExecutor\n"
            "def worker():\n"
            "    path = str(active_database().path)\n"
            "    return path, pyproj.database.get_database_metadata('EPSG.VERSION')\n"
            "with ThreadPoolExecutor(1) as pool:\n"
            "    extra['before'] = pool.submit(worker).result()\n"
            "    use_bundled_database(False)\n"
            "    extra['after'] = pool.submit(worker).result()\n"
        ),
    )
    assert state["extra"]["before"] == [str(installation.bundled), "bundled"]
    path, opened = state["extra"]["after"]
    assert path == str(installation.stock)
    assert opened not in _MARKED


def _longitude_first(installation: Installation, directory: Path) -> Path:
    """A marked copy of PROJ's database that lists EPSG:4326 longitude first."""
    database = _marked_copy(installation.stock, directory / "proj.db", "swapped")
    with closing(sqlite3.connect(database)) as connection, connection:
        connection.execute(
            "UPDATE geodetic_crs SET coordinate_system_code = '6424' "
            "WHERE auth_name = 'EPSG' AND code = '4326'"
        )
    return database


# Resolves EPSG:4326 through this package, which caches it for every thread.
_AXES = """
from concurrent.futures import ThreadPoolExecutor, wait
from geodetic_engine.geodesy import CoordinateReferenceSystem


def axes():
    return CoordinateReferenceSystem.from_user_input('EPSG:4326').axis_abbreviations
"""

# Holds a named thread inside pyproj's re-pointing of its context: in
# set_data_dir() before the path is written, as older versions of this
# package re-pointed, or in _set_context_data_dir(), as it does now.
_HOLD = """
import threading

set_data_dir = pyproj.datadir.set_data_dir
repoint = pyproj.datadir._set_context_data_dir
held, release = threading.Event(), threading.Event()


def hold():
    if threading.current_thread().name == HELD and not held.is_set():
        held.set()
        extra['released'] = release.wait(30)


def holding_set(path):
    hold()
    set_data_dir(path)


def holding_repoint():
    hold()
    repoint()


pyproj.datadir.set_data_dir = holding_set
pyproj.datadir._set_context_data_dir = holding_repoint
"""

_UNHOLD = (
    "pyproj.datadir.set_data_dir = set_data_dir\n"
    "pyproj.datadir._set_context_data_dir = repoint\n"
)


def test_a_worker_that_set_the_path_itself_follows_a_later_switch(
    installation: Installation, tmp_path: Path
) -> None:
    """A switch back to the path a thread last read still re-points it.

    The worker sets the search path with pyproj, and the switch on puts back
    the path the worker read before that. The worker's own database lists
    EPSG:4326 longitude first.
    """
    swapped = _longitude_first(installation, tmp_path / "swapped")
    [state], _ = _probe(
        installation,
        variables={"PROJ_DATA": str(swapped.parent)},
        after=_AXES
        + (
            "def reads():\n"
            "    path = str(active_database().path)\n"
            "    return path, pyproj.database.get_database_metadata('EPSG.VERSION')\n"
            "chosen = pyproj.datadir.get_data_dir()\n"
            "use_bundled_database()\n"
            "with ThreadPoolExecutor(1) as pool:\n"
            "    pool.submit(reads).result()\n"
            "    pool.submit(pyproj.datadir.set_data_dir, chosen).result()\n"
            "    use_bundled_database()\n"
            "    extra['worker'] = [pool.submit(axes).result(), pool.submit(reads).result()]\n"
            "extra['main'] = [axes(), reads()]\n"
        ),
    )
    expected = [["Lat", "Lon"], [str(installation.bundled), "bundled"]]
    assert [state["extra"]["worker"], state["extra"]["main"]] == [expected] * 2


def test_a_worker_does_not_take_a_switch_under_way_for_its_own_path(
    installation: Installation, tmp_path: Path
) -> None:
    """A switch is held after PROJ's search path changed, before it is done.

    The path is then back to the one the worker last read, which it has since
    set aside for its own. Resolving now, the worker must read the new path,
    not its own database as if nothing had changed.
    """
    swapped = _longitude_first(installation, tmp_path / "swapped")
    [state], _ = _probe(
        installation,
        variables={"PROJ_DATA": str(swapped.parent)},
        timeout=120,
        after=_AXES
        + (
            "import threading\n"
            "repoint = pyproj.datadir._set_context_data_dir\n"
            "held, release = threading.Event(), threading.Event()\n"
            "def holding():\n"
            "    repoint()\n"
            "    if threading.current_thread().name == 'switch' and not held.is_set():\n"
            "        held.set()\n"
            "        extra['released'] = release.wait(30)\n"
            "chosen = pyproj.datadir.get_data_dir()\n"
            "use_bundled_database()\n"
            "with ThreadPoolExecutor(1) as pool:\n"
            "    pool.submit(active_database).result()\n"
            "    pool.submit(pyproj.datadir.set_data_dir, chosen).result()\n"
            "    pyproj.datadir._set_context_data_dir = holding\n"
            "    switch = threading.Thread(target=use_bundled_database, name='switch')\n"
            "    switch.start()\n"
            "    extra['held'] = held.wait(30)\n"
            "    resolving = pool.submit(axes)\n"
            "    done, _ = wait([resolving], 30)\n"
            "    extra['worker'] = resolving.result() if done else None\n"
            "    release.set()\n"
            "    switch.join(30)\n"
            "    extra['joined'] = not switch.is_alive()\n"
            "pyproj.datadir._set_context_data_dir = repoint\n"
            "extra['main'] = axes()\n"
        ),
    )
    assert state["extra"] == {
        "held": True,
        "worker": ["Lat", "Lon"],
        "released": True,
        "joined": True,
        "main": ["Lat", "Lon"],
    }


def test_a_thread_following_the_search_path_cannot_undo_a_switch(
    installation: Installation,
) -> None:
    """A thread re-pointing its context must neither undo nor hold up a switch.

    The follower is held inside its re-pointing while the bundled database is
    switched off; the switch must finish, and stay in force after.
    """
    [state], _ = _probe(
        installation,
        timeout=120,
        after=_HOLD.replace("HELD", "'follower'")
        + (
            "from geodetic_engine.geodesy import CoordinateReferenceSystem\n"
            "switched = threading.Event()\n"
            "def switch_off():\n"
            "    use_bundled_database(False)\n"
            "    switched.set()\n"
            "follower = threading.Thread(\n"
            "    target=CoordinateReferenceSystem.from_user_input,\n"
            "    args=('EPSG:4326',),\n"
            "    name='follower',\n"
            ")\n"
            "follower.start()\n"
            "extra['held'] = held.wait(30)\n"
            "switch = threading.Thread(target=switch_off)\n"
            "switch.start()\n"
            "extra['switched_while_held'] = switched.wait(30)\n"
            "release.set()\n"
            "follower.join(30)\n"
            "switch.join(30)\n"
            "extra['joined'] = not follower.is_alive() and not switch.is_alive()\n"
        )
        + _UNHOLD,
    )
    assert state["extra"] == {
        "held": True,
        "switched_while_held": True,
        "released": True,
        "joined": True,
    }
    assert state["path"] == str(installation.stock)
    assert state["reason"] == "use_bundled_database(False) switched it off"


def test_a_later_set_data_dir_still_overrides(installation: Installation) -> None:
    states, _ = _probe(
        installation,
        variables={"PROJ_DATA": "{grids}"},
        after=(
            "record(); "
            "pyproj.datadir.set_data_dir(os.pathsep.join([{stock!r}, {grids!r}]))"
        ),
    )
    assert [state["path"] for state in states] == [
        str(installation.bundled),
        str(installation.stock),
    ]
    assert "changed PROJ's search path" in states[1]["reason"]
    assert states[1]["grid"]


def test_a_value_that_is_not_a_boolean_leaves_the_default(
    installation: Installation,
) -> None:
    [state], log = _probe(installation, variables={"GEODETIC_ENGINE_OSDU_DB": "maybe"})
    assert state["bundled"]
    assert "GEODETIC_ENGINE_OSDU_DB='maybe' is not a boolean" in log


def test_an_installation_without_the_database_changes_nothing(
    installation: Installation, tmp_path: Path
) -> None:
    states, _ = _probe(
        installation,
        root=_copy_package(tmp_path),
        after=(
            "try:\n"
            "    use_bundled_database()\n"
            "except FileNotFoundError as error:\n"
            "    print(error, file=__import__('sys').stderr)\n"
            "else:\n"
            "    raise SystemExit('switched on a database that is not there')"
        ),
    )
    [state] = states
    assert state["path"] == str(installation.stock)
    assert "has no bundled OSDU database" in state["reason"]


@pytest.mark.parametrize(
    ("variables", "parent", "expected"),
    [
        pytest.param({}, "pass", "bundled", id="nothing set"),
        pytest.param(
            {},
            "os.environ['PROJ_DATA'] = os.pathsep.join([{user!r}, {grids!r}])",
            "user",
            id="PROJ_DATA set after pyproj was imported",
        ),
        pytest.param(
            {"PROJ_DATA": "{user}{sep}{grids}"},
            "del os.environ['PROJ_DATA']",
            "bundled",
            id="PROJ_DATA removed after pyproj was imported",
        ),
    ],
)
def test_a_spawned_child_applies_the_same_rules(
    installation: Installation,
    tmp_path: Path,
    variables: dict[str, str],
    parent: str,
    expected: str,
) -> None:
    """Windows starts every multiprocessing child this way.

    The child's pyproj reads the environment the child inherits, so the parent
    must read the same database when a variable changed after its own pyproj
    was imported.
    """
    paths = installation.paths()
    script = tmp_path / "spawn.py"
    script.write_text(
        "import json\n"
        "import multiprocessing\n"
        "import os\n"
        "\n"
        "import pyproj\n"
        "\n"
        "if __name__ == '__main__':\n"
        f"    {parent.format(**paths)}\n"
        "\n"
        "import geodetic_engine\n"
        "from geodetic_engine.osdudb import active_database\n"
        "\n"
        "\n"
        "def report():\n"
        "    return str(active_database().path)\n"
        "\n"
        "\n"
        "if __name__ == '__main__':\n"
        "    with multiprocessing.get_context('spawn').Pool(1) as pool:\n"
        "        child = pool.apply(report)\n"
        "    print(json.dumps([report(), child]))\n",
        encoding="utf-8",
    )
    result = subprocess.run(
        [sys.executable, str(script)],
        env=_clean_environment(
            PYTHONPATH=str(installation.root),
            **{name: value.format(**paths) for name, value in variables.items()},
        ),
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == [str(getattr(installation, expected))] * 2


# Forks a child, as multiprocessing's fork method does, which asks
# active_database() from a thread of its own, as a worker pool would. The
# answer is empty if that thread could not get the lock.
_FORKED_REPORT = """
import multiprocessing
import threading


def report(connection):
    answer = []

    def ask():
        active = active_database()
        answer.extend([str(active.path), active.reason])

    thread = threading.Thread(target=ask, daemon=True)
    thread.start()
    thread.join(15)
    connection.send(answer)


def fork_and_report():
    fork = multiprocessing.get_context('fork')
    receiver, sender = fork.Pipe(duplex=False)
    child = fork.Process(target=report, args=(sender,))
    child.start()
    answer = receiver.recv() if receiver.poll(30) else None
    child.join(30)
    if child.is_alive():
        child.kill()
        child.join(30)
    return answer, child.exitcode
"""

_FORK = pytest.mark.skipif(not hasattr(os, "fork"), reason="needs os.fork")


@_FORK
def test_a_child_forked_during_a_switch_starts_after_it(
    installation: Installation,
) -> None:
    """Linux forks multiprocessing children before Python 3.14 by default.

    The switch is held after it set the search path, until a fork starts:
    a hook registered here runs before this package's, which must then wait
    for the switch. A child forked there would inherit the lock held, with no
    thread left to release it, or a path the recorded choice does not match.
    """
    [state], _ = _probe(
        installation,
        timeout=120,
        after=_FORKED_REPORT
        + (
            "set_data_dir = pyproj.datadir.set_data_dir\n"
            "held, forking = threading.Event(), threading.Event()\n"
            "def holding(path):\n"
            "    set_data_dir(path)\n"
            "    if threading.current_thread().name == 'switch' and not held.is_set():\n"
            "        held.set()\n"
            "        extra['forking'] = forking.wait(30)\n"
            "pyproj.datadir.set_data_dir = holding\n"
            "os.register_at_fork(before=forking.set)\n"
            "switch = threading.Thread(\n"
            "    target=use_bundled_database, args=(False,), name='switch'\n"
            ")\n"
            "switch.start()\n"
            "extra['held'] = held.wait(30)\n"
            "extra['child'], extra['exitcode'] = fork_and_report()\n"
            "switch.join(30)\n"
            "extra['joined'] = not switch.is_alive()\n"
            "pyproj.datadir.set_data_dir = set_data_dir\n"
        ),
    )
    assert state["extra"] == {
        "held": True,
        "forking": True,
        "child": [
            str(installation.stock),
            "use_bundled_database(False) switched it off",
        ],
        "exitcode": 0,
        "joined": True,
    }


@_FORK
def test_a_handler_that_forks_on_a_warning_leaves_the_child_the_lock(
    installation: Installation,
) -> None:
    """The warning is logged once the lock is released, not while it is held.

    A child forked while its parent held the lock would inherit it held by a
    thread that never returns to release it: the child's own threads would
    wait for it for ever.
    """
    [state], _ = _probe(
        installation,
        timeout=120,
        after=_FORKED_REPORT
        + (
            "import logging\n"
            "import geodetic_engine._bundled_database as module\n"
            "class Forking(logging.Handler):\n"
            "    def emit(self, record):\n"
            "        if record.levelno == logging.WARNING and 'forked' not in extra:\n"
            "            extra['forked'] = record.getMessage()\n"
            "            extra['child'], extra['exitcode'] = fork_and_report()\n"
            "logging.getLogger('geodetic_engine').addHandler(Forking())\n"
            "os.environ['GEODETIC_ENGINE_OSDU_DB'] = 'maybe'\n"
            "module._choice = None\n"
            "module.activate_at_import()\n"
        ),
    )
    child = state["extra"].pop("child")
    assert child and child[0] == str(installation.bundled)
    assert state["extra"] == {
        "forked": "GEODETIC_ENGINE_OSDU_DB='maybe' is not a boolean; the bundled "
        "OSDU database stays on by default",
        "exitcode": 0,
    }


@_FORK
def test_a_handler_forking_in_another_thread_cannot_deadlock_the_import(
    installation: Installation,
) -> None:
    """A thread holding the lock must never wait on a handler that is forking.

    Another thread is inside a handler, and forks once the import's warning
    reaches that handler and waits for it. The fork waits for the lock, so
    the warning must not be logged with the lock held.
    """
    [state], _ = _probe(
        installation,
        timeout=120,
        after=_FORKED_REPORT
        + (
            "import logging\n"
            "import geodetic_engine._bundled_database as module\n"
            "emitting, warned = threading.Event(), threading.Event()\n"
            "class Forking(logging.Handler):\n"
            "    def emit(self, record):\n"
            "        if record.name == 'other':\n"
            "            emitting.set()\n"
            "            extra['warned'] = warned.wait(30)\n"
            "            extra['child'], extra['exitcode'] = fork_and_report()\n"
            "def watch(record):\n"
            "    if record.levelno == logging.WARNING and record.name != 'other':\n"
            "        warned.set()\n"
            "    return True\n"
            "handler = Forking()\n"
            "handler.addFilter(watch)\n"
            "logging.getLogger().addHandler(handler)\n"
            "other = threading.Thread(\n"
            "    target=logging.getLogger('other').warning, args=('forking',)\n"
            ")\n"
            "other.start()\n"
            "extra['emitting'] = emitting.wait(30)\n"
            "os.environ['GEODETIC_ENGINE_OSDU_DB'] = 'maybe'\n"
            "module._choice = None\n"
            "importing = threading.Thread(target=module.activate_at_import)\n"
            "importing.start()\n"
            "importing.join(60)\n"
            "other.join(60)\n"
            "extra['joined'] = not importing.is_alive() and not other.is_alive()\n"
        ),
    )
    child = state["extra"].pop("child")
    assert child and child[0] == str(installation.bundled)
    assert state["extra"] == {
        "emitting": True,
        "warned": True,
        "exitcode": 0,
        "joined": True,
    }


@_FORK
def test_a_fork_from_pyprojs_own_warning_leaves_the_child_the_lock(
    installation: Installation,
) -> None:
    """pyproj warns from set_data_dir() when the first directory has no proj.db.

    That warning is issued while a thread re-points its context with the
    lock held, so code of the caller's can run, and fork, there. Neither the
    parent nor the child's own threads may then wait for the lock for ever.
    """
    [state], _ = _probe(
        installation,
        timeout=120,
        after=_FORKED_REPORT
        + (
            "import warnings\n"
            "parent = os.getpid()\n"
            "def forking(message, *args, **kwargs):\n"
            "    if os.getpid() == parent and 'forked' not in extra:\n"
            "        extra['forked'] = str(message)\n"
            "        extra['child'], extra['exitcode'] = fork_and_report()\n"
            "warnings.filterwarnings('always', 'pyproj unable to set')\n"
            "pyproj.datadir.set_data_dir(os.pathsep.join([{grids!r}, {stock!r}]))\n"
            "warnings.showwarning = forking\n"
            "worker = threading.Thread(target=active_database)\n"
            "worker.start()\n"
            "worker.join(60)\n"
            "extra['joined'] = not worker.is_alive()\n"
        ),
    )
    child = state["extra"].pop("child")
    assert child and child[0] == "None"
    assert "changed PROJ's search path" in child[1]
    assert state["extra"] == {
        "forked": "pyproj unable to set PROJ database path.",
        "exitcode": 0,
        "joined": True,
    }


@_FORK
def test_a_raw_fork_inside_the_lock_leaves_the_child_able_to_take_it(
    installation: Installation,
) -> None:
    """os.fork() returns into the ``with`` block in the child.

    The child starts with the lock free, so leaving the block must not
    release it again, and a thread of the child's must then be able to take
    it.
    """
    [state], _ = _probe(
        installation,
        timeout=120,
        after=(
            "import threading\n"
            "from geodetic_engine import _search_path\n"
            "reading, writing = os.pipe()\n"
            "pid = None\n"
            "try:\n"
            "    with _search_path.lock:\n"
            "        pid = os.fork()\n"
            "except RuntimeError as error:\n"
            "    if pid != 0:\n"
            "        raise\n"
            "    os.write(writing, ('RuntimeError: ' + str(error)).encode())\n"
            "    os._exit(1)\n"
            "if pid == 0:\n"
            "    try:\n"
            "        answer = []\n"
            "        def ask():\n"
            "            answer.append(str(active_database().path))\n"
            "        thread = threading.Thread(target=ask, daemon=True)\n"
            "        thread.start()\n"
            "        thread.join(15)\n"
            "        os.write(writing, (answer[0] if answer else 'blocked').encode())\n"
            "    finally:\n"
            "        os._exit(0)\n"
            "os.close(writing)\n"
            "os.waitpid(pid, 0)\n"
            "extra['child'] = os.read(reading, 4096).decode()\n"
        ),
    )
    assert state["extra"]["child"] == str(installation.bundled)


@_FORK
def test_a_warning_hook_waiting_on_a_fork_cannot_deadlock_it(
    installation: Installation,
) -> None:
    """pyproj warns when the first directory of its path holds no proj.db.

    A thread re-pointing its context meets that warning, and the hook waits
    there until another thread has forked. The fork waits for the lock, so
    the warning must not be issued with the lock held.
    """
    [state], _ = _probe(
        installation,
        timeout=120,
        after=_FORKED_REPORT
        + (
            "import warnings\n"
            "blocking, forked = threading.Event(), threading.Event()\n"
            "def waiting(message, *args, **kwargs):\n"
            "    if threading.current_thread().name == 'warned' and not blocking.is_set():\n"
            "        blocking.set()\n"
            "        extra['forked'] = forked.wait(30)\n"
            "warnings.filterwarnings('always', 'pyproj unable to set')\n"
            "pyproj.datadir.set_data_dir(os.pathsep.join([{grids!r}, {stock!r}]))\n"
            "warnings.showwarning = waiting\n"
            "warned = threading.Thread(target=active_database, name='warned')\n"
            "warned.start()\n"
            "extra['blocking'] = blocking.wait(30)\n"
            "extra['child'], extra['exitcode'] = fork_and_report()\n"
            "forked.set()\n"
            "warned.join(60)\n"
            "extra['joined'] = not warned.is_alive()\n"
        ),
    )
    child = state["extra"].pop("child")
    assert child and child[0] == "None"
    assert state["extra"] == {
        "blocking": True,
        "exitcode": 0,
        "forked": True,
        "joined": True,
    }


def test_builds_start_from_the_stock_database_while_the_bundled_one_is_active(
    installation: Installation,
) -> None:
    """The default base skips any database this package built, the bundled one too."""
    [state], _ = _probe(
        installation,
        after=(
            "from geodetic_engine.osdudb import load_config\n"
            "from geodetic_engine.projdb.settings import default_base_proj_db\n"
            "config = load_config(catalog={catalog!r}, env={{}}, load_dotenv_file=False)\n"
            "extra['bases'] = [str(default_base_proj_db()), str(config.base_proj_db)]"
        ),
    )
    assert state["bundled"]
    assert state["extra"]["bases"] == [str(installation.stock)] * 2


@pytest.mark.parametrize("name", URI_SYNTAX_DIRECTORIES)
def test_an_installation_under_any_directory_name_answers_osdu_ids(
    installation: Installation, tmp_path: Path, name: str
) -> None:
    """A path is not a URI: "#", "?" and "%" in it name the directory."""
    root = _copy_package(tmp_path / name)
    bundled = root / "geodetic_engine/osdudb/_data/proj_data/proj.db"
    bundled.parent.mkdir(parents=True)
    shutil.copyfile(installation.bundled, bundled)
    osdu_id = "reference-data--CoordinateReferenceSystem:Projected:EPSG::23031"
    with closing(sqlite3.connect(bundled)) as connection, connection:
        for statement in IDENTIFIER_DDL:
            connection.execute(statement)
        connection.execute(
            f"INSERT INTO {IDENTIFIER_TABLE} VALUES "
            "('OSDU', ?, 'projected_crs', 'EPSG', '23031', 0, NULL)",
            (osdu_id,),
        )
    [state], _ = _probe(
        installation,
        root=root,
        after=(
            "from geodetic_engine.geodesy import CoordinateReferenceSystem\n"
            "from geodetic_engine.projdb.settings import default_base_proj_db\n"
            f"crs = CoordinateReferenceSystem.from_osdu_id('opendes:{osdu_id}')\n"
            "extra['answers'] = [crs.authority_code, str(default_base_proj_db())]"
        ),
    )
    assert state["path"] == str(bundled.resolve())
    assert state["extra"]["answers"] == ["EPSG:23031", str(installation.stock)]


def test_pyproj_keeps_the_explicit_data_dir_where_this_package_reads_it() -> None:
    """A pyproj upgrade that renames the private global must fail here."""
    code = (
        "from pyproj import datadir\n"
        "from geodetic_engine._bundled_database import _user_data_dir\n"
        "assert datadir._USER_PROJ_DATA is None and _user_data_dir() is None\n"
        "datadir.set_data_dir(datadir.get_data_dir())\n"
        "assert datadir._USER_PROJ_DATA == datadir.get_data_dir() == _user_data_dir()\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", f"import pyproj\n{code}"],
        env=_clean_environment(GEODETIC_ENGINE_OSDU_DB="0"),
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr


def test_pyproj_re_points_one_thread_where_this_package_calls_it(
    installation: Installation,
) -> None:
    """A pyproj upgrade that renames or changes _set_context_data_dir() fails here.

    It must re-point the calling thread alone, from the current path, and
    leave the path as it is.
    """
    [state], _ = _probe(
        installation,
        variables={"GEODETIC_ENGINE_OSDU_DB": "0", "PROJ_DATA": "{user}"},
        timeout=120,
        after=(
            "import threading\n"
            "def opened():\n"
            "    return pyproj.database.get_database_metadata('EPSG.VERSION')\n"
            "ready, moved = threading.Event(), threading.Event()\n"
            "def other():\n"
            "    extra['before'] = opened()\n"
            "    ready.set()\n"
            "    extra['moved'] = moved.wait(30)\n"
            "    extra['kept'] = opened()\n"
            "    pyproj.datadir._set_context_data_dir()\n"
            "    extra['after'] = opened()\n"
            "thread = threading.Thread(target=other)\n"
            "thread.start()\n"
            "extra['ready'] = ready.wait(30)\n"
            "pyproj.datadir.set_data_dir({stock!r})\n"
            "chosen = pyproj.datadir._USER_PROJ_DATA\n"
            "moved.set()\n"
            "thread.join(30)\n"
            "extra['joined'] = not thread.is_alive()\n"
            "extra['path_kept'] = pyproj.datadir._USER_PROJ_DATA == chosen\n"
        ),
    )
    after = state["extra"].pop("after")
    assert after not in _MARKED
    assert state["extra"] == {
        "before": "user",
        "ready": True,
        "moved": True,
        "kept": "user",
        "joined": True,
        "path_kept": True,
    }
