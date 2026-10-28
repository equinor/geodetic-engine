"""Build the OSDU proj.db the release wheel ships.

    python scripts/build_bundled_osdudb.py

Runs ``geodetic-osdudb build --official`` in a new virtual environment holding
this checkout and the pyproj wheel .devcontainer/pins.env pins, from the
project's index, so the base database is that wheel's own proj.db: exactly
the EPSG content users install. Nothing of the calling environment's PROJ setup
reaches the build. The result is checked as scripts/check_package_dist.py
checks a release, against that same proj.db, then written to
geodetic_engine/osdudb/_data/proj_data/proj.db, alone in its directory, where
the build backend includes it in the sdist and the wheel. It is gitignored.
No version is stated here: moving a pin needs no change to this script.

The release workflow runs this before ``uv build``. Needs network access to
PyPI and to the project's pyproj index, and the Python the package requires.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import tempfile
import venv
from pathlib import Path
from typing import Any

from check_package_dist import check_database, stock_proj_db
from sync_pins import read_pins

REPO_ROOT = Path(__file__).resolve().parents[1]
DESTINATION = REPO_ROOT / "geodetic_engine/osdudb/_data/proj_data/proj.db"
CATALOG = REPO_ROOT / "catalogs/osdu/CRS_CT_osdu_official_crs_catalog.json"
PROVENANCE = CATALOG.with_suffix(".source.json")
# Left behind only when a run is interrupted while publishing.
_PARTIAL = ".proj.db.partial"


def clean_environment() -> dict[str, str]:
    """The calling environment without anything that steers PROJ or the package."""
    removed = {"PROJ_DATA", "PROJ_LIB", "PYTHONPATH", "VIRTUAL_ENV"}
    environment = {
        key: value
        for key, value in os.environ.items()
        if key not in removed and not key.startswith("GEODETIC_ENGINE_")
    }
    environment["PIP_DISABLE_PIP_VERSION_CHECK"] = "1"
    return environment


def run(
    command: list[str],
    *,
    env: dict[str, str],
    cwd: Path | None = None,
    capture: bool = False,
) -> str:
    print("+", " ".join(command), flush=True)
    return (
        subprocess.run(
            command,
            env=env,
            cwd=cwd,
            check=True,
            stdout=subprocess.PIPE if capture else None,
            text=True,
        ).stdout
        or ""
    )


def pinned_environment(directory: Path, pins: Any, *packages: str) -> Path:
    """Create a virtual environment with packages and the pyproj wheel pinned.

    Args:
        directory: Where to create it.
        pins: The pins that apply, as geodetic_engine/_pins.py reads them.
        packages: What else to install.

    Returns:
        Its Python.
    """
    venv.create(directory, with_pip=True)
    python = directory / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    run(
        [
            str(python),
            "-m",
            "pip",
            "install",
            "--quiet",
            "--only-binary",
            "pyproj",
            "--extra-index-url",
            pins.pyproj_wheel_index,
            *packages,
            pins.pyproj_requirement,
        ],
        env=clean_environment(),
    )
    return python


def own_proj_db(python: Path) -> Path:
    """The proj.db of the pyproj installed for a Python, as PROJ ships it."""
    search_path = run(
        [str(python), "-c", "import pyproj; print(pyproj.datadir.get_data_dir())"],
        env=clean_environment(),
        capture=True,
    )
    return stock_proj_db(search_path.strip())


def build(work: Path, pins: Any) -> tuple[Path, Path]:
    """Build the official catalogue in a new environment.

    Returns:
        The database, and the proj.db it was built on.
    """
    python = pinned_environment(work / "venv", pins, str(REPO_ROOT))
    output = work / "output"
    output.mkdir()
    # Run elsewhere than the checkout: no geodetic-osdudb.toml or .env of the
    # developer's is found, and the installed package, not the source tree,
    # is imported. The build must start from the wheel's stock database.
    run(
        [
            str(python),
            "-m",
            "geodetic_engine.osdudb",
            "build",
            "--official",
            "--output",
            str(output / "proj.db"),
        ],
        env=clean_environment() | {"GEODETIC_ENGINE_OSDU_DB": "0"},
        cwd=output,
    )
    return output / "proj.db", own_proj_db(python)


def publish(database: Path) -> None:
    """Replace the shipped database, keeping its directory to itself."""
    directory = DESTINATION.parent
    directory.mkdir(parents=True, exist_ok=True)
    (directory / _PARTIAL).unlink(missing_ok=True)
    others = sorted(
        entry.name for entry in directory.iterdir() if entry.name != DESTINATION.name
    )
    if others:
        raise SystemExit(
            f"{directory} must hold proj.db alone; remove {', '.join(others)}"
        )
    staged = directory / _PARTIAL
    try:
        shutil.copyfile(database, staged)
        os.replace(staged, DESTINATION)
    finally:
        staged.unlink(missing_ok=True)


def main() -> None:
    argparse.ArgumentParser(description=__doc__.splitlines()[0]).parse_args()
    pins = read_pins()
    provenance = json.loads(PROVENANCE.read_text(encoding="utf-8"))
    with tempfile.TemporaryDirectory(prefix="geodetic-bundled-osdudb-") as work:
        database, reference = build(Path(work), pins)
        summary = check_database(
            database,
            provenance=provenance,
            proj_version=pins.proj_version,
            reference=reference,
        )
        publish(database)
    counts = summary["osdu_counts"]
    print(
        f"wrote {DESTINATION.relative_to(REPO_ROOT)}: "
        f"{DESTINATION.stat().st_size / 1e6:.1f} MB, PROJ {summary['proj_version']} "
        f"(layout {summary['layout']}), {summary['source_version']}, "
        f"{counts['geodetic_crs']} geodetic and {counts['projected_crs']} "
        "projected OSDU CRSs"
    )


if __name__ == "__main__":
    main()
