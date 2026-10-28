"""Build the OSDU proj.db the release wheel ships.

    python scripts/build_bundled_osdudb.py

Runs ``geodetic-osdudb build --official`` in a new virtual environment holding
this checkout and the pinned pyproj wheel from the project's index, so the base
database is that wheel's own proj.db: exactly the EPSG content users install.
Nothing of the calling environment's PROJ setup reaches the build. The result
is checked as scripts/check_package_dist.py checks a release, then written to
geodetic_engine/osdudb/_data/proj_data/proj.db, alone in its directory, where
the build backend includes it in the sdist and the wheel. It is gitignored.

The release workflow runs this before ``uv build``. Needs network access to
PyPI and to the project's pyproj index, and the Python the package requires.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import tempfile
import venv
from pathlib import Path

from check_package_dist import check_database

REPO_ROOT = Path(__file__).resolve().parents[1]
DESTINATION = REPO_ROOT / "geodetic_engine/osdudb/_data/proj_data/proj.db"
CATALOG = REPO_ROOT / "catalogs/osdu/CRS_CT_osdu_official_crs_catalog.json"
PROVENANCE = CATALOG.with_suffix(".source.json")
ENVIRONMENT = REPO_ROOT / "geodetic_engine/_environment.py"
# Left behind only when a run is interrupted while publishing.
_PARTIAL = ".proj.db.partial"


def _pin(name: str) -> str:
    match = re.search(
        rf'^{name}: Final = "([^"]+)"$',
        ENVIRONMENT.read_text(encoding="utf-8"),
        re.MULTILINE,
    )
    if match is None:
        raise SystemExit(f"{ENVIRONMENT} sets no {name}")
    return match.group(1)


def _environment() -> dict[str, str]:
    """The calling environment without anything that would steer the build."""
    removed = {"PROJ_DATA", "PROJ_LIB", "PYTHONPATH", "VIRTUAL_ENV"}
    environment = {
        key: value
        for key, value in os.environ.items()
        if key not in removed and not key.startswith("GEODETIC_ENGINE_")
    }
    # The build must start from the wheel's stock database, not a bundled one.
    environment["GEODETIC_ENGINE_OSDU_DB"] = "0"
    environment["PIP_DISABLE_PIP_VERSION_CHECK"] = "1"
    return environment


def _run(command: list[str], *, env: dict[str, str], cwd: Path | None = None) -> None:
    print("+", " ".join(command), flush=True)
    subprocess.run(command, env=env, cwd=cwd, check=True)


def build(work: Path) -> Path:
    """Build the official catalogue in a new environment; return the database."""
    environment = _environment()
    venv.create(work / "venv", with_pip=True)
    python = work / "venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    pyproj = "pyproj=={}+proj{}".format(
        _pin("PINNED_PYPROJ_VERSION"), _pin("PINNED_PROJ_VERSION")
    )
    _run(
        [
            str(python),
            "-m",
            "pip",
            "install",
            "--quiet",
            "--only-binary",
            "pyproj",
            "--extra-index-url",
            _pin("PYPROJ_WHEEL_INDEX"),
            str(REPO_ROOT),
            pyproj,
        ],
        env=environment,
    )
    output = work / "output"
    output.mkdir()
    # Run elsewhere than the checkout: no geodetic-osdudb.toml or .env of the
    # developer's is found, and the installed package, not the source tree,
    # is imported.
    _run(
        [
            str(python),
            "-m",
            "geodetic_engine.osdudb",
            "build",
            "--official",
            "--output",
            str(output / "proj.db"),
        ],
        env=environment,
        cwd=output,
    )
    return output / "proj.db"


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
    provenance = json.loads(PROVENANCE.read_text(encoding="utf-8"))
    with tempfile.TemporaryDirectory(prefix="geodetic-bundled-osdudb-") as work:
        database = build(Path(work))
        summary = check_database(
            database, provenance=provenance, proj_version=_pin("PINNED_PROJ_VERSION")
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
