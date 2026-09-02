"""Shared state for one custom proj.db build.

Kept in its own module so the concept modules (crs, datum, operation) can share
it without importing the orchestrator and creating a cycle.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from geodetic_engine.georepository.client import GeorepositoryClient
from geodetic_engine.projdb import translate as tr
from geodetic_engine.projdb.alias import AliasCollector
from geodetic_engine.projdb.config import ProjDbBuildConfig
from geodetic_engine.projdb.errors import MissingReferencedObjectError
from geodetic_engine.projdb.translate import ObjectKey, UsageAccumulator
from geodetic_engine.projdb.writer import ProjDbWriter

logger = logging.getLogger(__name__)

JsonObject = dict[str, Any]


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
class BuildContext:
    """Everything a concept module needs while translating one authority."""

    config: ProjDbBuildConfig
    client: GeorepositoryClient
    writer: ProjDbWriter
    usage: UsageAccumulator
    alias: AliasCollector
    existing: dict[str, set[tuple[str, str]]] = field(default_factory=dict)
    skipped: list[SkippedObject] = field(default_factory=list)
    deprecated_keys: set[ObjectKey] = field(default_factory=set)
    imported_keys: list[ObjectKey] = field(default_factory=list)
    supersessions: list[tuple[ObjectKey, str]] = field(default_factory=list)

    def annotate(self, key: ObjectKey, obj: dict[str, Any]) -> None:
        """Record the usage, aliases and supersessions of an imported object.

        Applied uniformly to every object type, so no class of object silently
        loses its aliases or its link to a replacement.

        Args:
            key: Identity of the object just imported.
            obj: Its detail representation from the register.
        """
        if tr.is_deprecated(obj):
            self.deprecated_keys.add(key)
        for usage in obj.get("Usage") or []:
            self.usage.add(
                key,
                scope_obj=self.client.resolve(usage.get("Scope")),
                extent_obj=self.client.resolve(usage.get("Extent")),
            )
        self.alias.collect(key, obj)
        self.supersessions.extend(tr.supersession_candidates(key, obj))

    def known_keys(self, table: str) -> set[tuple[str, str]]:
        """Return the keys already present in the output database for a table."""
        if table not in self.existing:
            self.existing[table] = self.writer.existing_keys(table)
        return self.existing[table]

    def is_new(self, table: str, auth: str, code: str | None) -> bool:
        """Whether an object is absent from the base database.

        Objects the official database already defines are never re-imported;
        the EPSG dataset shipped with PROJ stays authoritative for its own
        objects.
        """
        if code is None:
            return False
        return (auth, str(code)) not in self.known_keys(table)

    def record(self, table: str, auth: str, code: str) -> ObjectKey:
        """Register an object as imported and return its key."""
        key = ObjectKey(table=table, auth_name=auth, code=str(code))
        self.imported_keys.append(key)
        self.known_keys(table).add((auth, str(code)))
        return key

    def skip(
        self,
        table: str,
        auth: str,
        code: str,
        obj: dict[str, Any] | None,
        reason: str,
    ) -> None:
        """Record that an object was not imported, and why.

        Args:
            table: proj.db table the object would have gone into.
            auth: Its authority.
            code: Its code.
            obj: The register object, used for its name and deprecation state.
            reason: Why it was not imported.
        """
        obj = obj or {}
        deprecated = tr.is_deprecated(obj)
        self.skipped.append(
            SkippedObject(
                table, auth, str(code), tr.text(obj, "Name"), reason, deprecated
            )
        )
        logger.info(
            "skipped %s %s:%s%s (%s)",
            table,
            auth,
            code,
            " [deprecated]" if deprecated else "",
            reason,
        )

    def datum_of(self, table: str, auth: str, code: str) -> tuple[str, str] | None:
        """Return the datum a CRS row already in the database references.

        Args:
            table: One of the CRS tables that carries a datum reference.
            auth: Authority of the CRS.
            code: Code of the CRS.

        Returns:
            The ``(auth_name, code)`` of its datum, or None if the CRS is absent
            or has no datum.
        """
        if table not in {"geodetic_crs", "vertical_crs", "engineering_crs"}:
            raise KeyError(f"{table!r} does not carry a datum reference")
        row = self.writer.connection.execute(
            f"SELECT datum_auth_name, datum_code FROM {table} "
            "WHERE auth_name = ? AND code = ?",
            (auth, code),
        ).fetchone()
        if row is None or row[0] is None or row[1] is None:
            return None
        return str(row[0]), str(row[1])

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
                f"{referenced_by} has no {table} reference"
            )
        if (auth, str(code)) not in self.known_keys(table):
            raise MissingReferencedObjectError(
                f"{referenced_by} references {table} {auth}:{code}, which is "
                "in neither the base proj.db nor this import. The Georepository "
                "instance and the EPSG dataset in proj.db are probably at "
                "different versions."
            )
        return auth, str(code)

    def resolve_link(
        self,
        link: dict[str, Any] | None,
        *,
        tables: str | tuple[str, ...],
        referenced_by: str,
    ) -> tuple[str, str]:
        """Resolve a ``ChildLink`` to the authority and code it points at.

        A ChildLink carries a code but usually no ``DataSource``, and the
        authority must not be assumed from that absence: a custom object
        referencing another custom object looks exactly like one referencing an
        EPSG object. The code is therefore looked up among the custom
        authorities and then the authorities already in the database, and only
        the register itself is asked when that is ambiguous.

        Args:
            link: The ``ChildLink`` to resolve.
            tables: proj.db table, or tables, the reference may point into.
            referenced_by: Description of the referring object, for errors.

        Returns:
            The resolved ``(auth_name, code)`` pair.

        Raises:
            MissingReferencedObjectError: If the reference is absent or cannot
                be found in any candidate table.
        """
        candidates = (tables,) if isinstance(tables, str) else tables
        code = tr.link_code(link)
        if code is None:
            raise MissingReferencedObjectError(
                f"{referenced_by} has no {'/'.join(candidates)} reference"
            )

        declared = tr.auth_name(link or {})
        if declared:
            for table in candidates:
                if (declared, code) in self.known_keys(table):
                    return declared, code
            raise MissingReferencedObjectError(
                f"{referenced_by} references {declared}:{code}, which is in "
                "neither the base proj.db nor this import"
            )

        matches = {
            authority
            for authority in (*sorted(self.config.authorities), "EPSG")
            for table in candidates
            if (authority, code) in self.known_keys(table)
        }
        if len(matches) == 1:
            return matches.pop(), code
        if len(matches) > 1:
            # The same code exists under several authorities, so only the
            # register can say which one is meant.
            resolved = tr.auth_name(self.client.resolve(link))
            if resolved and resolved in matches:
                return resolved, code
            raise MissingReferencedObjectError(
                f"{referenced_by} references code {code}, which exists under "
                f"{sorted(matches)} and the register did not say which is meant"
            )

        owner = tr.auth_name(self.client.resolve(link)) or "unknown authority"
        raise MissingReferencedObjectError(
            f"{referenced_by} references {owner}:{code} in "
            f"{'/'.join(candidates)}, which is in neither the base proj.db nor "
            "this import"
        )
