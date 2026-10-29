"""Helpers shared by test modules that read PROJ's own data directory."""

from __future__ import annotations

import os
import sqlite3
from contextlib import closing
from pathlib import Path

import pyproj
import pytest

from geodetic_engine.projdb.schema import BUILD_HISTORY_TABLE

# Directory names holding what a file: URI reads as syntax: "#" begins a
# fragment, "?" a query and "%" an escape. Each is legal in a file name, so a
# database may be installed under one; Windows forbids only the "?".
URI_SYNTAX_DIRECTORIES = (
    pytest.param("with#hash", id="hash"),
    pytest.param("with%41percent", id="percent"),
    pytest.param(
        "with?query",
        id="query",
        marks=pytest.mark.skipif(os.name == "nt", reason="Windows forbids '?'"),
    ),
)


def installed_proj_db() -> Path:
    """The ``proj.db`` PROJ was installed with: the first on its search path
    that this package did not build.

    ``PROJ_DATA`` may list several directories. The devcontainer's puts
    ``local/proj-data`` first, whose ``proj.db`` links to ``build/proj.db``
    once the OSDU catalogue is built into it, then the installed data
    directory, then ``local/grids``. Tests copy this database as a clean base,
    so a database this package built is skipped. An opt-in patched copy
    (``link-local-grids.sh --patched-copy``) is not a build and is returned
    when it comes first.
    """
    for directory in pyproj.datadir.get_data_dir().split(os.pathsep):
        candidate = Path(directory) / "proj.db"
        if candidate.is_file() and not _built_here(candidate):
            return candidate
    raise FileNotFoundError("no proj.db on PROJ's search path that was not built here")


def _built_here(database: Path) -> bool:
    try:
        with closing(
            sqlite3.connect(f"file:{database}?mode=ro", uri=True)
        ) as connection:
            row = connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
                (BUILD_HISTORY_TABLE,),
            ).fetchone()
    except sqlite3.Error:
        return False
    return row is not None
