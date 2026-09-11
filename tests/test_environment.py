"""Guards on the build environment.

These assert the properties the rest of the package depends on: that pyproj is
linked against the pinned PROJ built from source, and not against a PROJ
vendored inside a wheel, which would silently change which EPSG dataset answers
every query.
"""

import os
from pathlib import Path

import pyproj
from pyproj import CRS

EXPECTED_PROJ_VERSION = "9.8.1"


def test_pyproj_version_is_pinned() -> None:
    assert pyproj.__version__ == "3.8.0"


def test_proj_version_is_pinned() -> None:
    assert pyproj.proj_version_str == EXPECTED_PROJ_VERSION


def test_proj_data_dir_is_not_vendored() -> None:
    directories = [
        Path(directory) for directory in pyproj.datadir.get_data_dir().split(os.pathsep)
    ]
    assert any((directory / "proj.db").is_file() for directory in directories)
    # A vendored copy lives under site-packages/pyproj/proj_dir.
    assert all("site-packages" not in directory.parts for directory in directories)


def test_required_grid_inventory_is_installed() -> None:
    directories = [
        Path(directory) for directory in pyproj.datadir.get_data_dir().split(os.pathsep)
    ]
    for name in ("us_nga_egm08_25.tif", "no_kv_href2008a.tif", "us_noaa_conus.tif"):
        assert any((directory / name).is_file() for directory in directories), name


def test_epsg_4326_is_latitude_longitude() -> None:
    """EPSG axis order is authoritative and must not be silently normalised."""
    crs = CRS.from_epsg(4326)
    assert [axis.abbrev for axis in crs.axis_info] == ["Lat", "Lon"]
