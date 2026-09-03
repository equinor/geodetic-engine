"""Orchestration for a custom proj.db build.

The order objects are imported in is dictated by proj.db's foreign keys:
ellipsoids and prime meridians before datums, coordinate systems before CRSs,
CRSs before the operations that reference them, and everything before the usage,
alias and supersession rows that annotate it.
"""

from __future__ import annotations

import logging
import sqlite3
from datetime import UTC, datetime
from typing import Any

from geodetic_engine.georepository.client import GeorepositoryClient
from geodetic_engine.projdb import (
    annotate,
    authority,
    bound,
    coordinate_system,
    crs,
    datum,
    operation,
    schema,
)
from geodetic_engine.projdb.alias import AliasCollector
from geodetic_engine.projdb.config import ProjDbBuildConfig
from geodetic_engine.projdb.context import BuildContext
from geodetic_engine.projdb.errors import MissingReferencedObjectError
from geodetic_engine.projdb.records import UsageAccumulator
from geodetic_engine.projdb.report import BuildReport, log_summary
from geodetic_engine.projdb.writer import ProjDbWriter

__all__ = ["BuildReport", "build", "log_summary"]

logger = logging.getLogger(__name__)


def build(
    config: ProjDbBuildConfig,
    *,
    client: GeorepositoryClient | None = None,
    dry_run: bool = False,
) -> BuildReport:
    """Build an enriched proj.db from a Georepository instance.

    The official database is copied and added to; it is never modified in place
    and no row belonging to another authority is ever overwritten.

    Args:
        config: Resolved build configuration.
        client: Optional pre-built client, mainly for testing.
        dry_run: Perform the whole build, including every constraint and
            collision check, then discard it instead of committing. Nothing is
            left on disk. This exercises the same code as a real build rather
            than approximating it, so a dry run that succeeds means a real build
            would too.

    Returns:
        The build report. Written next to the output database unless this is a
        dry run, in which case there is no database to write it next to.

    Raises:
        ProjDbBuildError: On any failure; the partial output is removed.

    Example:
        >>> report = build(load_config(), dry_run=True)  # doctest: +SKIP
        >>> report.rows_by_table["geodetic_crs"]  # doctest: +SKIP
        12
    """
    owns_client = client is None
    client = client or GeorepositoryClient(config.georepository)
    try:
        with ProjDbWriter(config) as writer:
            authority_name = sorted(config.authorities)[0]
            context = BuildContext(
                config=config,
                client=client,
                writer=writer,
                usage=UsageAccumulator(authority=authority_name),
                alias=AliasCollector(config.naming_systems),
            )

            datum.collect_units(context)
            datum.collect_ellipsoids(context)
            datum.collect_prime_meridians(context)
            coordinate_system.collect(context)
            datum.collect_datums(context)
            operation.collect_conversions(context)
            crs.collect_geodetic(context)
            crs.collect_vertical(context)
            crs.collect_engineering(context)
            crs.collect_projected(context)
            crs.collect_compound(context)
            operation.collect_transformations(context)
            operation.collect_concatenated(context)
            # Last of the objects: a bound CRS embeds a transformation, so it
            # can only be assembled once the transformations exist.
            bound.collect_bound(context)
            # Annotations on other authorities' objects, which must already be
            # in the database for the usage rows to resolve.
            annotate.collect_foreign_annotations(context)

            _write_usage(context)
            _write_aliases(context)
            dropped = _write_supersessions(context)
            preferences = _write_authority_preferences(context)

            report = _report(config, context, dropped)
            report.authority_preferences = preferences
            report.rows_by_table = dict(sorted(writer.inserted.items()))
            report.appended = writer.appended
            report.overwrite_existing = config.overwrite_existing
            report.dry_run = dry_run
            if dry_run:
                logger.info("dry run: discarding %s", config.output_db)
            else:
                writer.commit()
    finally:
        if owns_client:
            client.close()

    return report


def _write_usage(context: BuildContext) -> None:
    """Write scope, extent and usage rows, in that foreign key order."""
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

    foreign = [
        row
        for row in (*new_scopes, *new_extents)
        if str(row["auth_name"]).casefold()
        not in {name.casefold() for name in context.config.authorities}
    ]
    if foreign:
        described = ", ".join(
            f"{row['auth_name']}:{row['code']}" for row in foreign[:5]
        )
        raise MissingReferencedObjectError(
            f"{len(foreign)} scope or extent objects belong to another authority "
            f"but are not in the base proj.db ({described}). The Georepository "
            "instance and the EPSG dataset in proj.db are at different versions."
        )

    context.writer.insert("scope", new_scopes)
    context.writer.insert("extent", new_extents)
    for row in new_scopes:
        context.known_keys("scope").add((row["auth_name"], str(row["code"])))
    for row in new_extents:
        context.known_keys("extent").add((row["auth_name"], str(row["code"])))

    context.writer.insert("usage", accumulator.usages)


