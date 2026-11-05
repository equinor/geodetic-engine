"""Build an organisation's register into a wheel its users pip install.

    python scripts/build_organisation_projdb.py --config geodetic-projdb.toml \\
        --name example-projdb --output dist

Using a register's CRSs and transformations needs the proj.db they were built
into, not the register: only the build needs Georepository credentials. This
builds that database once, as a wheel that everyone else installs from a
private package index, with no credentials of the register's:

1. A new virtual environment gets this checkout and the pyproj wheel
   .devcontainer/pins.env pins, as for scripts/build_bundled_osdudb.py, so the
   base database is that wheel's own proj.db: the EPSG content users install.
2. ``geodetic-projdb build`` imports the register --config describes, every
   response fetched afresh. ``geodetic-osdudb build --official --append`` adds
   the official OSDU catalogue, which users would otherwise lose: the database
   is read in place of the one the release wheel ships.
   scripts/patch-grid-alternatives.sh then adds the grid filename mappings it
   knows to be missing, as scripts/build-projdb.sh does.
3. The database must open read-only, carry the metadata of the wheel's own
   proj.db, record both builds as validated and started from it, and hold
   objects of every authority they imported.
4. --output gets a wheel named --name, versioned as the PROJ release and then
   the UTC minute of the build, ``<PROJ release>.<YYYYMMDDhhmm>``, so that
   ``<name>==<PROJ release>.*`` installs the newest build for that release. Its
   package holds proj_data/proj.db, alone in its directory, beside the build
   reports and proj.db.manifest.json, which states what the database holds. It
   names that directory under the entry point group geodetic_engine.proj_data,
   where geodetic_engine finds it.

Of the caller's environment, the build gets only what Python, pip, bash and
sqlite3 need to run and to reach the network, as clean_environment() in
scripts/build_bundled_osdudb.py lists it, and pip alone its PIP_ settings.
GEODETIC_ENGINE_GEOREP_CLIENT_ID and GEODETIC_ENGINE_GEOREP_CLIENT_SECRET
reach the register import, and nothing else: no other PROJ_ or
GEODETIC_ENGINE_ variable, and no other secret of the caller's, reaches the
build, and every other setting comes from --config. The wheel holds the
register's definitions: publish it only to an index that the register's
users, and no one else, can read. See docs/user-guide/sharing.md.

Needs the Python the package requires; network access to PyPI, the project's
pyproj index and the register; and, unless --skip-grid-patch, bash and sqlite3.
"""

from __future__ import annotations

import argparse
import base64
import csv
import hashlib
import io
import json
import keyword
import os
import re
import shutil
import sqlite3
import subprocess
import tempfile
import tomllib
import zipfile
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

try:
    from build_bundled_osdudb import (
        clean_environment,
        own_proj_db,
        pinned_environment,
        run,
    )
    from sync_pins import read_pins
except ImportError:  # imported as scripts.build_organisation_projdb, by the tests
    from scripts.build_bundled_osdudb import (
        clean_environment,
        own_proj_db,
        pinned_environment,
        run,
    )
    from scripts.sync_pins import read_pins

REPO_ROOT = Path(__file__).resolve().parents[1]
GRID_PATCH = REPO_ROOT / "scripts" / "patch-grid-alternatives.sh"
CREDENTIALS = (
    "GEODETIC_ENGINE_GEOREP_CLIENT_ID",
    "GEODETIC_ENGINE_GEOREP_CLIENT_SECRET",
)
DATABASE = "proj.db"
MANIFEST = "proj.db.manifest.json"
# Where geodetic_engine looks for an installed database; see _bundled_database.
ENTRY_POINT_GROUP = "geodetic_engine.proj_data"
_HISTORY_TABLE = "geodetic_engine_build_history"
_OSDU = "OSDU"
_WHEEL_TAG = "py3-none-any"
# A distribution name, as PEP 508 has it.
_NAME = re.compile(r"[A-Z0-9](?:[A-Z0-9._-]*[A-Z0-9])?", re.IGNORECASE)
_ZIP_TIME = (1980, 1, 1, 0, 0, 0)
_INIT = '''\
"""The proj.db {name} provides geodetic_engine, for PROJ {proj_version}."""

from pathlib import Path

PROJ_DATA = Path(__file__).resolve().parent / "proj_data"
'''


