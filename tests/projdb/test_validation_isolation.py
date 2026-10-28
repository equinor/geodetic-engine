"""Validation runs against private process state, not the caller's PROJ context."""

import json
import os
import shutil
import subprocess
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

import pytest
from pyproj import CRS, datadir

from geodetic_engine.projdb.errors import ProjDbBuildError
from geodetic_engine.projdb.validate import validate
from tests.support import installed_proj_db


def _stub_worker(monkeypatch: pytest.MonkeyPatch, seen: list[Path]) -> None:
    """Replace the worker process with one that records the database it is given."""

    def worker(*args: Any, **kwargs: Any) -> subprocess.CompletedProcess[str]:
        database = Path(json.loads(kwargs["input"])["database"])
        assert database.is_file(), "the database is gone while the worker runs"
        seen.append(database)
        return subprocess.CompletedProcess(
            args, 0, stdout=json.dumps({"crs_checked": 0}), stderr=""
        )

    monkeypatch.setattr(subprocess, "run", worker)


def test_a_staged_proj_db_outlives_the_worker(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """PROJ holds the file until the worker exits; Windows cannot remove it sooner."""
    database = tmp_path / ".proj.db.abc.staging"
    database.write_bytes(b"read by PROJ in the real worker")
    seen: list[Path] = []
    _stub_worker(monkeypatch, seen)

    assert validate(database, authorities=["Example"]) == {"crs_checked": 0}

    [staged] = seen
    assert staged.name == "proj.db"
    assert staged.parent.name.startswith("geodetic-projdb-")
    assert not staged.parent.exists()
    assert database.read_bytes() == b"read by PROJ in the real worker"


def test_a_database_named_proj_db_is_read_in_place(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    database = tmp_path / "proj.db"
    database.write_bytes(b"")
    seen: list[Path] = []
    _stub_worker(monkeypatch, seen)

    validate(database, authorities=["Example"])

    assert seen == [database.resolve()]


def test_a_missing_database_is_a_build_error(tmp_path: Path) -> None:
    """The CLIs report only GeodeticEngineError, not a raw FileNotFoundError."""
    with pytest.raises(ProjDbBuildError, match=r"missing\.db"):
        validate(tmp_path / "missing.db", authorities=["Example"])


def test_parallel_validation_does_not_change_caller_context(tmp_path: Path) -> None:
    previous = datadir.get_data_dir()
    environment = os.environ.get("PROJ_DATA")
    databases = [tmp_path / "first.db", tmp_path / "second.db"]
    for database in databases:
        shutil.copyfile(installed_proj_db(), database)
    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(
            executor.map(
                lambda database: validate(
                    database,
                    authorities=["EPSG"],
                    imported=[("geodetic_crs", "EPSG", "4326")],
                ),
                databases,
            )
        )
    assert [result["crs_checked"] for result in results] == [1, 1]
    assert datadir.get_data_dir() == previous
    assert os.environ.get("PROJ_DATA") == environment
    assert CRS(4326).to_epsg() == 4326