def _write_aliases(context: BuildContext) -> None:
    context.writer.insert("alias_name", context.alias.rows)


def _write_authority_preferences(context: BuildContext) -> list[dict[str, str]]:
    """Register the custom authorities and write their selection preferences."""
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


# Object tables a superseded object may be replaced by, grouped by kind. A CRS
# is replaced by a CRS, an operation by an operation.
_REPLACEMENT_FAMILIES: tuple[tuple[str, ...], ...] = (
    (
        "geodetic_crs",
        "projected_crs",
        "vertical_crs",
        "engineering_crs",
        "compound_crs",
    ),
    ("geodetic_datum", "vertical_datum", "engineering_datum"),
    (
        "conversion_table",
        "helmert_transformation_table",
        "grid_transformation",
        "other_transformation",
        "concatenated_operation",
    ),
    ("ellipsoid",),
    ("prime_meridian",),
    ("unit_of_measure",),
)

# Authorities a replacement may belong to, beyond the custom ones. A custom
# object is routinely superseded by an EPSG object once EPSG adopts it.
_REPLACEMENT_AUTHORITIES: tuple[str, ...] = ("EPSG", "ESRI", "IGNF", "NKG", "PROJ")


def _write_supersessions(context: BuildContext) -> list[dict[str, str]]:
    """Write supersessions whose replacement resolves, and report the rest.

    The register records only the replacement's code, not its authority, and a
    custom object is commonly replaced by an EPSG one. The code is therefore
    looked up across the authorities and the sibling tables of the superseded
    object's kind. proj.db has triggers that reject a supersession pointing at
    an object that does not exist, so anything still unresolved is dropped and
    reported rather than aborting an otherwise sound build.
    """
    keep: list[dict[str, Any]] = []
    dropped: list[dict[str, str]] = []
    for key, replacement_code in context.supersessions:
        resolved = _resolve_replacement(context, key.table, replacement_code)
        superseded = f"{key.auth_name}:{key.code}"
        if resolved is None:
            dropped.append(
                {
                    "superseded": superseded,
                    "replacement": replacement_code,
                    "reason": (
                        "no object with this code exists under any authority in "
                        "the database"
                    ),
                }
            )
            continue
        table, auth = resolved
        keep.append(
            {
                "superseded_table_name": key.object_table_name,
                "superseded_auth_name": key.auth_name,
                "superseded_code": key.code,
                "replacement_table_name": schema.OBJECT_TABLE_NAME[table],
                "replacement_auth_name": auth,
                "replacement_code": replacement_code,
                "source": None,
                "same_source_target_crs": 0,
            }
        )
        logger.debug(
            "supersession %s -> %s:%s (%s)", superseded, auth, replacement_code, table
        )
    context.writer.insert("supersession", keep)
    return dropped


def _resolve_replacement(
    context: BuildContext, superseded_table: str, code: str
) -> tuple[str, str] | None:
    """Find the table and authority owning a replacement code.

    The superseded object's own table and authority are tried first, then the
    sibling tables of the same kind, then the other authorities present in a
    PROJ database.

    Returns:
        The ``(table, auth_name)`` of the replacement, or None if no object with
        that code exists.
    """
    family = next(
        (tables for tables in _REPLACEMENT_FAMILIES if superseded_table in tables),
        (superseded_table,),
    )
    tables = (superseded_table, *(t for t in family if t != superseded_table))
    authorities = (*sorted(context.config.authorities), *_REPLACEMENT_AUTHORITIES)
    for table in tables:
        for auth_name in authorities:
            if (auth_name, code) in context.known_keys(table):
                return table, auth_name
    return None


def _report(
    config: ProjDbBuildConfig,
    context: BuildContext,
    dropped: list[dict[str, str]],
) -> BuildReport:
    with sqlite3.connect(
        f"file:{config.base_proj_db}?mode=ro", uri=True
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
        source=config.api_url,
        source_version=config.georepository_version,
        authorities=sorted(config.authorities),
        include_deprecated=config.include_deprecated,
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
        supersessions_written=len(context.supersessions) - len(dropped),
        supersessions_dropped=dropped,
    )
