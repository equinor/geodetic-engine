"""Database switches must not reuse another generation's resolved pipeline."""

import shutil
import sqlite3
from contextlib import closing
from pathlib import Path

import pytest
from pyproj import datadir

from geodetic_engine.geodesy import transform
from geodetic_engine.geodesy.database import grid_dataset
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


def test_grid_alias_cache_is_scoped_to_database_generation(tmp_path: Path) -> None:
    database = tmp_path / "proj.db"
    shutil.copyfile(Path(datadir.get_data_dir()) / "proj.db", database)
    with _proj_data(database):
        original = grid_dataset("A66 National (13.09.01).gsb")
        assert original is not None
        assert grid_dataset("au_icsm_A66_National_13_09_01.tif") == original
        with closing(sqlite3.connect(database)) as connection:
            connection.execute(
                "UPDATE grid_alternatives SET proj_grid_name=?, old_proj_grid_name=?, url=? "
                "WHERE original_grid_name=?",
                (
                    "provider_updated.tif",
                    "legacy_a66.gsb",
                    "https://cdn.proj.org/provider_updated.tif",
                    "A66 National (13.09.01).gsb",
                ),
            )
            connection.commit()
        assert grid_dataset("provider_updated.tif") == original
        assert grid_dataset("legacy_a66.gsb") == original
        assert grid_dataset("au_icsm_A66_National_13_09_01.tif") is None
    assert grid_dataset("provider_updated.tif") is None


def test_conflicting_grid_aliases_are_not_selected_arbitrarily(tmp_path: Path) -> None:
    database = tmp_path / "proj.db"
    shutil.copyfile(Path(datadir.get_data_dir()) / "proj.db", database)
    with closing(sqlite3.connect(database)) as connection:
        connection.execute(
            "UPDATE grid_alternatives SET proj_grid_name=?, url=? "
            "WHERE original_grid_name IN (?, ?)",
            (
                "shared_alias.tif",
                "https://cdn.proj.org/shared_alias.tif",
                "conus.las",
                "A66 National (13.09.01).gsb",
            ),
        )
        connection.commit()
    with _proj_data(database):
        assert grid_dataset("conus") is not None
        assert grid_dataset("A66 National (13.09.01).gsb") is not None
        assert grid_dataset("shared_alias.tif") is None