def read_credentials() -> dict[str, str]:
    """The Georepository credentials, from the caller's environment.

    Raises:
        SystemExit: If either is unset, before anything is built.
    """
    missing = [name for name in CREDENTIALS if not os.environ.get(name)]
    if missing:
        raise SystemExit(
            f"set {' and '.join(missing)} in the environment: from the secret "
            "store in CI, or locally from .env with `uv run --env-file .env "
            "python scripts/build_organisation_projdb.py ...`"
        )
    return {name: os.environ[name] for name in CREDENTIALS}


def module_name(name: str) -> str:
    """The package a distribution of this name installs.

    Raises:
        SystemExit: If no package can be imported by that name, or it is
            geodetic_engine's own.
    """
    module = re.sub(r"[-_.]+", "_", name).lower()
    if module in {"geodetic_engine", "pygeodetic_engine"}:
        raise SystemExit(
            f"--name {name!r} is geodetic-engine's own name, and installing the "
            "wheel would replace geodetic-engine: choose another"
        )
    if (
        not _NAME.fullmatch(name)
        or not module.isidentifier()
        or keyword.iskeyword(module)
    ):
        raise SystemExit(
            f"--name {name!r} names no package that can be imported: use "
            "letters, digits and hyphens, starting with a letter"
        )
    return module


def build(
    work: Path,
    pins: Any,
    config: Path,
    credentials: dict[str, str],
    *,
    grid_patch: bool,
) -> tuple[Path, Path]:
    """Build the register, then the official OSDU catalogue, into one proj.db.

    Returns:
        The database, and the proj.db it was built on.
    """
    python = pinned_environment(work / "venv", pins, str(REPO_ROOT))
    output = work / "output"
    output.mkdir()
    # Stop the CLIs' upward dotenv search, even if TMPDIR is under a .env.
    (output / ".env").touch()
    database = output / DATABASE
    # Run outside the checkout to import the installed package.
    environment = clean_environment() | {"GEODETIC_ENGINE_OSDU_DB": "0"}
    run(
        [
            str(python),
            "-m",
            "geodetic_engine.projdb",
            "build",
            "--config",
            str(config),
            "--output",
            str(database),
            "--no-cache",
        ],
        env=environment | credentials,
        cwd=output,
    )
    run(
        [
            str(python),
            "-m",
            "geodetic_engine.osdudb",
            "build",
            "--official",
            "--output",
            str(database),
            "--append",
        ],
        env=environment,
        cwd=output,
    )
    if grid_patch:
        run(["bash", str(GRID_PATCH), "--db", str(database)], env=environment)
    return database, own_proj_db(python)


def check_database(database: Path, reference: Path) -> dict[str, Any]:
    """Check a built database, opened read-only as PROJ opens it.

    Args:
        database: The database.
        reference: The pinned pyproj wheel's own proj.db, the build's base.

    Returns:
        The PROJ release it is for, what each authority contributed, and the
        builds that wrote it.

    Raises:
        ValueError: If it is not a register's build and then the official
            OSDU catalogue's, both validated and started from the reference,
            or cannot be read where it is installed.
    """
    with closing(_read_only(reference)) as connection:
        expected = dict(connection.execute("SELECT key, value FROM metadata"))
    with closing(_read_only(database)) as connection:
        # A WAL database needs a writable directory even to be read, and a
        # container image or a shared drive often is not one.
        (journal,) = connection.execute("PRAGMA journal_mode").fetchone()
        if str(journal).lower() == "wal":
            raise ValueError("it is in WAL mode")
        if connection.execute("PRAGMA quick_check").fetchone() != ("ok",):
            raise ValueError("it fails SQLite's integrity check")
        metadata = dict(connection.execute("SELECT key, value FROM metadata"))
        reports = [
            json.loads(report)
            for (report,) in connection.execute(
                f"SELECT report FROM {_HISTORY_TABLE} ORDER BY sequence"
            )
        ]
        counts = {
            kind: dict(
                connection.execute(
                    f"SELECT auth_name, COUNT(*) FROM {view} GROUP BY auth_name"
                )
            )
            for kind, view in (
                ("crs", "crs_view"),
                ("operations", "coordinate_operation_view"),
            )
        }

    differing = [
        f"{key}={metadata.get(key)!r}, not {value!r}"
        for key, value in sorted(expected.items())
        if metadata.get(key) != value
    ]
    if differing:
        raise ValueError(
            "its metadata differs from the pinned pyproj wheel's own proj.db: "
            + "; ".join(differing)
        )
    if len(reports) != 2 or reports[1].get("authorities") != [_OSDU]:
        raise ValueError(
            f"it records {len(reports)} builds, not a register's and then the "
            "official OSDU catalogue's"
        )
    for report in reports:
        built = ", ".join(report.get("authorities") or ["nothing"])
        if report.get("status") != "passed":
            raise ValueError(
                f"the build of {built} is {report.get('status')!r}, not 'passed'"
            )
        base = report.get("base_proj_db")
        if base is None or Path(base).resolve() != reference.resolve():
            raise ValueError(
                f"the build of {built} started from {base}, not the pinned "
                f"pyproj wheel's own {reference}"
            )
    authorities = sorted(
        {name for report in reports for name in report.get("authorities") or []}
    )
    empty = [
        name
        for name in authorities
        if not (counts["crs"].get(name) or counts["operations"].get(name))
    ]
    if empty:
        raise ValueError(f"it holds no CRS or operation of {', '.join(empty)}")
    return {
        "proj_version": metadata.get("PROJ.VERSION"),
        "epsg_version": metadata.get("EPSG.VERSION"),
        "database_layout": (
            f"{metadata.get('DATABASE.LAYOUT.VERSION.MAJOR')}."
            f"{metadata.get('DATABASE.LAYOUT.VERSION.MINOR')}"
        ),
        "authorities": {
            name: {
                "crs": counts["crs"].get(name, 0),
                "operations": counts["operations"].get(name, 0),
            }
            for name in authorities
        },
        "builds": [
            {
                "authorities": report.get("authorities"),
                "source_version": report.get("source_version"),
                "built_at": report.get("built_at"),
            }
            for report in reports
        ],
    }


