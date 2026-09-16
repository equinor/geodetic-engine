"""Reading the one thing PROJ's database knows but will not hand back.

proj.db has no bound CRS table. A bound CRS is stored as an ordinary
``geodetic_crs`` or ``projected_crs`` row whose ``text_definition`` holds the
whole ``BOUNDCRS`` WKT. PROJ honours that when it selects an operation --
``Equinor:1100001`` offers one candidate where plain ED50 offers thirty-five --
but the object it hands back has been unwrapped: ``is_bound`` is False,
``source_crs`` is None, and the PROJJSON carries no ``transformation`` node.

So a caller naming such a CRS by its code gets a CRS that cannot say which
operation it carries, and this package would then refuse the datum change as
ambiguous even though the CRS itself settles it. Reading the stored WKT back
out restores the object the database actually describes.

Only the ``BOUNDCRS`` definitions are read. Every other row is left to PROJ,
which is the authority on its own database.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import sqlite3
from collections.abc import Mapping
from contextlib import closing
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from threading import local

from pyproj import datadir

logger = logging.getLogger(__name__)

# CRS tables that carry a text_definition column. Checked against the database
# rather than assumed, since the layout differs between PROJ releases.
_CRS_TABLES = (
    "geodetic_crs",
    "projected_crs",
    "vertical_crs",
    "compound_crs",
    "engineering_crs",
)

_DATABASE_NAME = "proj.db"
_BOUND_KEYWORD = "BOUNDCRS"

# Where a build of this package records what it wrote and what it left out.
# Spelled here rather than imported from geodetic_engine.projdb, which depends
# on this package and must not be depended on in return.
_BUILD_HISTORY_TABLE = "geodetic_engine_build_history"

type DatabaseIdentity = tuple[tuple[str, int, int, int, int, int], ...]
_context = local()


def database_identity() -> DatabaseIdentity:
    """Identify the active search path and on-disk database generations."""
    entries = []
    for directory in datadir.get_data_dir().split(os.pathsep):
        path = (Path(directory) / _DATABASE_NAME).resolve()
        try:
            stat = path.stat()
        except OSError:
            entries.append((str(path), 0, 0, 0, 0, 0))
        else:
            entries.append(
                (
                    str(path),
                    stat.st_dev,
                    stat.st_ino,
                    stat.st_size,
                    stat.st_mtime_ns,
                    stat.st_ctime_ns,
                )
            )
    identity = tuple(entries)
    if getattr(_context, "identity", None) != identity:
        datadir.set_data_dir(datadir.get_data_dir())
        _context.identity = identity
    return identity


@lru_cache(maxsize=32)
def database_fingerprints(identity: DatabaseIdentity) -> tuple[tuple[str, str], ...]:
    """Return SHA-256 fingerprints of the databases in one resolution context."""
    found = []
    for path, _, _, size, _, _ in identity:
        if size:
            with Path(path).open("rb") as stream:
                found.append((path, hashlib.file_digest(stream, "sha256").hexdigest()))
    return tuple(found)


def bound_definition(auth_name: str, code: str) -> str | None:
    """The stored ``BOUNDCRS`` WKT for an authority code, if there is one.

    Args:
        auth_name: Authority of the CRS, for example ``"Equinor"``.
        code: Its code.

    Returns:
        The WKT, or None when the code names no CRS, names one that is not
        bound, or no readable database defines it.

    Example:
        >>> bound_definition("Equinor", "1100001")  # doctest: +SKIP
        'BOUNDCRS[SOURCECRS[GEOGCRS["ED50",...'
    """
    return _definitions(database_identity()).by_code.get(
        (auth_name.casefold(), str(code))
    )


def bound_definition_by_name(name: str) -> str | None:
    """The stored ``BOUNDCRS`` WKT going by a CRS's name.

    Needed because PROJ answers a bound CRS's own code with the *base* CRS,
    which reports the base's authority: ``Equinor:2100152`` comes back
    identifying itself as ``EPSG:26703``, so its code is no way back to the
    definition. The name survives that unwrapping.

    A name defined by more than one bound CRS is not resolved, since there
    would be no way to tell which was meant.

    Args:
        name: The CRS name, compared case insensitively.

    Returns:
        The WKT, or None when no single bound CRS goes by that name.
    """
    return _definitions(database_identity()).by_name.get(name.strip().casefold())


@dataclass(frozen=True, slots=True)
class _BoundDefinitions:
    """Bound CRS definitions, reachable by code and by name."""

    by_code: Mapping[tuple[str, str], str]
    by_name: Mapping[str, str]


@lru_cache(maxsize=8)
def _definitions(identity: DatabaseIdentity) -> _BoundDefinitions:
    """Every bound CRS definition in the databases PROJ is currently reading.

    Read once per data directory and kept, because the alternative is a query
    for every CRS resolved. Directories are searched in PROJ's own order, so
    the first database defining a code wins, exactly as PROJ resolves it.
    """
    found: dict[tuple[str, str], str] = {}
    names: dict[str, str] = {}
    for path, *_ in identity:
        database = Path(path)
        if not database.is_file():
            continue
        try:
            _read_into(found, names, database)
        except sqlite3.Error as error:
            # A database that cannot be read tells us nothing about bound CRSs;
            # it must not stop an ordinary CRS from resolving.
            logger.debug("could not read %s: %s", database, error)
        break
    if found:
        logger.debug("%d bound CRS definitions available", len(found))
    return _BoundDefinitions(by_code=found, by_name=names)


def skip_reason(definition: str) -> str | None:
    """Why the build left this CRS out, if that is why it cannot be resolved.

    An object the register defines but PROJ cannot represent is skipped when the
    database is built, with the reason recorded in the build history. Without
    this, naming such a CRS gives only PROJ's "unknown name", which is true but
    says nothing about a definition that was deliberately not written.

    Args:
        definition: What the caller named the CRS by, either its name or
            ``"AUTH:CODE"``.

    Returns:
        A sentence naming the object and the reason, or None when the build
        recorded no such skip.
    """
    return _skips(database_identity()).get(definition.strip().casefold())


@lru_cache(maxsize=8)
def _skips(identity: DatabaseIdentity) -> Mapping[str, str]:
    """Skipped objects of the databases PROJ is reading, by name and by code.

    Every build appends its own report, so a database written by more than one
    source carries more than one; later reports win, being the later word on
    what the database now holds.
    """
    found: dict[str, str] = {}
    for path, *_ in identity:
        database = Path(path)
        if not database.is_file():
            continue
        try:
            _read_skips_into(found, database)
        except (sqlite3.Error, ValueError) as error:
            # No build history, or an unreadable one, only means this database
            # cannot explain itself; it must not stop a CRS from resolving.
            logger.debug("could not read build history of %s: %s", database, error)
        break
    return found


def _read_skips_into(found: dict[str, str], database: Path) -> None:
    """Collect the skipped objects recorded in one database's build history."""
    with closing(sqlite3.connect(f"file:{database}?mode=ro", uri=True)) as connection:
        if not _has_table(connection, _BUILD_HISTORY_TABLE):
            return
        reports = connection.execute(
            f"SELECT report FROM {_BUILD_HISTORY_TABLE} ORDER BY sequence"
        )
        for (report,) in reports:
            for entry in json.loads(report).get("skipped") or ():
                if not isinstance(entry, dict) or not entry.get("reason"):
                    continue
                identifier = f"{entry.get('auth_name')}:{entry.get('code')}"
                explanation = (
                    f"the build that wrote this database left out {identifier} "
                    f"because {entry['reason']}"
                )
                for key in (entry.get("name"), identifier):
                    if key:
                        found[str(key).strip().casefold()] = explanation


