"""Build steps every proj.db build shares, whatever source it reads from.

A Georepository register and an OSDU catalogue state geodesy in different JSON,
but what a build does once an object has been read is the same: annotate it with
scope, extent and aliases, register its authority with PROJ, and account for
what was imported and what was not. That part lives here so the two sources
cannot drift apart on it, and only the reading of each source's own JSON stays
with that source.
"""

from __future__ import annotations

import logging
import sqlite3
from contextlib import closing
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Protocol

from geodetic_engine.projdb import authority, schema
from geodetic_engine.projdb.alias import AliasCollector
from geodetic_engine.projdb.errors import MissingReferencedObjectError
from geodetic_engine.projdb.records import ObjectKey, UsageAccumulator
from geodetic_engine.projdb.report import BuildReport
from geodetic_engine.projdb.settings import PreferenceSettings
from geodetic_engine.projdb.writer import ProjDbWriter

logger = logging.getLogger(__name__)

JsonObject = dict[str, Any]


def text(obj: JsonObject, *keys: str) -> str | None:
    """Return the first non-empty string among the given keys.

    Both sources spell the same idea under more than one key depending on the
    kind of record, so the candidates are tried in order of preference.

    Args:
        obj: The JSON object to read.
        *keys: Candidate field names, most preferred first.

    Returns:
        The first value that is a non-blank string, stripped, or None.
    """
    for key in keys:
        value = obj.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def number(obj: JsonObject, key: str) -> float | None:
    """Return a numeric field as a float, or None when absent or unparseable.

    Values are kept in the units the source reports them in; unit conversion is
    the responsibility of the caller that knows the associated unit of measure.
    """
    value = obj.get(key)
    if value is None or isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


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


class AnyBuildContext(Protocol):
    """The part of a source's build context the shared steps reach into.

    Declared structurally so that neither source's context has to inherit from
    the other's, and so adding a field to one of them cannot silently change
    what the shared steps see.
    """

    @property
    def config(self) -> PreferenceSettings: ...

    @property
    def writer(self) -> ProjDbWriter: ...

    @property
    def usage(self) -> UsageAccumulator: ...

    @property
    def alias(self) -> AliasCollector: ...

    @property
    def skipped(self) -> list[SkippedObject]: ...

    @property
    def deprecated_keys(self) -> set[ObjectKey]: ...

    @property
    def imported_keys(self) -> list[ObjectKey]: ...

    def known_keys(self, table: str) -> set[tuple[str, str]]: ...

    def is_new(self, table: str, auth: str, code: str | None) -> bool: ...


def write_annotations(context: AnyBuildContext, *, source_described: str) -> None:
    """Write the scope, extent, usage and alias rows the imported objects imply.

    Written in foreign key order: a usage row points at a scope and an extent,
    so both have to exist first.

    Args:
        context: The build in progress.
        source_described: How to name the source in the error raised when a
            scope or extent belongs to an authority this build may not write,
            for example ``"The catalogue"``.

    Raises:
        MissingReferencedObjectError: If a scope or extent belongs to another
            authority and is not already in the base proj.db, which means the
            source and the EPSG dataset in proj.db are at different versions.
    """
    accumulator = context.usage
    new_scopes = [
        row
        for key, row in accumulator.scopes.items()
        if context.is_new("scope", key[0], key[1])
    ]
    new_extents = [
        row
        for key, row in accumulator.extents.items()
        if context.is_new("extent", key[0], key[1])
    ]

    allowed = {name.casefold() for name in context.config.authorities}
    foreign = [
        row
        for row in (*new_scopes, *new_extents)
        if str(row["auth_name"]).casefold() not in allowed
    ]
    if foreign:
        described = ", ".join(
            f"{row['auth_name']}:{row['code']}" for row in foreign[:5]
        )
        raise MissingReferencedObjectError(
            f"{len(foreign)} scope or extent objects belong to another authority "
            f"but are not in the base proj.db ({described}). {source_described} "
            "and the EPSG dataset in proj.db are at different versions."
        )

    context.writer.insert("scope", new_scopes)
    context.writer.insert("extent", new_extents)
    for row in new_scopes:
        context.known_keys("scope").add((row["auth_name"], str(row["code"])))
    for row in new_extents:
        context.known_keys("extent").add((row["auth_name"], str(row["code"])))

    context.writer.insert("usage", accumulator.usages)
    context.writer.insert("alias_name", context.alias.rows)


def write_authority_preferences(context: AnyBuildContext) -> list[dict[str, str]]:
    """Register the imported authorities and write their selection preferences.

    Returns:
        The preference rows written, for the build report.
    """
    connection = context.writer.connection
    builtin = authority.builtin_rows(
        context.config.authorities, authority.read_builtin(connection)
    )
    context.writer.insert(authority.BUILTIN_TABLE, builtin)
    for row in builtin:
        logger.info("registered authority %s with PROJ", row["auth_name"])

    existing = authority.read_existing(connection)
    rows = authority.preference_rows(context.config, existing)
    context.writer.upsert_authority_preferences(rows)
    logger.info("authority preferences: %d rows written", len(rows))
    return [
        {
            "source": str(row["source_auth_name"]),
            "target": str(row["target_auth_name"]),
            "allowed_authorities": str(row["allowed_authorities"]),
        }
        for row in rows
    ]


def base_report(
    context: AnyBuildContext,
    *,
    source: str,
    source_version: str | None,
    include_deprecated: bool,
) -> BuildReport:
    """Start the build report, with everything that does not depend on the source.

    The PROJ, EPSG and layout versions are read from the base database rather
    than from the running PROJ, because the base database is what the build
    actually copied and what its foreign keys resolve against.

    Args:
        context: The finished build.
        source: Where the definitions came from: a Georepository base URL, or
            the path of an OSDU catalogue.
        source_version: The source's own version, when it states one.
        include_deprecated: Whether deprecated objects were imported.

    Returns:
        The report, without any field only one source can fill in.
    """
    config = context.config
    with closing(
        sqlite3.connect(f"file:{config.base_proj_db}?mode=ro", uri=True)
    ) as base_connection:
        base_metadata = schema.metadata(base_connection)
        layout = schema.database_layout_version(base_connection)

    deprecated = {
        (key.table, key.auth_name, key.code) for key in context.deprecated_keys
    }
    return BuildReport(
        built_at=datetime.now(UTC).isoformat(),
        proj_version=base_metadata.get("PROJ.VERSION", "unknown"),
        epsg_version=base_metadata.get("EPSG.VERSION", "unknown"),
        proj_data_version=base_metadata.get("PROJ_DATA.VERSION", "unknown"),
        database_layout_version=layout,
        source=source,
        source_version=source_version,
        authorities=sorted(config.authorities),
        include_deprecated=include_deprecated,
        base_proj_db=str(config.base_proj_db),
        output_db=str(config.output_db),
        imported=[
            {"table": key.table, "auth_name": key.auth_name, "code": key.code}
            for key in context.imported_keys
        ],
        deprecated_imported=[
            {"table": table, "auth_name": auth, "code": code}
            for table, auth, code in sorted(deprecated)
        ],
        skipped=[
            {
                "table": item.table,
                "auth_name": item.auth_name,
                "code": item.code,
                "name": item.name,
                "deprecated": item.deprecated,
                "reason": item.reason,
            }
            for item in context.skipped
        ],
    )
