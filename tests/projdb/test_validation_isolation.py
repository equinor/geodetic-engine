"""Validation runs against private process state, not the caller's PROJ context."""

import os
import shutil
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from pyproj import CRS, datadir

from geodetic_engine.projdb.validate import validate


def test_parallel_validation_does_not_change_caller_context(tmp_path: Path) -> None:
    previous = datadir.get_data_dir()
    environment = os.environ.get("PROJ_DATA")
    databases = [tmp_path / "first.db", tmp_path / "second.db"]
    for database in databases:
        shutil.copyfile(Path(previous) / "proj.db", database)
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
