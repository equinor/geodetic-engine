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

import pytest

from geodetic_engine.projdb.schema import BUILD_HISTORY_TABLE

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

logging.basicConfig(level=logging.INFO, format="%(name)s: %(message)s")

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
    states, _ = _probe(
        installation,
        variables={"PROJ_DATA": "{user}{sep}{grids}"},
        after="use_bundled_database(); record(); use_bundled_database(False)",
    )
    assert [state["path"] for state in states] == [
        str(installation.bundled),
        str(installation.user),
    ]
    assert all(state["grid"] for state in states)


def test_switching_off_returns_to_pyprojs_own_search_path(
    installation: Installation,
) -> None:
    [state], _ = _probe(installation, after="use_bundled_database(False)")
    assert state["path"] == str(installation.stock)
    assert state["search_path"] == [str(installation.stock.parent)]


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


def test_a_spawned_child_applies_the_same_rules(
    installation: Installation, tmp_path: Path
) -> None:
    """Windows starts every multiprocessing child this way."""
    script = tmp_path / "spawn.py"
    script.write_text(
        "import json\n"
        "import multiprocessing\n"
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
        env=_clean_environment(PYTHONPATH=str(installation.root)),
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == [str(installation.bundled)] * 2


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
