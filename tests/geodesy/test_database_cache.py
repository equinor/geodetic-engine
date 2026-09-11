"""Database switches must not reuse another generation's resolved pipeline."""

import shutil
import sqlite3
from contextlib import closing
from pathlib import Path

import pytest
from pyproj import datadir

from geodetic_engine.geodesy import transform
from geodetic_engine.projdb.validate import _proj_data


def test_pipeline_cache_is_scoped_to_database_generation(tmp_path: Path) -> None:
    base = Path(datadir.get_data_dir()) / "proj.db"
    database = tmp_path / "proj.db"
    shutil.copyfile(base, database)
    original = transform("EPSG:4326", "EPSG:32631", (3, 0))
    with _proj_data(database):
        first = transform("EPSG:4326", "EPSG:32631", (3, 0))
        with closing(sqlite3.connect(database)) as connection:
            connection.execute(
                "UPDATE conversion_table SET param4_value=600000 WHERE auth_name='EPSG' AND code=16031"
            )
            connection.commit()
        second = transform("EPSG:4326", "EPSG:32631", (3, 0))
    assert original.coordinates[0][0] == pytest.approx(500000, abs=1e-6)
    assert first.coordinates[0][0] == pytest.approx(500000, abs=1e-6)
    assert second.coordinates[0][0] == pytest.approx(600000, abs=1e-6)
    assert first.database_fingerprints != second.database_fingerprints
    assert (
        transform("EPSG:4326", "EPSG:32631", (3, 0)).coordinates == original.coordinates
    )
