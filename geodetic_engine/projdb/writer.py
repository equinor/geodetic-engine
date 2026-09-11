"""Locked, staged, atomic publication of enriched PROJ databases.

The base or appended output is snapshotted through SQLite's backup API.
All imports and optional updates occur in staging. A validation callback
runs before atomic publication; exceptions discard only staging. Existing
base definitions and foreign authority objects are never overwritten.
"""

from __future__ import annotations

import logging
import os
import sqlite3
import tempfile
from collections import Counter
from collections.abc import Callable, Mapping, Sequence
from contextlib import closing
from pathlib import Path
from types import TracebackType
from typing import Any

from filelock import FileLock

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
        self._staging: Path | None = None
        self._lock: FileLock | None = None
        self.inserted: Counter[str] = Counter()
        self._base_keys: dict[str, set[tuple[str, str]]] = {}

    def is_base_object(self, table: str, auth: str, code: str) -> bool:
        """Whether a key belongs to the protected base database."""
        _assert_known_table(table)
        if table not in self._base_keys:
            with closing(
                sqlite3.connect(f"file:{self._config.base_proj_db}?mode=ro", uri=True)
            ) as connection:
                self._base_keys[table] = {
                    (str(owner), str(value))
                    for owner, value in connection.execute(
                        f"SELECT auth_name, code FROM {table}"
                    )
                }
        return (auth, code) in self._base_keys[table]

    def __enter__(self) -> ProjDbWriter:
        self.open()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        try:
            if exc_type is not None or not self._committed:
                self.rollback()
            elif self._connection is not None:
                self._connection.close()
                self._connection = None
        finally:
            if self._lock is not None:
                self._lock.release()

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

        Lock the destination and snapshot the base into a temporary sibling
        file. With append and an existing output, snapshot that output instead.
        The published destination is not changed until commit succeeds.

        Raises:
            SchemaDriftError: If the database opened is not the expected schema.
        """
        output = self._config.output_db
        output.parent.mkdir(parents=True, exist_ok=True)
        self._lock = FileLock(str(output) + ".lock")
        self._lock.acquire()
        try:
            self._appended = self._config.append and output.is_file()
            descriptor, name = tempfile.mkstemp(
                prefix=f".{output.name}.", suffix=".staging", dir=output.parent
            )
            os.close(descriptor)
            self._staging = Path(name)
            source = output if self._appended else self._config.base_proj_db
            self._connection = sqlite3.connect(self._staging, isolation_level=None)
            with closing(
                sqlite3.connect(f"file:{source}?mode=ro", uri=True)
            ) as original:
                original.backup(self.connection)
            self.connection.execute("PRAGMA foreign_keys = ON")
            verify_schema(self.connection)
            self.connection.execute("BEGIN IMMEDIATE")
        except BaseException:
            self.rollback()
            self._lock.release()
            raise
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

    def clear_annotations(
        self, table: str, auth: str, code: str, naming_systems: frozenset[str]
    ) -> None:
        """Refresh an updated object's owned annotations without touching others."""
        if not self._config.overwrite_rows:
            return
        from geodetic_engine.projdb.schema import OBJECT_TABLE_NAME

        object_table = OBJECT_TABLE_NAME.get(table)
        if object_table is None or self.is_base_object(table, auth, code):
            return
        owners = tuple(self._config.authorities)
        placeholders = ",".join("?" for _ in owners)
        self.connection.execute(
            "DELETE FROM usage WHERE object_table_name=? AND object_auth_name=? "
            f"AND object_code=? AND auth_name IN ({placeholders})",
            (object_table, auth, code, *owners),
        )
        self.connection.execute(
            "DELETE FROM supersession WHERE superseded_table_name=? "
            "AND superseded_auth_name=? AND superseded_code=?",
            (object_table, auth, code),
        )
        for source in naming_systems:
            self.connection.execute(
                "DELETE FROM alias_name WHERE table_name=? AND auth_name=? "
                "AND code=? AND (lower(source)=lower(?) OR ?='*')",
                (object_table, auth, code, source, source),
            )

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
                ``overwrite_rows`` is not configured.
        """
        _assert_known_table(table)
        if not rows:
            return 0

        columns = TABLE_COLUMNS[table]
        if table not in FOREIGN_AUTHORITY_ALLOWED:
            self._guard_authorities(table, rows)

        verb = "INSERT OR REPLACE" if self._config.overwrite_rows else "INSERT"
        statement = (
            f"{verb} INTO {table} ({', '.join(columns)}) "
            f"VALUES ({', '.join('?' * len(columns))})"
        )
        for row in rows:
            keyed_update = (
                self._config.overwrite_rows
                and table not in FOREIGN_AUTHORITY_ALLOWED
                and "auth_name" in row
                and "code" in row
            )
            if keyed_update:
                auth, code = str(row["auth_name"]), str(row["code"])
                if self.is_base_object(table, auth, code):
                    raise ForeignAuthorityCollision(
                        f"refusing to replace protected base object {auth}:{code}"
                    )
                if table == "coordinate_system":
                    self.connection.execute(
                        "DELETE FROM axis WHERE coordinate_system_auth_name=? "
                        "AND coordinate_system_code=?",
                        (auth, code),
                    )
                elif table == "concatenated_operation":
                    self.connection.execute(
                        "DELETE FROM concatenated_operation_step "
                        "WHERE operation_auth_name=? AND operation_code=?",
                        (auth, code),
                    )
            values = tuple(row.get(column) for column in columns)
            try:
                if (
                    keyed_update
                    and self.connection.execute(
                        f"SELECT 1 FROM {table} WHERE auth_name=? AND code=?",
                        (row["auth_name"], row["code"]),
                    ).fetchone()
                ):
                    changed = [
                        column
                        for column in columns
                        if column not in ("auth_name", "code")
                    ]
                    self.connection.execute(
                        f"UPDATE {table} SET "
                        f"{', '.join(f'{column}=?' for column in changed)} "
                        "WHERE auth_name=? AND code=?",
                        (
                            *(row.get(column) for column in changed),
                            row["auth_name"],
                            row["code"],
                        ),
                    )
                else:
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

    def commit(
        self, *, validate: Callable[[Path], None] | None = None, publish: bool = True
    ) -> None:
        """Commit staging, optionally validate it, then publish atomically.

        Args:
            validate: Callback run against the committed staging database.
                Any exception leaves the published database unchanged.
            publish: False validates staging without replacing the output.
        """
        self.connection.execute("COMMIT")
        assert self._staging is not None
        if validate is not None:
            validate(self._staging)
        if not publish:
            return
        self.connection.close()
        self._connection = None
        with self._staging.open("rb") as stream:
            os.fsync(stream.fileno())
        os.replace(self._staging, self._config.output_db)
        directory = os.open(self._config.output_db.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
        self._staging = None
        self._committed = True
        logger.info(
            "committed %d rows across %d tables",
            sum(self.inserted.values()),
            len(self.inserted),
        )

    def rollback(self) -> None:
        """Roll back the build transaction and discard what it produced.

        Only the staging file is discarded. The published destination is
        preserved for both append and fresh-build failures.
        """
        if self._connection is not None:
            try:
                if self._connection.in_transaction:
                    self._connection.rollback()
            finally:
                self._connection.close()
                self._connection = None
        if self._staging is not None:
            _unlink(self._staging)
            self._staging = None


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
