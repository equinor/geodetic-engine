"""Orchestration for a custom proj.db build.

The order objects are imported in is dictated by proj.db's foreign keys:
ellipsoids and prime meridians before datums, coordinate systems before CRSs,
CRSs before the operations that reference them, and everything before the usage,
alias and supersession rows that annotate it.
"""

from __future__ import annotations

import logging
from typing import Any

from geodetic_engine.georepository.cache import (
    CacheMode,
    CachingTransport,
    ResponseCache,
    is_newer,
)
from geodetic_engine.georepository.client import GeorepositoryClient
from geodetic_engine.projdb import (
    annotate,
    bound,
    common,
    coordinate_system,
    crs,
    datum,
    operation,
    schema,
)
from geodetic_engine.projdb.alias import AliasCollector
from geodetic_engine.projdb.config import ProjDbBuildConfig
from geodetic_engine.projdb.context import BuildContext
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
    skip_validation: bool = False,
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
            would too. Responses are cached only in memory, without reading or
            changing the persistent cache.
        skip_validation: Explicitly skip PROJ validation before publication.
            False by default; the choice is recorded in the build history.

    Returns:
        The build report. Written next to the output database unless this is a
        dry run, in which case there is no database to write it next to.

    Raises:
        ProjDbBuildError: On a build or validation failure. Staging is discarded
            and any previously published database is preserved.

    Example:
        >>> report = build(load_config(), dry_run=True)  # doctest: +SKIP
        >>> report.rows_by_table["geodetic_crs"]  # doctest: +SKIP
        12
    """
    owns_client = client is None
    cache = (
        ResponseCache(config.cache_path(), transient=dry_run)
        if owns_client and config.cache_mode is not CacheMode.OFF
        else None
    )
    if client is None:
        client = GeorepositoryClient(
            config.georepository,
            transport=(
                None
                if cache is None
                else CachingTransport(
                    cache, refresh=config.cache_mode is CacheMode.REFRESH
                )
            ),
        )
    try:
        versions = _reconcile_versions(config, client, cache)
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

            common.write_annotations(
                context, source_described="The Georepository instance"
            )
            dropped = _write_supersessions(context)
            preferences = common.write_authority_preferences(context)

            report = _report(config, context, dropped)
            report.authority_preferences = preferences
            report.register_versions = versions
            report.source_version = _own_version(config, versions)
            if cache is not None:
                report.cache = cache.stats.as_dict()
            report.rows_by_table = dict(sorted(writer.inserted.items()))
            report.appended = writer.appended
            report.overwrite_rows = config.overwrite_rows
            report.dry_run = dry_run
            common.finish_build(
                context, report, source="projdb", skip_validation=skip_validation
            )
            if cache is not None and (
                config.cache_mode is CacheMode.REFRESH or not cache.stats.versions
            ):
                cache.record_versions(versions)
    finally:
        if owns_client:
            client.close()
        if cache is not None:
            cache.close()

    return report


def _reconcile_versions(
    config: ProjDbBuildConfig,
    client: GeorepositoryClient,
    cache: ResponseCache | None,
) -> dict[str, str]:
    """Read the register's versions and warn when the cache predates them.

    The version history is never cached, so this is always the live answer. A
    stale cache is reported rather than discarded: a version bump is usually a
    handful of changed objects, and silently turning it into a twenty minute
    refetch would be a worse surprise than a warning.
    """
    versions = client.versions(custom_authority=sorted(config.authorities)[0])
    logger.info(
        "register versions: %s",
        ", ".join(f"{name} {version}" for name, version in versions.items()) or "none",
    )
    if cache is None:
        return versions

    cached = cache.versions()
    cache.stats.versions = cached
    if config.cache_mode is CacheMode.REFRESH or not cached:
        cache.clear()
    stale = [
        f"{source} {cached[source]} -> {version}"
        for source, version in versions.items()
        if source in cached and is_newer(version, cached[source])
    ]
    if stale:
        cache.stats.stale = stale
        if config.cache_mode is CacheMode.USE:
            logger.warning(
                "the register has moved on since this cache was filled (%s); the "
                "build will use the cached responses and so will not see those "
                "changes -- rerun with --refresh-cache or --no-cache to pick them up",
                "; ".join(stale),
            )
    return versions


def _own_version(config: ProjDbBuildConfig, versions: dict[str, str]) -> str | None:
    """The version of the authority being imported, for the report's summary."""
    for authority in sorted(config.authorities):
        for source, version in versions.items():
            if source.casefold() == authority.casefold():
                return version
    return config.georepository_version


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
    report = common.base_report(
        context,
        source=config.api_url,
        source_version=config.georepository_version,
        include_deprecated=config.include_deprecated,
    )
    report.supersessions_written = len(context.supersessions) - len(dropped)
    report.supersessions_dropped = dropped
    return report
