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

import hashlib
import json
import logging
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
from collections.abc import Generator, Iterable, Sequence
from contextlib import closing, contextmanager
from pathlib import Path
from typing import Any

from geodetic_engine._sqlite import read_only
from geodetic_engine.projdb.errors import ProjDbBuildError
from geodetic_engine.projdb.schema import (
    IDENTIFIER_TABLE,
    PERSISTABLE_REFERENCE_TABLE,
    identifier_alias_source,
)

logger = logging.getLogger(__name__)

# Which tables, in object_view vocabulary, the object a persistableReference's
# record names may be in, by the kind of definition the reference states.
_REFERENCE_KIND_TABLES: dict[str, tuple[str, ...]] = {
    "LBC": (
        "geodetic_crs",
        "projected_crs",
        "vertical_crs",
        "compound_crs",
        "engineering_crs",
        "derived_projected_crs",
    ),
    "ST": (
        "conversion",
        "helmert_transformation",
        "grid_transformation",
        "other_transformation",
        "concatenated_operation",
    ),
    "USO": ("unit_of_measure",),
}
_REFERENCE_KIND_TABLES["EBC"] = _REFERENCE_KIND_TABLES["LBC"]
_REFERENCE_KIND_TABLES["CT"] = _REFERENCE_KIND_TABLES["ST"]
_REFERENCE_KIND_TABLES["UAD"] = _REFERENCE_KIND_TABLES["USO"]

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
    from pyproj import datadir

    # Staged here, not in the worker: PROJ keeps the file open until the worker
    # exits, and Windows cannot remove a file that is still open.
    try:
        with _as_proj_db(database) as directory:
            request = {
                "database": str(directory / "proj.db"),
                "authorities": list(authorities),
                "imported": None if imported is None else list(imported),
                "data_dir": datadir.get_data_dir(),
            }
            process = subprocess.run(
                [sys.executable, "-m", "geodetic_engine.projdb._validate_worker"],
                input=json.dumps(request),
                capture_output=True,
                text=True,
                check=False,
            )
        response = json.loads(process.stdout)
    except (OSError, ValueError) as error:
        raise ProjDbBuildError(f"cannot validate {database}: {error}") from error
    if process.returncode or "error" in response:
        raise ProjDbBuildError(str(response.get("error") or process.stderr))
    if process.stderr.strip():
        logger.warning("%s", process.stderr.strip())
    return dict(response)


def _validate_in_process(
    database: Path,
    *,
    authorities: Iterable[str],
    imported: Iterable[tuple[str, str, str]] | None = None,
) -> dict[str, Any]:
    """Validate within the worker's isolated PROJ context."""
    authority_list = sorted(authorities)
    logger.info("validating %s for %s", database, authority_list)

    with closing(read_only(database)) as connection:
        _check_integrity(connection)
        _check_foreign_keys(connection)
        identifiers = _check_identifiers(connection)
        references = _check_persistable_references(connection)
        logger.info(
            "integrity, foreign key, identifier and persistable reference checks passed"
        )
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
        "identifiers_checked": identifiers,
        "persistable_references_checked": references,
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


