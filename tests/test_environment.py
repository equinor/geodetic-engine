"""Guards on the build environment.

These assert the properties the rest of the package depends on: that pyproj is
linked against the pinned PROJ built from source, and not against a PROJ
vendored inside a wheel, which would silently change which EPSG dataset answers
every query.
"""

from pathlib import Path

import pyproj
from pyproj import CRS

EXPECTED_PROJ_VERSION = "9.8.1"


def test_proj_version_is_pinned() -> None:
    assert pyproj.proj_version_str == EXPECTED_PROJ_VERSION


def test_proj_data_dir_is_not_vendored() -> None:
    data_dir = Path(pyproj.datadir.get_data_dir())
    assert (data_dir / "proj.db").is_file()
    # A vendored copy lives under site-packages/pyproj/proj_dir.
    assert "site-packages" not in data_dir.parts


def test_epsg_4326_is_latitude_longitude() -> None:
    """EPSG axis order is authoritative and must not be silently normalised."""
    crs = CRS.from_epsg(4326)
    assert [axis.abbrev for axis in crs.axis_info] == ["Lat", "Lon"]
