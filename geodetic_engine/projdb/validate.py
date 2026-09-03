"""Validation of a built custom proj.db.

This validates the *database*, not the transformations a caller might one day
ask for. Two questions are asked of every object that was imported: is the file
structurally sound, and can PROJ construct this object from it?

Deliberately not asked here:

* **Whether a grid file is installed.** A grid transformation is a correct
  database entry whether or not the grid is on this machine, and the grid may
  well be installed somewhere else, or fetched over the network, by whatever
  consumes the database. Grid availability is reported, never enforced.
* **Whether a CRS reaches WGS 84 without a ballpark step.** That is a property
  of geodesy, not of the database. ETRS89 and WGS 84 are separate ensembles
  with no operation between them, so an ETRS89-based CRS is legitimately
  ballpark-only to WGS 84 and is not a defect. Refusing ballpark results
  belongs at transformation time, in the API that serves coordinates.

Objects are constructed through :class:`pyproj.crs.CoordinateOperation` and
:class:`pyproj.CRS` rather than by building a ``Transformer``. A Transformer
needs grids and a target CRS; constructing the object needs only the database,
which is what is under test.
"""

from __future__ import annotations

import logging
import os
import shutil
import sqlite3
import tempfile
from collections.abc import Generator, Iterable, Sequence
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from geodetic_engine.projdb.errors import ProjDbBuildError

logger = logging.getLogger(__name__)

_CRS_TABLES = (
    "geodetic_crs",
    "projected_crs",
    "vertical_crs",
    "engineering_crs",
    "compound_crs",
)
_OPERATION_TABLES = (
    "conversion_table",
    "helmert_transformation_table",
    "grid_transformation",
    "other_transformation",
    "concatenated_operation",
)


def validate(
    database: Path,
    *,
    authorities: Iterable[str],
    imported: Iterable[tuple[str, str, str]] | None = None,
) -> dict[str, Any]:
    """Check that a built database is sound and that PROJ can read every object.

    Args:
        database: Path to the enriched proj.db.
        authorities: Custom authority names whose objects should be checked.
        imported: The objects this build actually added, as
            ``(table, auth_name, code)`` triples. When given, only these are
            constructed. A build that imports objects under an established
            authority such as EPSG would otherwise be judged on the whole of
            that authority's stock content, and the EPSG dataset PROJ ships
            with contains a handful of operations PROJ itself cannot build.

    Returns:
        A summary for the build report: how many CRSs and operations were
        constructed, and every grid the imported operations reference with
        whether PROJ can currently find it.

    Raises:
        ProjDbBuildError: If the file is corrupt, has dangling references, or
            contains an object PROJ cannot construct.

    Example:
        >>> validate(Path("build/proj.db"), authorities=["Example"])  # doctest: +SKIP
        {'crs_checked': 195, 'operations_checked': 191, 'grids': [...]}
    """
    authority_list = sorted(authorities)
    logger.info("validating %s for %s", database, authority_list)

    with sqlite3.connect(f"file:{database}?mode=ro", uri=True) as connection:
        _check_integrity(connection)
        _check_foreign_keys(connection)
        logger.info("integrity and foreign key checks passed")
        if imported is None:
            crs_keys = _custom_objects(connection, _CRS_TABLES, authority_list)
            operation_keys = _custom_objects(
                connection, _OPERATION_TABLES, authority_list
            )
        else:
            keys = list(imported)
            crs_keys = [key for key in keys if key[0] in _CRS_TABLES]
            operation_keys = [key for key in keys if key[0] in _OPERATION_TABLES]

    with _proj_data(database):
        _check_crs(crs_keys)
        grids = _check_operations(operation_keys)

    missing = [grid["name"] for grid in grids if not grid["available"]]
    if missing:
        logger.warning(
            "%d of %d referenced grid files are not installed here: %s. The "
            "transformations are in the database regardless; install the grids "
            "wherever the database is used, or let PROJ fetch them.",
            len(missing),
            len(grids),
            missing,
        )

    return {
        "crs_checked": len(crs_keys),
        "operations_checked": len(operation_keys),
        "grids": grids,
        "missing_grids": missing,
    }


def _check_integrity(connection: sqlite3.Connection) -> None:
    result = connection.execute("PRAGMA integrity_check").fetchone()
    if not result or result[0] != "ok":
        raise ProjDbBuildError(f"SQLite integrity check failed: {result}")


def _check_foreign_keys(connection: sqlite3.Connection) -> None:
    violations = connection.execute("PRAGMA foreign_key_check").fetchall()
    if violations:
        sample = "; ".join(
            f"{row[0]} rowid={row[1]} -> {row[2]}" for row in violations[:5]
        )
        raise ProjDbBuildError(
            f"{len(violations)} foreign key violations in the built database: {sample}"
        )


