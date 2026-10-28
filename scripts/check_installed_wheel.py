"""Check a release as a user installs it.

    python scripts/check_installed_wheel.py dist/*.whl [dist/*.tar.gz]

Installs the wheel into a new environment with the pyproj wheel it pins, read
from the wheel's own geodetic_engine/_environment.py, and nothing else. There,
scripts/check_package_dist.py checks the distributions against that pyproj's
own proj.db. Then, with no PROJ_DATA or PROJ_LIB set and no build step, OSDU
lookups must answer from the database the wheel ships, in that process and in
a spawned child process, as on Windows. No version is stated here.
"""

from __future__ import annotations

import argparse
import multiprocessing
import os
import tempfile
from pathlib import Path

from build_bundled_osdudb import clean_environment, pinned_environment, run
from check_package_dist import wheel_environment

CHECK_PACKAGE_DIST = Path(__file__).with_name("check_package_dist.py")
OSDU_CRS = ("OSDU", "2003001")
OSDU_CRS_NAME = "Grenada 1953 * DOS-Grd / British West Indies Grid [2003,1443]"
OSDU_ID = "reference-data--CoordinateReferenceSystem:Projected:EPSG::23031"


def lookups() -> tuple[str, str, tuple[str, str, str] | None]:
    """The active database, and what the two lookups answer with it."""
    from pyproj import CRS

    import geodetic_engine  # noqa: F401  # applies the rules, in a child too
    from geodetic_engine.geodesy.database import identified_object
    from geodetic_engine.osdudb import active_database

    found = identified_object("OSDU", OSDU_ID)
    return (
        str(active_database().path),
        CRS.from_authority(*OSDU_CRS).name,
        None if found is None else (found.table_name, found.auth_name, found.code),
    )


def probe() -> None:
    """The lookups, run inside the new environment."""
    import geodetic_engine
    from geodetic_engine.geodesy.database import (
        database_fingerprints,
        database_identity,
    )
    from geodetic_engine.osdudb import active_database

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


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__.splitlines()[0] if __doc__ else None
    )
    parser.add_argument("wheel", type=Path, nargs="?", help="the built project wheel")
    parser.add_argument("sdist", type=Path, nargs="?", help="the built sdist")
    parser.add_argument("--probe", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.probe:
        probe()
        return
    if args.wheel is None:
        parser.error("the wheel is required")
    distributions = [str(path.resolve()) for path in (args.wheel, args.sdist) if path]
    with tempfile.TemporaryDirectory(prefix="geodetic-installed-wheel-") as work:
        python = pinned_environment(
            Path(work) / "venv", wheel_environment(args.wheel), distributions[0]
        )
        environment = clean_environment()
        run([str(python), str(CHECK_PACKAGE_DIST), *distributions], env=environment)
        # Outside the checkout, so the installed package is the one imported.
        run(
            [str(python), str(Path(__file__).resolve()), "--probe"],
            env=environment,
            cwd=Path(work),
        )


if __name__ == "__main__":
    main()