def _check_identifiers(connection: sqlite3.Connection) -> int:
    """Check every recorded identifier names an object the database defines.

    The table's insert trigger already refuses a dangling row, but nothing
    stops a later build replacing or removing the object one names. An
    identifier alias must also still agree with the identifier it mirrors, or
    PROJ's name search would resolve the identifier to a different object
    than this package does.

    Returns:
        How many identifiers were checked; 0 when the database records none.
    """
    if not connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
        (IDENTIFIER_TABLE,),
    ).fetchone():
        return 0
    dangling = connection.execute(
        f"SELECT i.scheme, i.identifier, i.table_name, i.auth_name, i.code "
        f"FROM {IDENTIFIER_TABLE} i WHERE NOT EXISTS (SELECT 1 FROM object_view o "
        "WHERE o.table_name = i.table_name AND o.auth_name = i.auth_name "
        "AND o.code = i.code)"
    ).fetchall()
    if dangling:
        sample = "; ".join(
            f"{scheme} {identifier!r} -> {table} {auth}:{code}"
            for scheme, identifier, table, auth, code in dangling[:5]
        )
        raise ProjDbBuildError(
            f"{len(dangling)} identifiers name objects the database does not "
            f"define: {sample}"
        )
    stray: list[str] = []
    schemes = [
        str(scheme)
        for (scheme,) in connection.execute(
            f"SELECT DISTINCT scheme FROM {IDENTIFIER_TABLE}"
        )
    ]
    for scheme in schemes:
        rows = connection.execute(
            "SELECT a.alt_name, a.table_name, a.auth_name, a.code FROM alias_name a "
            f"WHERE a.source = ? AND NOT EXISTS (SELECT 1 FROM {IDENTIFIER_TABLE} i "
            "WHERE i.scheme = ? AND i.identifier = a.alt_name "
            "AND i.table_name = a.table_name AND i.auth_name = a.auth_name "
            "AND i.code = a.code)",
            (identifier_alias_source(scheme), scheme),
        )
        stray.extend(
            f"{alias!r} -> {table} {auth}:{code}" for alias, table, auth, code in rows
        )
    if stray:
        raise ProjDbBuildError(
            f"{len(stray)} identifier aliases disagree with the identifiers they "
            f"mirror: {'; '.join(stray[:5])}"
        )
    (count,) = connection.execute(f"SELECT COUNT(*) FROM {IDENTIFIER_TABLE}").fetchone()
    return int(count)


def _check_persistable_references(connection: sqlite3.Connection) -> int:
    """Check every recorded persistableReference is intact and fits its object.

    The foreign key to the identifier table, checked with every other foreign
    key, already ties each reference to a recorded id. What it cannot catch is
    a payload changed after it was written without its digest, or a reference
    stating a CRS for an id that names a transformation: either way a caller
    would be handed something other than what the catalogue published for
    that object.

    Returns:
        How many references were checked; 0 when the database records none.
    """
    if not connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
        (PERSISTABLE_REFERENCE_TABLE,),
    ).fetchone():
        return 0
    altered: list[str] = []
    mismatched: list[str] = []
    count = 0
    rows = connection.execute(
        "SELECT r.scheme, r.identifier, r.kind, r.sha256, r.persistable_reference, "
        f"i.table_name FROM {PERSISTABLE_REFERENCE_TABLE} r "
        f"LEFT JOIN {IDENTIFIER_TABLE} i "
        "ON i.scheme = r.scheme AND i.identifier = r.identifier"
    )
    for scheme, identifier, kind, digest, payload, table in rows:
        count += 1
        actual = hashlib.sha256(str(payload).encode("utf-8")).hexdigest()
        if actual != digest:
            altered.append(f"{scheme} {identifier!r}")
        if table is not None and table not in _REFERENCE_KIND_TABLES.get(kind, ()):
            mismatched.append(f"{scheme} {identifier!r}: {kind} for a {table}")
    if altered:
        raise ProjDbBuildError(
            f"{len(altered)} persistable references no longer match the digest "
            f"recorded with them: {'; '.join(altered[:5])}"
        )
    if mismatched:
        raise ProjDbBuildError(
            f"{len(mismatched)} persistable references state a different kind "
            f"of object than the one their id names: {'; '.join(mismatched[:5])}"
        )
    return count


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
    it is linked, or copied when linking is not possible or on Windows, into a
    temporary directory that is removed afterwards.
    """
    resolved = database.resolve()
    if resolved.name == "proj.db":
        yield resolved.parent
        return
    with tempfile.TemporaryDirectory(prefix="geodetic-projdb-") as staging:
        staged = Path(staging) / "proj.db"
        # Windows refuses to remove a link to a file the writer still has open.
        if os.name == "nt":
            shutil.copyfile(resolved, staged)
        else:
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
