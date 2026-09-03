"""Transactional writer for the enriched PROJ database.

Three rules distinguish this from a naive bulk insert, and each exists because
its absence produces a database that looks fine and is wrong:

* Rows are written with ``INSERT``, never ``INSERT OR REPLACE``. Replacing lets
  a custom object silently overwrite an EPSG or PROJ definition. The one
  opt-in exception, ``overwrite_existing``, still cannot reach another
  authority's rows: the per-row authority guard runs first, and every object
  table is keyed on ``(auth_name, code)``, so a replacement can only ever land
  on a row this build's own authorities already own.
* Every row's ``auth_name`` is checked against the configured custom
  authorities before it is bound.
* The whole build is one transaction. A partially enriched database is worse
  than no database, because it is still loadable.

A build normally starts from a fresh copy of the official proj.db. With
``append`` it starts from an existing output instead, so a second source can
add its authority to a database a first source already enriched -- a
Georepository build followed by an OSDU build into one file. Appending changes
what failure means: the output is no longer this build's to delete, so a failed
append rolls the transaction back and leaves the database as it found it.
"""

from __future__ import annotations

import logging
import shutil
import sqlite3
from collections import Counter
from collections.abc import Mapping, Sequence
from pathlib import Path
from types import TracebackType
from typing import Any

from geodetic_engine.projdb.errors import ForeignAuthorityCollision
from geodetic_engine.projdb.schema import (
    AUTHORITY_COLUMN,
    FOREIGN_AUTHORITY_ALLOWED,
    TABLE_COLUMNS,
    verify_schema,
)
from geodetic_engine.projdb.settings import DatabaseSettings

logger = logging.getLogger(__name__)

Row = Mapping[str, Any]


