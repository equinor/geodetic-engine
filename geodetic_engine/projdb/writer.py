"""Transactional writer for the enriched PROJ database.

Three rules distinguish this from a naive bulk insert, and each exists because
its absence produces a database that looks fine and is wrong:

* Rows are written with ``INSERT``, never ``INSERT OR REPLACE``. Replacing lets
  a custom object silently overwrite an EPSG or PROJ definition.
* Every row's ``auth_name`` is checked against the configured custom
  authorities before it is bound.
* The whole build is one transaction. A partially enriched database is worse
  than no database, because it is still loadable.
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

from geodetic_engine.projdb.config import ProjDbBuildConfig
from geodetic_engine.projdb.errors import ForeignAuthorityCollision
from geodetic_engine.projdb.schema import (
    AUTHORITY_COLUMN,
    FOREIGN_AUTHORITY_ALLOWED,
    TABLE_COLUMNS,
    verify_schema,
)

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

    def __init__(self, config: ProjDbBuildConfig) -> None:
        self._config = config
        self._connection: sqlite3.Connection | None = None
        self._committed = False
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

    def open(self) -> None:
        """Copy the base database and begin the build transaction.

        Raises:
            SchemaDriftError: If the base database is not the expected schema.
        """
        output = self._config.output_db
        output.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(self._config.base_proj_db, output)

        connection = sqlite3.connect(output, isolation_level=None)
        connection.execute("PRAGMA foreign_keys = ON")
        verify_schema(connection)
        connection.execute("BEGIN IMMEDIATE")
        self._connection = connection
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
                not configured as custom, or collides with an existing row.
        """
        _assert_known_table(table)
        if not rows:
            return 0

        columns = TABLE_COLUMNS[table]
        if table not in FOREIGN_AUTHORITY_ALLOWED:
            self._guard_authorities(table, rows)

        statement = (
            f"INSERT INTO {table} ({', '.join(columns)}) "
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
        """Roll back and remove the partially written output database."""
        if self._connection is not None:
            try:
                self._connection.execute("ROLLBACK")
            except sqlite3.Error:
                logger.debug("rollback failed; discarding output regardless")
            self._connection.close()
            self._connection = None
        _unlink(self._config.output_db)


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
