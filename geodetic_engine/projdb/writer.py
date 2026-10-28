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
import stat
import tempfile
from collections import Counter
from collections.abc import Callable, Mapping, Sequence
from contextlib import closing
from pathlib import Path
from types import TracebackType
from typing import Any

from filelock import FileLock

from geodetic_engine.projdb.errors import (
    ForeignAuthorityCollision,
    MissingReferencedObjectError,
    ProjDbBuildError,
    SchemaDriftError,
)
from geodetic_engine.projdb.schema import (
    AUTHORITY_COLUMN,
    FOREIGN_AUTHORITY_ALLOWED,
    IDENTIFIER_COLUMNS,
    IDENTIFIER_DDL,
    IDENTIFIER_TABLE,
    LEGACY_IDENTIFIER_TABLE,
    PERSISTABLE_REFERENCE_COLUMNS,
    PERSISTABLE_REFERENCE_DDL,
    PERSISTABLE_REFERENCE_TABLE,
    TABLE_COLUMNS,
    identifier_alias_source,
    verify_schema,
)
from geodetic_engine.projdb.settings import (
    DatabaseSettings,
    check_discarded_authorities,
)

logger = logging.getLogger(__name__)

_WINDOWS = os.name == "nt"

Row = Mapping[str, Any]

# Rows that belong to another object and are replaced with it: the child table,
# its columns naming the parent, and the column ordering the rows.
_DEPENDENTS: dict[str, tuple[str, str, str, str]] = {
    "coordinate_system": (
        "axis",
        "coordinate_system_auth_name",
        "coordinate_system_code",
        "coordinate_system_order",
    ),
    "concatenated_operation": (
        "concatenated_operation_step",
        "operation_auth_name",
        "operation_code",
        "step_number",
    ),
}
_DEPENDENT_LABEL = {"coordinate_system": "axes", "concatenated_operation": "steps"}

