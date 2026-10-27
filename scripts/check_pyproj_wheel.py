"""Smoke test of a pyproj wheel built by .github/workflows/pyproj-wheels.yml.

cibuildwheel runs it against every wheel, with the PROJ the wheel must bundle:

    python scripts/check_pyproj_wheel.py 9.9.0
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

import pyproj
from pyproj import CRS, Transformer

# Northing of 60N 3E in UTM zone 31N, on the zone's central meridian.
UTM_31N_NORTHING_60N = 6651411.190362714


def check(expected_proj: str) -> None:
    if pyproj.proj_version_str != expected_proj:
        sys.exit(f"bundles PROJ {pyproj.proj_version_str}, not {expected_proj}")
    if not pyproj.__version__.endswith(f"+proj{expected_proj}"):
        sys.exit(f"pyproj {pyproj.__version__} lacks the +proj{expected_proj} label")

    directories = [Path(d) for d in pyproj.datadir.get_data_dir().split(os.pathsep)]
    if not any((directory / "proj.db").is_file() for directory in directories):
        sys.exit(f"no proj.db in {directories}")

    axes = [axis.abbrev for axis in CRS.from_epsg(4326).axis_info]
    if axes != ["Lat", "Lon"]:
        sys.exit(f"EPSG:4326 axes are {axes}, not latitude first")
    easting, northing = Transformer.from_crs("EPSG:4326", "EPSG:32631").transform(
        60.0, 3.0
    )
    if abs(easting - 500000.0) > 1e-6 or abs(northing - UTM_31N_NORTHING_60N) > 1e-6:
        sys.exit(f"EPSG:4326 to EPSG:32631 gave {easting}, {northing}")

    print(f"pyproj {pyproj.__version__} with PROJ {pyproj.proj_version_str}: ok")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("proj_version", help="the PROJ version the wheel must bundle")
    check(parser.parse_args().proj_version)


if __name__ == "__main__":
    main()