def _custom_objects(
    connection: sqlite3.Connection,
    tables: Sequence[str],
    authorities: Sequence[str],
) -> list[tuple[str, str, str]]:
    placeholders = ", ".join("?" * len(authorities))
    found: list[tuple[str, str, str]] = []
    for table in tables:
        rows = connection.execute(
            f"SELECT auth_name, code FROM {table} WHERE auth_name IN ({placeholders})",
            tuple(authorities),
        )
        found.extend((table, str(auth), str(code)) for auth, code in rows)
    return found


@contextmanager
def _proj_data(database: Path) -> Generator[None]:
    """Point PROJ at the built database for the duration of the block.

    PROJ opens the file called ``proj.db`` in each directory of its search
    path, so a database under any other name is staged in a temporary
    directory under that name first. Without this, PROJ would quietly read the
    installed database instead and the build would be checked against the wrong
    file.
    """
    from pyproj import datadir

    previous_env = os.environ.get("PROJ_DATA")
    previous_dir = datadir.get_data_dir()
    with _as_proj_db(database) as directory:
        # The custom database must be found ahead of the installed one, but
        # PROJ still needs the installed directory for grids and proj.ini.
        search = os.pathsep.join([str(directory), previous_dir])
        os.environ["PROJ_DATA"] = search
        datadir.set_data_dir(search)
        try:
            yield
        finally:
            datadir.set_data_dir(previous_dir)
            if previous_env is None:
                os.environ.pop("PROJ_DATA", None)
            else:
                os.environ["PROJ_DATA"] = previous_env


@contextmanager
def _as_proj_db(database: Path) -> Generator[Path]:
    """Yield a directory in which the database is called ``proj.db``.

    The database itself is used where it is already named that way; otherwise
    it is linked, or copied when linking is not possible, into a temporary
    directory that is removed afterwards.
    """
    resolved = database.resolve()
    if resolved.name == "proj.db":
        yield resolved.parent
        return
    with tempfile.TemporaryDirectory(prefix="geodetic-projdb-") as staging:
        staged = Path(staging) / "proj.db"
        try:
            os.link(resolved, staged)
        except OSError:
            shutil.copyfile(resolved, staged)
        logger.debug("staged %s as %s for PROJ to read", resolved, staged)
        yield Path(staging)


def _check_crs(keys: Sequence[tuple[str, str, str]]) -> None:
    """Construct every imported CRS through PROJ."""
    from pyproj import CRS
    from pyproj.exceptions import CRSError

    logger.info("constructing %d imported CRS", len(keys))
    failures: list[str] = []
    for table, auth, code in keys:
        try:
            CRS.from_authority(auth, code)
        except CRSError as exc:
            failures.append(f"{auth}:{code} ({table}): {exc}")

    if failures:
        logger.error("CRS that PROJ could not construct: %s", failures)
        raise ProjDbBuildError(
            f"{len(failures)} imported CRS could not be constructed by PROJ: "
            f"{failures[:5]}. The full list is in the build log."
        )


def _check_operations(keys: Sequence[tuple[str, str, str]]) -> list[dict[str, Any]]:
    """Construct every imported operation and collect the grids they reference.

    Constructing the operation exercises its method, parameters and units as
    stored, which is what a build can be wrong about. It does not require the
    grids to be present, so a grid-based transformation is validated on the
    same terms as any other.
    """
    from pyproj.crs import CoordinateOperation
    from pyproj.exceptions import CRSError

    logger.info("constructing %d imported coordinate operations", len(keys))
    failures: list[str] = []
    grids: dict[str, dict[str, Any]] = {}
    for table, auth, code in keys:
        try:
            operation = CoordinateOperation.from_authority(auth, code)
        except (CRSError, RuntimeError) as exc:
            failures.append(f"{auth}:{code} ({table}): {exc}")
            continue
        for grid in operation.grids:
            entry = grids.setdefault(
                grid.short_name,
                {
                    "name": grid.short_name,
                    "full_name": grid.full_name or None,
                    "package_name": grid.package_name or None,
                    "url": grid.url or None,
                    "available": grid.available,
                    "used_by": [],
                },
            )
            entry["used_by"].append(f"{auth}:{code}")

    if failures:
        logger.error("operations that PROJ could not construct: %s", failures)
        raise ProjDbBuildError(
            f"{len(failures)} imported coordinate operations could not be "
            f"constructed by PROJ: {failures[:5]}. The full list is in the "
            "build log."
        )
    return [grids[name] for name in sorted(grids)]