def manifest(
    summary: dict[str, Any], database: Path, pins: Any, *, grid_patch: bool
) -> dict[str, Any]:
    """What a database holds, for whoever installs or publishes it."""
    with database.open("rb") as stream:
        digest = hashlib.file_digest(stream, "sha256").hexdigest()
    return {
        "proj_version": summary["proj_version"],
        "epsg_version": summary["epsg_version"],
        "database_layout": summary["database_layout"],
        "pyproj_requirement": pins.pyproj_requirement,
        "geodetic_engine_version": _project_version(),
        "geodetic_engine_commit": _commit(),
        "sha256": digest,
        "grid_alternatives_patched": grid_patch,
        "authorities": summary["authorities"],
        "builds": summary["builds"],
    }


def write_wheel(
    directory: Path,
    *,
    name: str,
    version: str,
    database: Path,
    record: dict[str, Any],
) -> Path:
    """Write a wheel that installs a database where geodetic_engine finds it.

    Args:
        directory: Where to write it.
        name: Its distribution name.
        version: Its version.
        database: The database; the reports its builds wrote beside it come
            along.
        record: The manifest.

    Returns:
        The wheel.
    """
    module = module_name(name)
    stem = f"{module}-{version}"
    info = f"{stem}.dist-info"
    proj_version = record["proj_version"]
    reports = sorted(database.parent.glob(f"{DATABASE}*.report.json"))
    files = [
        (
            f"{module}/__init__.py",
            _INIT.format(name=name, proj_version=proj_version).encode(),
        ),
        (f"{module}/proj_data/{DATABASE}", database.read_bytes()),
        *((f"{module}/{report.name}", report.read_bytes()) for report in reports),
        (f"{module}/{MANIFEST}", (json.dumps(record, indent=2) + "\n").encode()),
        (
            f"{info}/METADATA",
            (
                "Metadata-Version: 2.1\n"
                f"Name: {name}\n"
                f"Version: {version}\n"
                f"Summary: proj.db for geodetic-engine and PROJ {proj_version}, "
                f"with {', '.join(record['authorities'])}\n"
            ).encode(),
        ),
        (
            f"{info}/WHEEL",
            (
                "Wheel-Version: 1.0\n"
                f"Generator: geodetic-engine scripts/{Path(__file__).name}\n"
                "Root-Is-Purelib: true\n"
                f"Tag: {_WHEEL_TAG}\n"
            ).encode(),
        ),
        (
            f"{info}/entry_points.txt",
            f"[{ENTRY_POINT_GROUP}]\n{name} = {module}:PROJ_DATA\n".encode(),
        ),
    ]
    rows = [(path, f"sha256={_digest(data)}", str(len(data))) for path, data in files]
    rows.append((f"{info}/RECORD", "", ""))
    text = io.StringIO()
    csv.writer(text, lineterminator="\n").writerows(rows)
    files.append((f"{info}/RECORD", text.getvalue().encode()))

    wheel = directory / f"{stem}-{_WHEEL_TAG}.whl"
    descriptor, name_staged = tempfile.mkstemp(prefix=f".{wheel.name}.", dir=directory)
    os.close(descriptor)
    staged = Path(name_staged)
    try:
        with zipfile.ZipFile(staged, "w") as archive:
            for path, data in files:
                entry = zipfile.ZipInfo(path, _ZIP_TIME)
                entry.external_attr = 0o644 << 16
                archive.writestr(entry, data, zipfile.ZIP_DEFLATED)
        os.replace(staged, wheel)
    finally:
        staged.unlink(missing_ok=True)
    return wheel


