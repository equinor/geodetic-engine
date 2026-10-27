"""Smoke test of a pyproj wheel built by .github/workflows/pyproj-wheels.yml.

cibuildwheel runs it against every wheel, with the PROJ the wheel must bundle:

    python scripts/check_pyproj_wheel.py 9.9.0
"""

from __future__ import annotations

import argparse
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
from contextlib import closing
from pathlib import Path

import pyproj
from pyproj import CRS, Transformer

# Northing of 60N 3E in UTM zone 31N, on the zone's central meridian.
UTM_31N_NORTHING_60N = 6651411.190362714
_MARKER = "geodetic-engine-custom-database"


def _check_data_paths() -> None:
    bundled = Path(pyproj.__file__).parent / "proj_dir" / "share" / "proj"
    database = bundled / "proj.db"
    if not database.is_file():
        sys.exit(f"no bundled proj.db in {bundled}")

    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        custom = root / "custom"
        custom.mkdir()
        shutil.copyfile(database, custom / "proj.db")
        with closing(sqlite3.connect(custom / "proj.db")) as connection, connection:
            changed = connection.execute(
                "UPDATE metadata SET value = ? WHERE key = 'EPSG.VERSION'",
                (_MARKER,),
            ).rowcount
        if changed != 1:
            sys.exit("bundled proj.db has no unique EPSG.VERSION metadata")

        code = """
import os
import sys
from pathlib import Path
import pyproj

expected, bundled, marker = sys.argv[1:]
directories = pyproj.datadir.get_data_dir().split(os.pathsep)
if Path(directories[0]).resolve() != Path(expected).resolve():
    raise SystemExit(f"PROJ_DATA was ignored: {directories}")
if pyproj.database.get_database_metadata("EPSG.VERSION") != marker:
    raise SystemExit("PROJ did not read the custom database")
pyproj.datadir.set_data_dir(bundled)
if Path(pyproj.datadir.get_data_dir()).resolve() != Path(bundled).resolve():
    raise SystemExit("set_data_dir did not override PROJ_DATA")
if pyproj.database.get_database_metadata("EPSG.VERSION") == marker:
    raise SystemExit("set_data_dir did not switch databases")
"""
        environment = os.environ.copy()
        environment["PROJ_DATA"] = str(custom)
        subprocess.run(
            [
                sys.executable,
                "-W",
                "error::UserWarning",
                "-c",
                code,
                str(custom),
                str(bundled),
                _MARKER,
            ],
            env=environment,
            check=True,
        )

        grids = root / "grids"
        grids.mkdir()
        environment["PROJ_DATA"] = str(grids)
        code = """
import os
import sys
from pathlib import Path
import pyproj

directories = [
    Path(p).resolve()
    for p in pyproj.datadir.get_data_dir().split(os.pathsep)
]
expected = [Path(p).resolve() for p in sys.argv[1:]]
if directories != expected or not (directories[0] / "proj.db").is_file():
    raise SystemExit(f"grid-only PROJ_DATA lost the bundled database: {directories}")
if not pyproj.database.get_database_metadata("EPSG.VERSION"):
    raise SystemExit("grid-only PROJ_DATA did not open the bundled database")
"""
        subprocess.run(
            [
                sys.executable,
                "-W",
                "error::UserWarning",
                "-c",
                code,
                str(bundled),
                str(grids),
            ],
            env=environment,
            check=True,
        )

        environment["PROJ_DATA"] = os.pathsep.join((str(grids), str(custom)))
        result = subprocess.run(
            [
                sys.executable,
                "-c",
                "from pyproj import datadir; datadir.get_data_dir()",
            ],
            env=environment,
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode == 0 or "first PROJ_DATA/PROJ_LIB" not in result.stderr:
            sys.exit(f"invalid PROJ_DATA order was not rejected: {result.stderr}")


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

    _check_data_paths()
    print(f"pyproj {pyproj.__version__} with PROJ {pyproj.proj_version_str}: ok")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("proj_version", help="the PROJ version the wheel must bundle")
    check(parser.parse_args().proj_version)


if __name__ == "__main__":
    main()
