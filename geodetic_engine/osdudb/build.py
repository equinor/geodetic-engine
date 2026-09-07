"""Orchestration for a proj.db build from an OSDU catalogue.

The catalogue is read concept by concept, in the order proj.db's foreign keys
require: CRSs before the operations between them, and bound CRSs last because
each one embeds a transformation. Everything a record implies is staged as it
is read and written in one transaction at the end; see
:mod:`geodetic_engine.osdudb.context`.
"""

from __future__ import annotations

import logging

from geodetic_engine.osdudb import bound, crs, operation
from geodetic_engine.osdudb.catalog import OsduCatalog
from geodetic_engine.osdudb.config import OsduBuildConfig
from geodetic_engine.osdudb.context import OsduBuildContext, write_staged
from geodetic_engine.osdudb.definition import UnitResolver
from geodetic_engine.projdb import common
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
        common.write_annotations(context, source_described="The catalogue")
        preferences = common.write_authority_preferences(context)

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


def _report(
    config: OsduBuildConfig, context: OsduBuildContext, catalog: OsduCatalog
) -> BuildReport:
    return common.base_report(
        context,
        source=str(catalog.path or config.catalog),
        source_version=config.catalog_version,
        include_deprecated=config.include_deprecated,
    )
