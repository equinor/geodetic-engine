"""Guards on the build environment.

These assert the properties the rest of the package depends on: that pyproj is
linked against the pinned PROJ built from source, and not against a PROJ
vendored inside a wheel, which would silently change which EPSG dataset answers
every query; and that the installed database is the stock one, not a copy
patched in place, so that what passes here passes on any stock installation.
"""

import os
import sqlite3
from contextlib import closing
from pathlib import Path

import pyproj
from pyproj import CRS

EXPECTED_PROJ_VERSION = "9.9.0"

REPO_ROOT = Path(__file__).resolve().parents[1]

# The rows scripts/patch-grid-alternatives.sh adds. They belong in a copy of
# the database under local/ or build/, never in the installed one.
PATCHED_GRID_NAMES = (
    "Und_min1x1_egm2008_isw=82_WGS84_TideFree",
    "Und_min1x1_egm2008_isw=82_WGS84_TideFree.gz",
    "NNTrans2018B.gtx",
)


def _search_path() -> list[Path]:
    return [
        Path(directory) for directory in pyproj.datadir.get_data_dir().split(os.pathsep)
    ]


def test_pyproj_version_is_pinned() -> None:
    assert pyproj.__version__ == "3.8.0"


def test_proj_version_is_pinned() -> None:
    assert pyproj.proj_version_str == EXPECTED_PROJ_VERSION


def test_proj_data_dir_is_not_vendored() -> None:
    directories = _search_path()
    assert any((directory / "proj.db").is_file() for directory in directories)
    # A vendored copy lives under site-packages/pyproj/proj_dir.
    assert all("site-packages" not in directory.parts for directory in directories)


def test_required_grid_inventory_is_installed() -> None:
    directories = _search_path()
    for name in ("us_nga_egm08_25.tif", "no_kv_href2008a.tif", "us_noaa_conus.tif"):
        assert any((directory / name).is_file() for directory in directories), name


def test_installed_database_is_not_patched() -> None:
    """The stock proj.db is copied, never modified in place.

    A patched database belongs under the repository -- build/proj.db from
    scripts/build-projdb.sh, or the opt-in copy .devcontainer/link-local-grids.sh
    writes -- and is put on PROJ_DATA by hand. Every database outside the
    repository must be free of the patch rows: otherwise results here carry a
    fingerprint no stock installation reproduces, and a test can pass here on
    a mapping a user's installation does not have.
    """
    installed = [
        directory / "proj.db"
        for directory in _search_path()
        if (directory / "proj.db").is_file()
        and not directory.resolve().is_relative_to(REPO_ROOT)
    ]
    assert installed, "no installed proj.db on PROJ's search path"
    placeholders = ",".join("?" for _ in PATCHED_GRID_NAMES)
    for database in installed:
        with closing(
            sqlite3.connect(f"file:{database}?mode=ro", uri=True)
        ) as connection:
            found = connection.execute(
                "SELECT original_grid_name FROM grid_alternatives "
                f"WHERE original_grid_name IN ({placeholders})",
                PATCHED_GRID_NAMES,
            ).fetchall()
        assert not found, f"{database} carries patched grid_alternatives rows: {found}"


def test_installed_directory_holds_no_links_into_the_repository() -> None:
    """Local grids are read from local/grids/ through PROJ_DATA, not linked in."""
    for directory in _search_path():
        if not directory.is_dir() or directory.resolve().is_relative_to(REPO_ROOT):
            continue
        linked = [
            entry.name
            for entry in directory.iterdir()
            if entry.is_symlink() and entry.resolve().is_relative_to(REPO_ROOT)
        ]
        assert not linked, f"{directory} links into the repository: {linked}"


def test_epsg_4326_is_latitude_longitude() -> None:
    """EPSG axis order is authoritative and must not be silently normalised."""
    crs = CRS.from_epsg(4326)
    assert [axis.abbrev for axis in crs.axis_info] == ["Lat", "Lon"]