_OVERWRITE_HINT = (
    " Pass --overwrite-rows (overwrite_rows = true) to let this source's "
    "definitions replace the ones an earlier build wrote."
)


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
        # What an overwriting build replaced that an earlier build had written.
        self._replaced: list[dict[str, Any]] = []
        self._unchanged = 0
        self._dependents: dict[tuple[str, str, str], list[tuple[Any, ...]]] = {}
        self._moved_identifiers: list[dict[str, str]] = []
        self._replaced_references: list[dict[str, str]] = []
        self._removed_references: list[str] = []
        # The digest of each reference removed with an identifier that moved
        # to another object, so one recorded again is reported as replaced.
        self._displaced_references: dict[tuple[str, str], str] = {}

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
            OutputWouldBeDiscarded: If a fresh build would drop authorities the
                existing output database was built with.
        """
        output = self._config.output_db
        output.parent.mkdir(parents=True, exist_ok=True)
        self._lock = FileLock(str(output) + ".lock")
        self._lock.acquire()
        try:
            if not self._config.append:
                check_discarded_authorities(
                    output,
                    self._config.authorities,
                    replace=self._config.replace,
                )
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
            if self._appended:
                self._migrate_legacy_identifier_table()
        except BaseException:
            self.rollback()
            self._lock.release()
            raise
        if self._appended:
            logger.info("appending to the existing database at %s", output)
        else:
            logger.info("copied %s to %s", self._config.base_proj_db, output)

    def _migrate_legacy_identifier_table(self) -> None:
        """Move identifiers from the former table name in an appended database."""
        tables = {
            str(name)
            for (name,) in self.connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table' "
                "AND name IN (?, ?)",
                (IDENTIFIER_TABLE, LEGACY_IDENTIFIER_TABLE),
            )
        }
        if LEGACY_IDENTIFIER_TABLE not in tables:
            return
        if IDENTIFIER_TABLE in tables:
            raise SchemaDriftError(
                f"database contains both {LEGACY_IDENTIFIER_TABLE!r} and "
                f"{IDENTIFIER_TABLE!r}; refusing to merge identifier tables"
            )

        for statement in IDENTIFIER_DDL:
            self.connection.execute(statement)
        columns = ", ".join(IDENTIFIER_COLUMNS)
        self.connection.execute(
            f"INSERT INTO {IDENTIFIER_TABLE} ({columns}) "
            f"SELECT {columns} FROM {LEGACY_IDENTIFIER_TABLE}"
        )
        self.connection.execute(f"DROP TABLE {LEGACY_IDENTIFIER_TABLE}")
        logger.info(
            "migrated %s to %s",
            LEGACY_IDENTIFIER_TABLE,
            IDENTIFIER_TABLE,
        )

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
            try:
                if keyed_update and self._replace(table, columns, row):
                    continue
                self.connection.execute(
                    statement, tuple(row.get(column) for column in columns)
                )
            except sqlite3.IntegrityError as exc:
                identity = {
                    key: row.get(key)
                    for key in ("auth_name", "code", "name")
                    if key in row
                }
                hint = _OVERWRITE_HINT if "UNIQUE" in str(exc) else ""
                raise ForeignAuthorityCollision(
                    f"could not insert into {table}: {exc}. Row: {identity}. "
                    "The build is aborted rather than overwriting an existing "
                    f"definition.{hint}"
                ) from exc

        self.inserted[table] += len(rows)
        return len(rows)

    def _replace(self, table: str, columns: Sequence[str], row: Row) -> bool:
        """Bring an existing row of this build's own authorities up to date.

        A row stated exactly as it already is, is left alone. One that differs
        is updated and recorded with the columns that changed, so the build can
        say what it replaced.

        Returns:
            False when there is no such row yet, for the caller to insert.

        Raises:
            ForeignAuthorityCollision: If the row is a base database object.
        """
        auth, code = str(row["auth_name"]), str(row["code"])
        if self.is_base_object(table, auth, code):
            raise ForeignAuthorityCollision(
                f"refusing to replace protected base object {auth}:{code}"
            )
        existing = self.connection.execute(
            f"SELECT {', '.join(columns)} FROM {table} WHERE auth_name=? AND code=?",
            (auth, code),
        ).fetchone()
        if existing is None:
            return False
        if table in _DEPENDENTS:
            child, auth_column, code_column, _ = _DEPENDENTS[table]
            self._dependents.setdefault(
                (table, auth, code), self._dependent_rows(table, auth, code)
            )
            self.connection.execute(
                f"DELETE FROM {child} WHERE {auth_column}=? AND {code_column}=?",
                (auth, code),
            )
        before = dict(zip(columns, existing, strict=True))
        changed = [
            column
            for column in columns
            if column not in ("auth_name", "code")
            and not _same(before[column], row.get(column))
        ]
        # An axis is part of its coordinate system and is reported with it.
        reported = table != "axis"
        if not changed:
            if reported:
                self._unchanged += 1
            return True
        self.connection.execute(
            f"UPDATE {table} SET {', '.join(f'{column}=?' for column in changed)} "
            "WHERE auth_name=? AND code=?",
            (*(row.get(column) for column in changed), auth, code),
        )
        if reported:
            self._replaced.append(
                {
                    "table": table,
                    "auth_name": auth,
                    "code": code,
                    "name": row.get("name"),
                    "previous_name": before.get("name"),
                    "changed": changed,
                }
            )
        return True

    def _dependent_rows(
        self, table: str, auth: str, code: str
    ) -> list[tuple[Any, ...]]:
        """The rows of an object's dependent table, without their own keys."""
        child, auth_column, code_column, order = _DEPENDENTS[table]
        compared = [
            column
            for column in TABLE_COLUMNS[child]
            if column not in (auth_column, code_column, "auth_name", "code")
        ]
        return self.connection.execute(
            f"SELECT {', '.join(compared)} FROM {child} "
            f"WHERE {auth_column}=? AND {code_column}=? ORDER BY {order}",
            (auth, code),
        ).fetchall()

    def overrides(self) -> dict[str, Any]:
        """What this build replaced that an earlier build had written.

        Only a build with ``overwrite_rows`` replaces anything. Usages and
        aliases are refreshed with the objects they annotate and not listed.

        Returns:
            ``objects``, each object whose definition changed, with the columns
            (or ``axes``, ``steps``) that did; ``unchanged``, how many objects
            this build stated exactly as they already were; ``identifiers``,
            each id that now names a different object or nothing, or changed
            its inactive flag; and
            ``persistable_references`` and ``persistable_references_removed``,
            each record id whose reference was replaced or removed.
        """
        objects = {
            (item["table"], item["auth_name"], item["code"]): item
            for item in self._replaced
        }
        unchanged = self._unchanged
        for (table, auth, code), before in self._dependents.items():
            if self._dependent_rows(table, auth, code) == before:
                continue
            label = _DEPENDENT_LABEL[table]
            if (key := (table, auth, code)) in objects:
                objects[key]["changed"].append(label)
                continue
            unchanged -= 1
            name = None
            if "name" in TABLE_COLUMNS[table]:
                (name,) = self.connection.execute(
                    f"SELECT name FROM {table} WHERE auth_name=? AND code=?",
                    (auth, code),
                ).fetchone()
            objects[key] = {
                "table": table,
                "auth_name": auth,
                "code": code,
                "name": name,
                "previous_name": name,
                "changed": [label],
            }
        return {
            "objects": list(objects.values()),
            "unchanged": unchanged,
            "identifiers": list(self._moved_identifiers),
            "persistable_references": list(self._replaced_references),
            "persistable_references_removed": list(self._removed_references),
        }

    def write_identifiers(
        self, rows: Sequence[Row], *, aliases: bool = False
    ) -> tuple[int, int]:
        """Record identifiers another scheme gives objects in this database.

        An identifier annotates an object rather than defining one, so it may
        point at an object of any authority, EPSG's included, and the authority
        guard does not apply. The object must exist: the table's insert
        trigger refuses an identifier for an absent one, as PROJ's own
        ``alias_name`` trigger does.

        Recording an identifier again against the same object changes nothing,
        so the same source can be appended twice. Recording it against a
        different object is a collision, refused unless ``overwrite_rows`` is
        configured, because a caller already resolving that identifier would
        otherwise silently get a different object. A persistableReference
        recorded for a moved identifier stated the earlier object, so it is
        removed with it and reported, as removed or, once this build records
        another for the identifier, as replaced.

        Args:
            rows: Mappings with every column of ``IDENTIFIER_COLUMNS``; the
                ``table_name`` in PROJ's ``object_view`` vocabulary.
            aliases: Also record each identifier as an ``alias_name`` row under
                :func:`~geodetic_engine.projdb.schema.identifier_alias_source`,
                so that PROJ's own name search finds it. An existing identical
                alias is not duplicated.

        Returns:
            How many identifiers and how many aliases were written.

        Raises:
            ForeignAuthorityCollision: If an identifier already names a
                different object, or the same object with a different
                deprecation flag, and ``overwrite_rows`` is not configured.
            MissingReferencedObjectError: If a row names an object the database
                does not define.
        """
        for statement in IDENTIFIER_DDL:
            self.connection.execute(statement)
        written = aliased = 0
        for row in rows:
            scheme, identifier = str(row["scheme"]), str(row["identifier"])
            target = (str(row["table_name"]), str(row["auth_name"]), str(row["code"]))
            existing = self.connection.execute(
                f"SELECT table_name, auth_name, code, deprecated FROM "
                f"{IDENTIFIER_TABLE} WHERE scheme = ? AND identifier = ?",
                (scheme, identifier),
            ).fetchone()
            if existing is None:
                self._insert_identifier(row)
                written += 1
            else:
                previous = (str(existing[0]), str(existing[1]), str(existing[2]))
                unchanged = previous == target and bool(existing[3]) == bool(
                    row["deprecated"]
                )
                if not unchanged:
                    was = (
                        f"{previous[0]} {previous[1]}:{previous[2]}"
                        f"{' (inactive)' if existing[3] else ''}"
                    )
                    now = (
                        f"{target[0]} {target[1]}:{target[2]}"
                        f"{' (inactive)' if row['deprecated'] else ''}"
                    )
                    if not self._config.overwrite_rows:
                        raise ForeignAuthorityCollision(
                            f"{scheme} identifier {identifier!r} already names "
                            f"{was}; refusing to record it against {now}. The "
                            "build is aborted rather than changing what the "
                            f"identifier resolves to.{_OVERWRITE_HINT}"
                        )
                    self._moved_identifiers.append(
                        {"identifier": identifier, "previous": was, "now": now}
                    )
                    if previous == target:
                        # Only the inactive flag changed. Deleting the row would
                        # cascade to the persistableReference recorded for it.
                        self.connection.execute(
                            f"UPDATE {IDENTIFIER_TABLE} SET deprecated = ?, "
                            "source = ? WHERE scheme = ? AND identifier = ?",
                            (row["deprecated"], row.get("source"), scheme, identifier),
                        )
                        written += 1
                        if aliases and self._add_identifier_alias(
                            scheme, identifier, target
                        ):
                            aliased += 1
                        continue
                    # The reference stated the object the id named, so the
                    # foreign key removes it with the id; report that.
                    digest = self._reference_digest(scheme, identifier)
                    if digest is not None:
                        self._displaced_references[(scheme, identifier)] = digest
                        self._removed_references.append(identifier)
                    self.connection.execute(
                        f"DELETE FROM {IDENTIFIER_TABLE} "
                        "WHERE scheme = ? AND identifier = ?",
                        (scheme, identifier),
                    )
                    self.connection.execute(
                        "DELETE FROM alias_name WHERE table_name = ? AND "
                        "auth_name = ? AND code = ? AND alt_name = ? AND source = ?",
                        (*previous, identifier, identifier_alias_source(scheme)),
                    )
                    self._insert_identifier(row)
                    written += 1
            if aliases and self._add_identifier_alias(scheme, identifier, target):
                aliased += 1

        if written:
            self.inserted[IDENTIFIER_TABLE] += written
        if aliased:
            self.inserted["alias_name"] += aliased
        return written, aliased

    def remove_identifiers(self, removals: Sequence[tuple[str, str, str]]) -> int:
        """Remove identifiers an earlier build recorded that a source now refuses.

        An identifier this database does not record is passed over. One it
        does is removed with its alias and, through the foreign key, its
        persistableReference, so that it stops resolving rather than going on
        naming the earlier object.

        Args:
            removals: The scheme, the identifier, and why the source records it
                for nothing, as a clause about the identifier.

        Returns:
            How many identifiers were removed.

        Raises:
            ForeignAuthorityCollision: If an identifier is recorded and
                ``overwrite_rows`` is not configured, since removing it changes
                what it resolves to.
        """
        if not removals:
            return 0
        for statement in IDENTIFIER_DDL:
            self.connection.execute(statement)
        removed = 0
        for scheme, identifier, why in removals:
            existing = self.connection.execute(
                f"SELECT table_name, auth_name, code, deprecated FROM "
                f"{IDENTIFIER_TABLE} WHERE scheme = ? AND identifier = ?",
                (scheme, identifier),
            ).fetchone()
            if existing is None:
                continue
            previous = (str(existing[0]), str(existing[1]), str(existing[2]))
            was = (
                f"{previous[0]} {previous[1]}:{previous[2]}"
                f"{' (inactive)' if existing[3] else ''}"
            )
            if not self._config.overwrite_rows:
                raise ForeignAuthorityCollision(
                    f"{scheme} identifier {identifier!r} already names {was}, but "
                    f"{why}, so it would name nothing. The build is aborted rather "
                    f"than changing what the identifier resolves to.{_OVERWRITE_HINT}"
                )
            if self.persistable_reference_recorded(scheme, identifier):
                self._removed_references.append(identifier)
            self.connection.execute(
                f"DELETE FROM {IDENTIFIER_TABLE} WHERE scheme = ? AND identifier = ?",
                (scheme, identifier),
            )
            self.connection.execute(
                "DELETE FROM alias_name WHERE table_name = ? AND "
                "auth_name = ? AND code = ? AND alt_name = ? AND source = ?",
                (*previous, identifier, identifier_alias_source(scheme)),
            )
            self._moved_identifiers.append(
                {"identifier": identifier, "previous": was, "now": f"nothing: {why}"}
            )
            removed += 1
        return removed

    def _insert_identifier(self, row: Row) -> None:
        values = tuple(row.get(column) for column in IDENTIFIER_COLUMNS)
        try:
            self.connection.execute(
                f"INSERT INTO {IDENTIFIER_TABLE} ({', '.join(IDENTIFIER_COLUMNS)}) "
                f"VALUES ({', '.join('?' * len(IDENTIFIER_COLUMNS))})",
                values,
            )
        except sqlite3.IntegrityError as exc:
            raise MissingReferencedObjectError(
                f"{row.get('scheme')} identifier {row.get('identifier')!r} names "
                f"{row.get('table_name')} {row.get('auth_name')}:{row.get('code')}, "
                f"which this database does not define: {exc}"
            ) from exc

    def _add_identifier_alias(
        self, scheme: str, identifier: str, target: tuple[str, str, str]
    ) -> bool:
        source = identifier_alias_source(scheme)
        if self.connection.execute(
            "SELECT 1 FROM alias_name WHERE table_name = ? AND auth_name = ? "
            "AND code = ? AND alt_name = ? AND source = ?",
            (*target, identifier, source),
        ).fetchone():
            return False
        self.connection.execute(
            "INSERT INTO alias_name (table_name, auth_name, code, alt_name, source) "
            "VALUES (?, ?, ?, ?, ?)",
            (*target, identifier, source),
        )
        return True

    def write_persistable_references(self, rows: Sequence[Row]) -> tuple[int, int]:
        """Record the persistableReference strings records publish, by record id.

        A reference annotates the record an identifier names, so it may belong
        to an object of any authority and the authority guard does not apply.
        The record's identifier must already be recorded: the table's foreign
        key refuses a reference for one that is not, which is what ties every
        reference to an object in the database.

        Recording the same payload again changes nothing, so the same source
        can be appended twice. Recording a different payload for an id that
        already has one is refused unless ``overwrite_rows`` is configured,
        because whoever already reads that reference would otherwise silently
        get a different one.

        Args:
            rows: Mappings with every column of
                ``PERSISTABLE_REFERENCE_COLUMNS``.

        Returns:
            How many references were written and how many were already
            recorded unchanged.

        Raises:
            ForeignAuthorityCollision: If an id already has a different
                reference and ``overwrite_rows`` is not configured.
            MissingReferencedObjectError: If a row's identifier is not
                recorded in the identifier table.
        """
        # The identifier table is the foreign key's parent, so it has to exist
        # for the key to refuse a reference rather than fail to resolve.
        for statement in (*IDENTIFIER_DDL, *PERSISTABLE_REFERENCE_DDL):
            self.connection.execute(statement)
        columns = ", ".join(PERSISTABLE_REFERENCE_COLUMNS)
        placeholders = ", ".join("?" * len(PERSISTABLE_REFERENCE_COLUMNS))
        written = unchanged = 0
        for row in rows:
            scheme, identifier = str(row["scheme"]), str(row["identifier"])
            existing = self.connection.execute(
                f"SELECT sha256 FROM {PERSISTABLE_REFERENCE_TABLE} "
                "WHERE scheme = ? AND identifier = ?",
                (scheme, identifier),
            ).fetchone()
            if existing is not None:
                if str(existing[0]) == str(row["sha256"]):
                    unchanged += 1
                    continue
                if not self._config.overwrite_rows:
                    raise ForeignAuthorityCollision(
                        f"{scheme} record {identifier!r} already has a different "
                        "persistableReference recorded; refusing to replace it. "
                        "The build is aborted rather than changing what is "
                        f"returned for it.{_OVERWRITE_HINT}"
                    )
                self._replaced_references.append(
                    {
                        "identifier": identifier,
                        "previous_sha256": str(existing[0]),
                        "sha256": str(row["sha256"]),
                    }
                )
                self.connection.execute(
                    f"DELETE FROM {PERSISTABLE_REFERENCE_TABLE} "
                    "WHERE scheme = ? AND identifier = ?",
                    (scheme, identifier),
                )
            elif (
                displaced := self._displaced_references.pop((scheme, identifier), None)
            ) is not None:
                # Removed earlier in this build with an id that moved, and now
                # recorded again: a replacement, not a removal.
                self._removed_references.remove(identifier)
                if displaced != str(row["sha256"]):
                    self._replaced_references.append(
                        {
                            "identifier": identifier,
                            "previous_sha256": displaced,
                            "sha256": str(row["sha256"]),
                        }
                    )
            try:
                self.connection.execute(
                    f"INSERT INTO {PERSISTABLE_REFERENCE_TABLE} ({columns}) "
                    f"VALUES ({placeholders})",
                    tuple(row.get(column) for column in PERSISTABLE_REFERENCE_COLUMNS),
                )
            except sqlite3.IntegrityError as exc:
                raise MissingReferencedObjectError(
                    f"a persistableReference was given for {scheme} record "
                    f"{identifier!r}, whose id this database does not record: "
                    f"{exc}"
                ) from exc
            written += 1

        if written:
            self.inserted[PERSISTABLE_REFERENCE_TABLE] += written
        return written, unchanged

    def persistable_reference_recorded(self, scheme: str, identifier: str) -> bool:
        """Whether a persistableReference is already recorded for a record id."""
        return self._reference_digest(scheme, identifier) is not None

    def _reference_digest(self, scheme: str, identifier: str) -> str | None:
        """The digest of the reference recorded for a record id, if any."""
        if not self._table_exists(PERSISTABLE_REFERENCE_TABLE):
            return None
        found = self.connection.execute(
            f"SELECT sha256 FROM {PERSISTABLE_REFERENCE_TABLE} "
            "WHERE scheme = ? AND identifier = ?",
            (scheme, identifier),
        ).fetchone()
        return None if found is None else str(found[0])

    def recorded_identifier(
        self, scheme: str, identifier: str
    ) -> tuple[str, str, str, bool] | None:
        """The object an earlier build recorded an identifier against, if any.

        Returns:
            The object's ``table_name``, ``auth_name`` and ``code``, and the
            identifier's inactive flag; None when it is not recorded.
        """
        if not self._table_exists(IDENTIFIER_TABLE):
            return None
        found = self.connection.execute(
            f"SELECT table_name, auth_name, code, deprecated FROM {IDENTIFIER_TABLE} "
            "WHERE scheme = ? AND identifier = ?",
            (scheme, identifier),
        ).fetchone()
        if found is None:
            return None
        return str(found[0]), str(found[1]), str(found[2]), bool(found[3])

    def _table_exists(self, table: str) -> bool:
        return bool(
            self.connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
                (table,),
            ).fetchone()
        )

    def remove_persistable_references(self, keys: Sequence[tuple[str, str]]) -> int:
        """Remove the references a source no longer states, by scheme and id.

        Only with ``overwrite_rows``: removing a reference changes what is
        returned for the object as surely as replacing it does.

        Returns:
            How many references were removed.

        Raises:
            ForeignAuthorityCollision: If ``overwrite_rows`` is not configured.
        """
        if not keys:
            return 0
        if not self._config.overwrite_rows:
            raise ForeignAuthorityCollision(
                "refusing to remove recorded persistableReferences without "
                "overwrite_rows"
            )
        removed = 0
        for scheme, identifier in keys:
            count = self.connection.execute(
                f"DELETE FROM {PERSISTABLE_REFERENCE_TABLE} "
                "WHERE scheme = ? AND identifier = ?",
                (scheme, identifier),
            ).rowcount
            if count:
                self._removed_references.append(identifier)
            removed += count
        return removed

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

        Existing destination permission bits are preserved. New databases keep
        staging's private permissions (0600 on POSIX). Staging remains private
        until validation succeeds; ownership and ACLs are not copied.

        Args:
            validate: Callback run against the committed staging database.
                Any exception leaves the published database unchanged.
            publish: False validates staging without replacing the output.

        Raises:
            ProjDbBuildError: On Windows, if the published database cannot be
                replaced, most often because another process has it open.
        """
        self.connection.execute("COMMIT")
        assert self._staging is not None
        if validate is not None:
            validate(self._staging)
        if not publish:
            return
        self.connection.close()
        self._connection = None
        # Windows flushes only a handle opened for writing.
        with self._staging.open("r+b") as stream:
            try:
                mode = stat.S_IMODE(self._config.output_db.stat().st_mode)
            except FileNotFoundError:
                pass
            else:
                self._staging.chmod(mode)
            os.fsync(stream.fileno())
        try:
            os.replace(self._staging, self._config.output_db)
        except PermissionError as error:
            if not _WINDOWS:
                raise
            raise ProjDbBuildError(
                f"cannot replace {self._config.output_db}: {error}. The database "
                "there is unchanged. Another process may have it open, such as a "
                "Python session or notebook reading it through PROJ, or the file "
                "or its directory may be read-only."
            ) from error
        # Windows cannot open a directory to sync it; NTFS journals the rename.
        if not _WINDOWS:
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


def _same(stored: Any, stated: Any) -> bool:
    """Whether a stored value is the one a row states, across SQLite's affinity.

    A code written as text may come back as an integer, without the definition
    having changed.
    """
    if stored == stated:
        return True
    if stored is None or stated is None:
        return False
    return str(stored) == str(stated)


def _unlink(path: Path) -> None:
    try:
        try:
            path.unlink(missing_ok=True)
        except PermissionError:
            if not _WINDOWS:
                raise
            # Windows will not delete a read-only file; staging takes the output's mode.
            path.chmod(stat.S_IREAD | stat.S_IWRITE)
            path.unlink(missing_ok=True)
    except OSError:
        logger.warning("could not remove partial output at %s", path)