def _digest(data: bytes) -> str:
    """A file's hash as a wheel's RECORD states it."""
    return base64.urlsafe_b64encode(hashlib.sha256(data).digest()).rstrip(b"=").decode()


def _read_only(database: Path) -> sqlite3.Connection:
    return sqlite3.connect(f"{database.resolve().as_uri()}?mode=ro", uri=True)


def _project_version() -> str:
    with (REPO_ROOT / "pyproject.toml").open("rb") as stream:
        return str(tomllib.load(stream)["project"]["version"])


def _commit() -> str | None:
    """The checkout's commit, marked when tracked files differ from it."""
    try:
        commit, changes = (
            subprocess.run(
                ["git", "-C", str(REPO_ROOT), *command],
                check=True,
                capture_output=True,
                text=True,
            ).stdout.strip()
            for command in (
                ["rev-parse", "HEAD"],
                ["status", "--porcelain", "--untracked-files=no"],
            )
        )
    except (OSError, subprocess.CalledProcessError):
        return None
    return f"{commit}-dirty" if changes else commit


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--config",
        type=Path,
        required=True,
        help=(
            "geodetic-projdb settings naming the register and its authorities; "
            "see geodetic-projdb.example.toml"
        ),
    )
    parser.add_argument(
        "--name",
        required=True,
        help=(
            "the wheel's distribution name, such as example-projdb; choose one "
            "that no public package index serves"
        ),
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("dist"),
        help="directory to write the wheel to; dist by default",
    )
    parser.add_argument(
        "--skip-grid-patch",
        action="store_true",
        help=(
            "leave out the grid filename mappings "
            "scripts/patch-grid-alternatives.sh adds"
        ),
    )
    args = parser.parse_args()
    config = args.config.resolve()
    output = args.output.resolve()
    grid_patch = not args.skip_grid_patch
    # Everything that can be refused is, before the build, which takes long.
    if not config.is_file():
        parser.error(f"{args.config} does not exist")
    if output.exists() and not output.is_dir():
        parser.error(f"{args.output} is not a directory")
    module_name(args.name)
    credentials = read_credentials()
    if grid_patch and (
        missing := [tool for tool in ("bash", "sqlite3") if not shutil.which(tool)]
    ):
        raise SystemExit(
            f"the grid patch needs {' and '.join(missing)}; install it, or pass "
            "--skip-grid-patch"
        )
    pins = read_pins()

    with tempfile.TemporaryDirectory(prefix="geodetic-organisation-projdb-") as work:
        database, reference = build(
            Path(work), pins, config, credentials, grid_patch=grid_patch
        )
        try:
            summary = check_database(database, reference)
        except ValueError as error:
            raise SystemExit(
                f"the built proj.db is not fit to share: {error}"
            ) from error
        record = manifest(summary, database, pins, grid_patch=grid_patch)
        version = f"{record['proj_version']}.{datetime.now(UTC):%Y%m%d%H%M}"
        output.mkdir(parents=True, exist_ok=True)
        wheel = write_wheel(
            output, name=args.name, version=version, database=database, record=record
        )

    contents = "; ".join(
        f"{name}: {count['crs']} CRSs, {count['operations']} operations"
        for name, count in record["authorities"].items()
    )
    print(
        f"wrote {wheel} for PROJ {record['proj_version']} (EPSG "
        f"{record['epsg_version']}, layout {record['database_layout']}): "
        f"{contents}. Publish it only to an index the register's users, and no "
        "one else, can read."
    )


if __name__ == "__main__":
    main()
