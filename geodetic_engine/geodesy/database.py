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
import logging
import os
import sqlite3
from collections.abc import Mapping
from contextlib import closing
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
    return _definitions(database_identity()).get((auth_name.casefold(), str(code)))


@lru_cache(maxsize=8)
def _definitions(identity: DatabaseIdentity) -> Mapping[tuple[str, str], str]:
    """Every bound CRS definition in the databases PROJ is currently reading.

    Read once per data directory and kept, because the alternative is a query
    for every CRS resolved. Directories are searched in PROJ's own order, so
    the first database defining a code wins, exactly as PROJ resolves it.
    """
    found: dict[tuple[str, str], str] = {}
    for path, *_ in identity:
        database = Path(path)
        if not database.is_file():
            continue
        try:
            _read_into(found, database)
        except sqlite3.Error as error:
            # A database that cannot be read tells us nothing about bound CRSs;
            # it must not stop an ordinary CRS from resolving.
            logger.debug("could not read %s: %s", database, error)
        break
    if found:
        logger.debug("%d bound CRS definitions available", len(found))
    return found


def _read_into(found: dict[tuple[str, str], str], database: Path) -> None:
    """Collect the bound CRS rows of one database into ``found``."""
    with closing(sqlite3.connect(f"file:{database}?mode=ro", uri=True)) as connection:
        for table in _CRS_TABLES:
            if not _has_text_definition(connection, table):
                continue
            rows = connection.execute(
                f"SELECT auth_name, code, text_definition FROM {table} "
                "WHERE text_definition IS NOT NULL"
            )
            for auth_name, code, text in rows:
                if str(text).lstrip().upper().startswith(_BOUND_KEYWORD):
                    found.setdefault((str(auth_name).casefold(), str(code)), str(text))


def _has_text_definition(connection: sqlite3.Connection, table: str) -> bool:
    """Whether a table exists in this database and carries a text definition."""
    columns = connection.execute(f"PRAGMA table_info({table})").fetchall()
    return any(column[1] == "text_definition" for column in columns)
