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

import logging
import os
import sqlite3
from collections.abc import Mapping
from functools import lru_cache
from pathlib import Path

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
    return _definitions(datadir.get_data_dir()).get((auth_name.casefold(), str(code)))


@lru_cache(maxsize=8)
def _definitions(data_dir: str) -> Mapping[tuple[str, str], str]:
    """Every bound CRS definition in the databases PROJ is currently reading.

    Read once per data directory and kept, because the alternative is a query
    for every CRS resolved. Directories are searched in PROJ's own order, so
    the first database defining a code wins, exactly as PROJ resolves it.
    """
    found: dict[tuple[str, str], str] = {}
    for directory in data_dir.split(os.pathsep):
        database = Path(directory) / _DATABASE_NAME
        if not database.is_file():
            continue
        try:
            _read_into(found, database)
        except sqlite3.Error as error:
            # A database that cannot be read tells us nothing about bound CRSs;
            # it must not stop an ordinary CRS from resolving.
            logger.debug("could not read %s: %s", database, error)
    if found:
        logger.debug("%d bound CRS definitions available", len(found))
    return found


def _read_into(found: dict[tuple[str, str], str], database: Path) -> None:
    """Collect the bound CRS rows of one database into ``found``."""
    with sqlite3.connect(f"file:{database}?mode=ro", uri=True) as connection:
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
