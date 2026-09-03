"""Orchestration for a proj.db build from an OSDU catalogue.

The catalogue is read concept by concept, in the order proj.db's foreign keys
require: CRSs before the operations between them, and bound CRSs last because
each one embeds a transformation. Everything a record implies is staged as it
is read and written in one transaction at the end; see
:mod:`geodetic_engine.osdudb.context`.
"""

from __future__ import annotations

import logging
import sqlite3
from datetime import UTC, datetime
from typing import Any

from geodetic_engine.osdudb import bound, crs, operation
from geodetic_engine.osdudb.catalog import OsduCatalog
from geodetic_engine.osdudb.config import OsduBuildConfig
from geodetic_engine.osdudb.context import OsduBuildContext, write_staged
from geodetic_engine.osdudb.definition import UnitResolver
from geodetic_engine.osdudb.errors import MissingReferencedObjectError
from geodetic_engine.projdb import authority, schema
from geodetic_engine.projdb.alias import AliasCollector
from geodetic_engine.projdb.records import UsageAccumulator
from geodetic_engine.projdb.report import BuildReport, log_summary
from geodetic_engine.projdb.writer import ProjDbWriter

__all__ = ["BuildReport", "build", "log_summary"]

logger = logging.getLogger(__name__)


def build(
    config: OsduBuildConfig,
    *,
    catalog: OsduCatalog | None = None,
    dry_run: bool = False,
) -> BuildReport:
    """Build an enriched proj.db from an OSDU coordinate reference catalogue.

    The official database is copied and added to; it is never modified in place
    and no row belonging to another authority is ever overwritten.

    Args:
        config: Resolved build configuration.
        catalog: Pre-read catalogue, mainly for testing. Read from
            ``config.catalog`` when omitted.
        dry_run: Perform the whole build, including every constraint and
            collision check, then discard it instead of committing. Nothing is
            left on disk. This exercises the same code as a real build rather
            than approximating it, so a dry run that succeeds means a real build
            would too.

    Returns:
        The build report.

    Raises:
        ProjDbBuildError: On any failure; the partial output is removed.

    Example:
        >>> report = build(load_config(catalog=Path("CRS_CT.json")))  # doctest: +SKIP
        >>> report.rows_by_table["projected_crs"]  # doctest: +SKIP
        998
    """
    catalog = catalog or OsduCatalog.from_file(config.catalog)

    with ProjDbWriter(config) as writer:
        context = OsduBuildContext(
            config=config,
            catalog=catalog,
            writer=writer,
            usage=UsageAccumulator(authority=sorted(config.authorities)[0]),
            alias=AliasCollector(config.naming_systems),
            units=UnitResolver(writer.connection),
        )

        crs.collect_geodetic(context)
        crs.collect_vertical(context)
        crs.collect_engineering(context)
        crs.collect_projected(context)
        crs.collect_compound(context)
        operation.collect_transformations(context)
        operation.collect_concatenated(context)
        # Last of the objects: a bound CRS embeds a transformation, so it can
        # only be assembled once the transformations have been read.
        bound.collect_bound(context)

        write_staged(context)
        _write_usage(context)
        _write_aliases(context)
        preferences = _write_authority_preferences(context)

        report = _report(config, context, catalog)
        report.authority_preferences = preferences
        report.rows_by_table = dict(sorted(writer.inserted.items()))
        report.appended = writer.appended
        report.overwrite_existing = config.overwrite_existing
        report.dry_run = dry_run
        if dry_run:
            logger.info("dry run: discarding %s", config.output_db)
        else:
            writer.commit()

    return report


def _write_usage(context: OsduBuildContext) -> None:
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
            f"but are not in the base proj.db ({described}). The catalogue and "
            "the EPSG dataset in proj.db are at different versions."
        )

    context.writer.insert("scope", new_scopes)
    context.writer.insert("extent", new_extents)
    for row in new_scopes:
        context.known_keys("scope").add((row["auth_name"], str(row["code"])))
    for row in new_extents:
        context.known_keys("extent").add((row["auth_name"], str(row["code"])))

    context.writer.insert("usage", accumulator.usages)


def _write_aliases(context: OsduBuildContext) -> None:
    context.writer.insert("alias_name", context.alias.rows)


def _write_authority_preferences(context: OsduBuildContext) -> list[dict[str, str]]:
    """Register the imported authorities and write their selection preferences."""
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


def _report(
    config: OsduBuildConfig, context: OsduBuildContext, catalog: OsduCatalog
) -> BuildReport:
    with sqlite3.connect(
        f"file:{config.base_proj_db}?mode=ro", uri=True
    ) as base_connection:
        base_metadata = schema.metadata(base_connection)
        layout = schema.database_layout_version(base_connection)

    deprecated = {
        (key.table, key.auth_name, key.code) for key in context.deprecated_keys
    }
    skipped: list[dict[str, Any]] = [
        {
            "table": item.table,
            "auth_name": item.auth_name,
            "code": item.code,
            "name": item.name,
            "deprecated": item.deprecated,
            "reason": item.reason,
        }
        for item in context.skipped
    ]
    return BuildReport(
        built_at=datetime.now(UTC).isoformat(),
        proj_version=base_metadata.get("PROJ.VERSION", "unknown"),
        epsg_version=base_metadata.get("EPSG.VERSION", "unknown"),
        proj_data_version=base_metadata.get("PROJ_DATA.VERSION", "unknown"),
        database_layout_version=layout,
        source=str(catalog.path or config.catalog),
        source_version=config.catalog_version,
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
        skipped=skipped,
    )