class ProjDbWriter:
    """Writes custom authority rows into a copy of the official proj.db.

    The base database is copied rather than modified, so the PROJ installation
    is never mutated and the copy inherits the upstream schema including its
    views and triggers.

    Example:
        >>> with ProjDbWriter(config) as writer:  # doctest: +SKIP
        ...     writer.insert("ellipsoid", rows)
        ...     writer.commit()
    """

    def __init__(self, config: DatabaseSettings) -> None:
        self._config = config
        self._connection: sqlite3.Connection | None = None
        self._committed = False
        self._appended = False
        self.inserted: Counter[str] = Counter()

    def __enter__(self) -> ProjDbWriter:
        self.open()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        if exc_type is not None or not self._committed:
            self.rollback()
        elif self._connection is not None:
            self._connection.close()
            self._connection = None

    @property
    def connection(self) -> sqlite3.Connection:
        """The open connection to the output database."""
        if self._connection is None:
            raise RuntimeError("writer is not open")
        return self._connection

    @property
    def appended(self) -> bool:
        """Whether this build added to an existing database rather than a copy."""
        return self._appended

    def open(self) -> None:
        """Open the output database and begin the build transaction.

        The base database is copied to the output first, unless ``append`` is
        configured and an output already exists, in which case that output is
        opened and added to. Appending to a path that does not exist yet is not
        an error: the first build of a chain has nothing to append to, so it
        copies the base like any other.

        Raises:
            SchemaDriftError: If the database opened is not the expected schema.
        """
        output = self._config.output_db
        output.parent.mkdir(parents=True, exist_ok=True)
        self._appended = self._config.append and output.is_file()
        if not self._appended:
            shutil.copyfile(self._config.base_proj_db, output)

        connection = sqlite3.connect(output, isolation_level=None)
        connection.execute("PRAGMA foreign_keys = ON")
        verify_schema(connection)
        connection.execute("BEGIN IMMEDIATE")
        self._connection = connection
        if self._appended:
            logger.info("appending to the existing database at %s", output)
        else:
            logger.info("copied %s to %s", self._config.base_proj_db, output)

    def existing_keys(self, table: str) -> set[tuple[str, str]]:
        """Return the ``(auth_name, code)`` pairs already present in a table.

        Used to import only objects the official database does not already
        define.

        Args:
            table: A table listed in ``schema.TABLE_COLUMNS``.

        Returns:
            Set of key pairs, with codes normalised to strings.
        """
        _assert_known_table(table)
        return {
            (str(auth), str(code))
            for auth, code in self.connection.execute(
                f"SELECT auth_name, code FROM {table}"
            )
        }

    def insert(self, table: str, rows: Sequence[Row]) -> int:
        """Insert rows into a table, one statement per row.

        Args:
            table: Target table, which must appear in ``TABLE_COLUMNS``.
            rows: Mappings keyed by column name. Missing columns bind to NULL.

        Returns:
            The number of rows inserted.

        Raises:
            ForeignAuthorityCollision: If a row belongs to an authority that is
                not configured as custom, or collides with an existing row and
                ``overwrite_existing`` is not configured.
        """
        _assert_known_table(table)
        if not rows:
            return 0

        columns = TABLE_COLUMNS[table]
        if table not in FOREIGN_AUTHORITY_ALLOWED:
            self._guard_authorities(table, rows)

        verb = "INSERT OR REPLACE" if self._config.overwrite_existing else "INSERT"
        statement = (
            f"{verb} INTO {table} ({', '.join(columns)}) "
            f"VALUES ({', '.join('?' * len(columns))})"
        )
        for row in rows:
            values = tuple(row.get(column) for column in columns)
            try:
                self.connection.execute(statement, values)
            except sqlite3.IntegrityError as exc:
                identity = {
                    key: row.get(key)
                    for key in ("auth_name", "code", "name")
                    if key in row
                }
                raise ForeignAuthorityCollision(
                    f"could not insert into {table}: {exc}. Row: {identity}. "
                    "The build is aborted rather than overwriting an existing "
                    "definition."
                ) from exc

        self.inserted[table] += len(rows)
        return len(rows)

    def upsert_authority_preferences(self, rows: Sequence[Row]) -> int:
        """Replace rows in ``authority_to_authority_preference``.

        This is the one table where an existing row is legitimately rewritten
        rather than added to: its key is a pair of authority names, not an
        object, so extending PROJ's shipped preference for a CRS pair means
        replacing that row. It is kept separate from :meth:`insert` so that
        replacement stays impossible everywhere else.

        Args:
            rows: Mappings with ``source_auth_name``, ``target_auth_name`` and
                ``allowed_authorities``.

        Returns:
            The number of rows written.
        """
        if not rows:
            return 0
        table = "authority_to_authority_preference"
        columns = TABLE_COLUMNS[table]
        placeholders = ", ".join("?" * len(columns))
        statement = (
            f"INSERT OR REPLACE INTO {table} ({', '.join(columns)}) "
            f"VALUES ({placeholders})"
        )
        self.connection.executemany(
            statement, [tuple(row.get(column) for column in columns) for row in rows]
        )
        self.inserted[table] += len(rows)
        return len(rows)

    def _guard_authorities(self, table: str, rows: Sequence[Row]) -> None:
        column = AUTHORITY_COLUMN.get(table, "auth_name")
        allowed = {name.casefold() for name in self._config.authorities}
        for row in rows:
            auth = str(row.get(column) or "")
            if auth.casefold() not in allowed:
                raise ForeignAuthorityCollision(
                    f"refusing to write {table} row with {column}={auth!r} "
                    f"because it is not one of the configured custom "
                    f"authorities {sorted(self._config.authorities)}. Only "
                    "custom authority objects may be added to proj.db."
                )

    def commit(self) -> None:
        """Commit the build transaction."""
        self.connection.execute("COMMIT")
        self._committed = True
        logger.info(
            "committed %d rows across %d tables",
            sum(self.inserted.values()),
            len(self.inserted),
        )

    def rollback(self) -> None:
        """Roll back the build transaction and discard what it produced.

        A database this build created is removed outright. One it was appending
        to is left in place: it holds an earlier build's work, so the
        transaction rollback that undoes this build's rows is the whole of the
        cleanup, and deleting the file would destroy what it was extending.
        """
        rolled_back = True
        if self._connection is not None:
            try:
                self._connection.execute("ROLLBACK")
            except sqlite3.Error:
                rolled_back = False
                logger.debug("rollback failed; discarding output regardless")
            self._connection.close()
            self._connection = None
        if not self._appended:
            _unlink(self._config.output_db)
        elif not rolled_back:
            logger.error(
                "could not roll back the append to %s; it may hold part of a "
                "failed build and should be rebuilt from scratch",
                self._config.output_db,
            )


def _assert_known_table(table: str) -> None:
    if table not in TABLE_COLUMNS:
        raise KeyError(
            f"{table!r} is not a table this builder writes; add it to "
            "TABLE_COLUMNS with its exact column list first"
        )


def _unlink(path: Path) -> None:
    try:
        path.unlink(missing_ok=True)
    except OSError:
        logger.warning("could not remove partial output at %s", path)