def _has_table(connection: sqlite3.Connection, table: str) -> bool:
    """Whether a table exists in this database."""
    return bool(
        connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
        ).fetchone()
    )


def _read_into(
    found: dict[tuple[str, str], str], names: dict[str, str], database: Path
) -> None:
    """Collect the bound CRS rows of one database into ``found`` and ``names``."""
    ambiguous: set[str] = set()
    with closing(sqlite3.connect(f"file:{database}?mode=ro", uri=True)) as connection:
        for table in _CRS_TABLES:
            if not _has_text_definition(connection, table):
                continue
            rows = connection.execute(
                f"SELECT auth_name, code, name, text_definition FROM {table} "
                "WHERE text_definition IS NOT NULL"
            )
            for auth_name, code, name, text in rows:
                if not str(text).lstrip().upper().startswith(_BOUND_KEYWORD):
                    continue
                found.setdefault((str(auth_name).casefold(), str(code)), str(text))
                if not name:
                    continue
                key = str(name).strip().casefold()
                if key in names and names[key] != str(text):
                    ambiguous.add(key)
                names.setdefault(key, str(text))
    for key in ambiguous:
        del names[key]


def _has_text_definition(connection: sqlite3.Connection, table: str) -> bool:
    """Whether a table exists in this database and carries a text definition."""
    columns = connection.execute(f"PRAGMA table_info({table})").fetchall()
    return any(column[1] == "text_definition" for column in columns)
