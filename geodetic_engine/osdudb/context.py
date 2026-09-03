"""Shared state for one proj.db build from an OSDU catalogue.

Unlike a register served over HTTP, an OSDU catalogue defines a CRS's units,
ellipsoid, prime meridian, coordinate system and datum nowhere but inside that
CRS's own WKT. There is therefore no separate pass that could import them
before the CRSs that need them: every object a record implies is produced while
that record is read, staged here, and written in foreign key order once the
whole catalogue has been read.

Staging also makes each record atomic. A CRS whose datum turns out to be
unusable contributes nothing at all rather than leaving its ellipsoid behind as
an orphan.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable, Iterator, Sequence
from dataclasses import dataclass, field
from typing import Any

from geodetic_engine.osdudb import translate as tr
from geodetic_engine.osdudb.catalog import OsduCatalog, Record
from geodetic_engine.osdudb.config import OsduBuildConfig
from geodetic_engine.osdudb.definition import Identifier, UnitResolver
from geodetic_engine.osdudb.errors import MissingReferencedObjectError
from geodetic_engine.projdb.alias import AliasCollector
from geodetic_engine.projdb.records import ObjectKey, UsageAccumulator
from geodetic_engine.projdb.writer import ProjDbWriter

logger = logging.getLogger(__name__)

# The order rows are written in, dictated by proj.db's foreign keys: the units
# an ellipsoid is measured in before the ellipsoid, coordinate systems before
# their axes, everything a CRS references before the CRS, CRSs before the
# operations between them, and the annotations last.
WRITE_ORDER: tuple[str, ...] = (
    "unit_of_measure",
    "ellipsoid",
    "prime_meridian",
    "coordinate_system",
    "axis",
    "geodetic_datum",
    "vertical_datum",
    "engineering_datum",
    "conversion_param",
    "conversion_table",
    "geodetic_crs",
    "vertical_crs",
    "engineering_crs",
    "projected_crs",
    "compound_crs",
    "helmert_transformation_table",
    "grid_transformation",
    "other_transformation",
    "concatenated_operation",
    "concatenated_operation_step",
)

Staged = tuple[str, dict[str, Any]]


@dataclass(slots=True)
class SkippedObject:
    """An object deliberately not imported, and why.

    Whether it was deprecated matters when reading the report: a deprecated
    object that could not be imported is usually of no consequence, while an
    active one is a gap in the database.
    """

    table: str
    auth_name: str
    code: str
    name: str | None
    reason: str
    deprecated: bool = False


@dataclass(slots=True)
class OsduBuildContext:
    """Everything a concept module needs while reading one catalogue."""

    config: OsduBuildConfig
    catalog: OsduCatalog
    writer: ProjDbWriter
    usage: UsageAccumulator
    alias: AliasCollector
    units: UnitResolver
    rows: dict[str, dict[tuple[str, ...], dict[str, Any]]] = field(default_factory=dict)
    existing: dict[str, set[tuple[str, str]]] = field(default_factory=dict)
    skipped: list[SkippedObject] = field(default_factory=list)
    deprecated_keys: set[ObjectKey] = field(default_factory=set)
    imported_keys: list[ObjectKey] = field(default_factory=list)

    def candidates(self, *types: str) -> Iterator[Record]:
        """Yield the records of the given types this build should consider.

        Records belonging to an authority that is not configured are another
        authority's business, and inactive records are only considered when the
        configuration asks for them.
        """
        allowed = {name.casefold() for name in self.config.authorities}
        for record in self.catalog.records(*types):
            if record.auth_name.casefold() not in allowed:
                continue
            if tr.is_deprecated(record.data) and not self.config.include_deprecated:
                logger.debug("skipping inactive %s", record.described)
                continue
            yield record

    def known_keys(self, table: str) -> set[tuple[str, str]]:
        """Return the keys already present in the database or staged for it."""
        if table not in self.existing:
            self.existing[table] = self.writer.existing_keys(table)
        return self.existing[table]

    def is_new(self, table: str, auth: str | None, code: str | None) -> bool:
        """Whether an object is absent from the base database and this build.

        Objects the official database already defines are never re-imported;
        the EPSG dataset shipped with PROJ stays authoritative for its own
        objects.
        """
        if not auth or code is None:
            return False
        return (auth, str(code)) not in self.known_keys(table)

    def stage(self, staged: Iterable[Staged]) -> None:
        """Write one record's rows into the staging area, all or nothing.

        Args:
            staged: ``(table, row)`` pairs. Rows already staged or already in
                the base database are ignored, so a coordinate system shared by
                a thousand CRSs is written once.
        """
        for table, row in staged:
            key = _row_key(table, row)
            table_rows = self.rows.setdefault(table, {})
            if key in table_rows:
                continue
            table_rows[key] = row
            if table != "concatenated_operation_step":
                self.record(table, str(row["auth_name"]), str(row["code"]))

    def pending(self, table: str) -> list[dict[str, Any]]:
        """Return the rows staged for one table, in the order they were added."""
        return list(self.rows.get(table, {}).values())

    def record(self, table: str, auth: str, code: str) -> ObjectKey:
        """Register an object as imported and return its key."""
        key = ObjectKey(table=table, auth_name=auth, code=str(code))
        self.imported_keys.append(key)
        self.known_keys(table).add((auth, str(code)))
        return key

    def annotate(self, key: ObjectKey, record: Record) -> None:
        """Record the usages and aliases of an imported object.

        Applied uniformly to every object type, so no class of object silently
        loses its aliases or the extent it is valid within. OSDU states no
        replacement for an inactive record, so a deprecated object is flagged
        but cannot be linked to whatever superseded it.
        """
        if tr.is_deprecated(record.data):
            self.deprecated_keys.add(key)
        for index, usage in enumerate(tr.usages(record.data), start=1):
            # An extent OSDU computed rather than cited has no code of its own,
            # so it is recorded against the object it was computed for.
            derived = (key.auth_name, f"{key.object_table_name}_{key.code}_{index}")
            self.usage.add(
                key,
                scope=tr.scope_of(usage, derived=derived),
                extent=tr.extent_of(usage, derived=derived),
            )
        for alias, source in tr.aliases(record.data):
            self.alias.add(key, alias=alias, source=source)

    def skip(self, table: str, record: Record, reason: str) -> None:
        """Record that an object was not imported, and why."""
        deprecated = tr.is_deprecated(record.data)
        self.skipped.append(
            SkippedObject(
                table, record.auth_name, record.code, record.name, reason, deprecated
            )
        )
        logger.info(
            "skipped %s %s:%s%s (%s)",
            table,
            record.auth_name,
            record.code,
            " [deprecated]" if deprecated else "",
            reason,
        )

    def require_reference(
        self,
        *,
        table: str,
        auth: str | None,
        code: str | None,
        referenced_by: str,
    ) -> tuple[str, str]:
        """Assert that a referenced object exists, in this build or the base db.

        Args:
            table: proj.db table the reference points into.
            auth: Authority of the referenced object.
            code: Code of the referenced object.
            referenced_by: Human readable description of the referring object,
                used in the error message.

        Returns:
            The ``(auth_name, code)`` pair.

        Raises:
            MissingReferencedObjectError: If the reference cannot be resolved.
                This is a hard error rather than a dropped field: a CRS whose
                datum is missing is not a CRS.
        """
        if not auth or code is None:
            raise MissingReferencedObjectError(
                f"{referenced_by} states no {table} reference"
            )
        if self.is_new(table, auth, code):
            raise MissingReferencedObjectError(
                f"{referenced_by} references {table} {auth}:{code}, which is in "
                "neither the base proj.db nor this catalogue. The catalogue and "
                "the EPSG dataset in proj.db are probably at different versions."
            )
        return auth, str(code)

    def require_writable(
        self, table: str, identifier: Identifier, described: str
    ) -> None:
        """Assert that a new object may be written under its own authority.

        A CRS whose ellipsoid belongs to an authority this build is not
        importing cannot be written: the ellipsoid would have to be invented
        under a different code, and a coordinate computed against an invented
        ellipsoid is wrong.

        Raises:
            MissingReferencedObjectError: If the object is new and its
                authority is not configured.
        """
        allowed = {name.casefold() for name in self.config.authorities}
        if identifier.auth_name.casefold() in allowed:
            return
        raise MissingReferencedObjectError(
            f"{described} needs {table} {identifier.auth_name}:{identifier.code}, "
            f"which the base proj.db does not define and this build may not add "
            f"because {identifier.auth_name} is not among the configured "
            f"authorities {sorted(self.config.authorities)}"
        )

    def new_dependency(
        self, table: str, identifier: Identifier | None, described: str
    ) -> bool:
        """Whether a referenced object still has to be produced from the WKT.

        Args:
            table: proj.db table the object belongs in.
            identifier: The object's authority and code, or None when the WKT
                gave it none.
            described: Identity of the object that needs it, for errors.

        Returns:
            True when the object is new and writable, False when the database
            already defines it.

        Raises:
            MissingReferencedObjectError: If the object is unidentified, or is
                new but belongs to an authority this build may not write.
        """
        if identifier is None:
            raise MissingReferencedObjectError(
                f"{described} references a {table} the WKT does not identify, so "
                "it cannot be recorded under a code anything could resolve"
            )
        if not self.is_new(table, identifier.auth_name, identifier.code):
            return False
        self.require_writable(table, identifier, described)
        return True


def _row_key(table: str, row: dict[str, Any]) -> tuple[str, ...]:
    """Return the primary key of a row, which is not always authority and code."""
    if table == "concatenated_operation_step":
        return (
            str(row["operation_auth_name"]),
            str(row["operation_code"]),
            str(row["step_number"]),
        )
    return (str(row["auth_name"]), str(row["code"]))


def write_staged(context: OsduBuildContext) -> None:
    """Write every staged row, in the order proj.db's foreign keys require."""
    for table in WRITE_ORDER:
        rows: Sequence[dict[str, Any]] = context.pending(table)
        if rows:
            context.writer.insert(table, rows)
            logger.info("%s: %d imported", table, len(rows))
