"""Check that an installed release answers OSDU lookups with no setup.

Run it with the Python of a new environment holding the release wheel and the
pinned pyproj wheel, and nothing else, with no PROJ_DATA or PROJ_LIB set:

    python -m venv /tmp/clean
    /tmp/clean/bin/python -m pip install dist/*.whl "pyproj==3.8.0+proj9.9.0" \
        --extra-index-url https://equinor.github.io/geodetic-engine/simple/
    /tmp/clean/bin/python scripts/check_installed_wheel.py

No database is built: the one the wheel ships must be the one PROJ reads, in
this process and in a spawned child process, as on Windows.
"""

from __future__ import annotations

import multiprocessing
import os
from pathlib import Path

from pyproj import CRS

# Imported at the top, so a spawned child imports it again.
import geodetic_engine
from geodetic_engine.geodesy.database import (
    database_fingerprints,
    database_identity,
    identified_object,
)
from geodetic_engine.osdudb import active_database

OSDU_CRS = ("OSDU", "2003001")
OSDU_CRS_NAME = "Grenada 1953 * DOS-Grd / British West Indies Grid [2003,1443]"
OSDU_ID = "reference-data--CoordinateReferenceSystem:Projected:EPSG::23031"


def lookups() -> tuple[str, str, tuple[str, str, str] | None]:
    """The active database, and what the two lookups answer with it."""
    found = identified_object("OSDU", OSDU_ID)
    return (
        str(active_database().path),
        CRS.from_authority(*OSDU_CRS).name,
        None if found is None else (found.table_name, found.auth_name, found.code),
    )


def main() -> None:
    for name in ("PROJ_DATA", "PROJ_LIB", "GEODETIC_ENGINE_OSDU_DB"):
        if name in os.environ:
            raise SystemExit(f"{name} is set; run this in a clean environment")
    package = Path(geodetic_engine.__file__).resolve().parent
    if "site-packages" not in package.parts:
        raise SystemExit(f"imported {package}, not an installed wheel")

    active = active_database()
    if not active.bundled or active.path is None:
        raise SystemExit(f"PROJ does not read the bundled database: {active}")
    if not active.path.is_relative_to(package):
        raise SystemExit(f"{active.path} is not inside {package}")
    here = lookups()
    if here[1] != OSDU_CRS_NAME:
        raise SystemExit(f"OSDU:2003001 is {here[1]!r}, not {OSDU_CRS_NAME!r}")
    if here[2] != ("projected_crs", "EPSG", "23031"):
        raise SystemExit(f"{OSDU_ID} names {here[2]}, not EPSG:23031")
    fingerprints = dict(database_fingerprints(database_identity()))
    if str(active.path) not in fingerprints:
        raise SystemExit(f"provenance does not name {active.path}: {fingerprints}")

    with multiprocessing.get_context("spawn").Pool(1) as pool:
        child = pool.apply(lookups)
    if child != here:
        raise SystemExit(f"a spawned child answered {child}, not {here}")

    print(
        f"geodetic_engine {geodetic_engine.__version__} reads {active.path} "
        f"({active.reason}); OSDU:2003001 is {here[1]!r}; {OSDU_ID} names "
        f"EPSG:23031; a spawned child agrees"
    )


if __name__ == "__main__":
    main()
