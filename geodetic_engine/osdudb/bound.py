"""Bound CRSs from an OSDU catalogue.

A bound CRS is a CRS packaged together with the single transformation that ties
it to a hub, almost always WGS 84. It is early binding made explicit: the
operation is part of the CRS definition rather than something chosen at
transformation time, which is exactly the guarantee this workflow wants. OSDU
publishes these as their own records, with a synthetic code and references to
the CRS and the transformation being bound together.

proj.db has no bound CRS table. PROJ stores one as an ordinary ``geodetic_crs``
or ``projected_crs`` row whose ``text_definition`` holds the whole ``BOUNDCRS``
WKT, and whose coordinate system and datum columns are therefore NULL, which
the table's own CHECK constraints require.

The WKT is assembled here with pyproj rather than taken from the catalogue,
which publishes none for a bound CRS, so the transformation actually embedded
is one this package has inspected. That matters for a bound CRS defined over a
concatenated operation: PROJ cannot embed a chain, so the chain is first
collapsed into a single equivalent step and refused if it does not compose. See
:mod:`geodetic_engine.geodesy.utils.helmert`.
"""

from __future__ import annotations

import logging

from pyproj import CRS
from pyproj.crs import BoundCRS, CoordinateOperation
from pyproj.exceptions import CRSError

from geodetic_engine.geodesy.errors import NotCollapsibleError
from geodetic_engine.geodesy.utils import collapse_concatenated
from geodetic_engine.osdudb import definition as df
from geodetic_engine.osdudb import translate as tr
from geodetic_engine.osdudb.catalog import BOUND_CRS, Record
from geodetic_engine.osdudb.context import OsduBuildContext
from geodetic_engine.osdudb.errors import ProjDbBuildError, UnreadableDefinitionError
from geodetic_engine.projdb.records import ObjectKey

logger = logging.getLogger(__name__)


def collect_bound(context: OsduBuildContext) -> None:
    """Import bound CRSs as text definitions on the CRS tables.

    Each one needs three definitions: its base CRS, the transformation, and the
    transformation's target. Any bound CRS whose parts cannot be resolved, or
    whose transformation cannot be reduced to a single step, is skipped and
    reported rather than written in a weakened form.
    """
    geodetic = 0
    projected = 0
    for record in context.candidates(BOUND_CRS):
        try:
            definition, base = _definition(context, record)
        except ProjDbBuildError as exc:
            # The table is decided by the base CRS, which is exactly what could
            # not be read here, so a failure is attributed to the geodetic
            # table.
            context.skip("geodetic_crs", record, str(exc))
            continue

        table = "projected_crs" if base.is_projected else "geodetic_crs"
        if not context.is_new(table, record.auth_name, record.code):
            continue

        row = {
            "auth_name": record.auth_name,
            "code": record.code,
            "name": record.name,
            "description": tr.text(record.data, "Description"),
            # A text definition replaces the structured columns; proj.db's
            # CHECK constraints require them to be NULL when one is present.
            "coordinate_system_auth_name": None,
            "coordinate_system_code": None,
            "text_definition": definition,
            "deprecated": tr.deprecated_flag(record.data),
        }
        if table == "projected_crs":
            row |= {
                "geodetic_crs_auth_name": None,
                "geodetic_crs_code": None,
                "conversion_auth_name": None,
                "conversion_code": None,
            }
            projected += 1
        else:
            row |= {
                "type": df.geodetic_type(base),
                "datum_auth_name": None,
                "datum_code": None,
            }
            geodetic += 1

        context.stage([(table, row)])
        context.annotate(
            ObjectKey(table=table, auth_name=record.auth_name, code=record.code),
            record,
        )

    logger.info(
        "bound CRS: %d read (%d geodetic, %d projected)",
        geodetic + projected,
        geodetic,
        projected,
    )


def _definition(context: OsduBuildContext, record: Record) -> tuple[str, CRS]:
    """Build the BOUNDCRS WKT for one bound CRS, with the base CRS it wraps.

    Raises:
        UnreadableDefinitionError: If any of the three definitions cannot be
            read, or the transformation cannot be reduced to a single step, or
            PROJ will not assemble the result.
    """
    base_auth, base_code = tr.authority_code(record.data.get("SourceCRS"))
    base = _crs(context, base_auth, base_code, f"{record.described} base CRS")

    operation_auth, operation_code = tr.authority_code(
        record.data.get("Transformation")
    )
    operation_record = context.catalog.operation(operation_auth, operation_code)
    operation = _operation(
        context, operation_auth, operation_code, f"{record.described} transformation"
    )

    hub_auth, hub_code = (
        tr.authority_code(
            (operation_record.data if operation_record else {}).get("TargetCRS")
        )
        if operation_record
        else (None, None)
    )
    hub = _crs(context, hub_auth, hub_code, f"{record.described} hub CRS")

    try:
        operation = _single_step(operation)
    except NotCollapsibleError as exc:
        # Logged as an error, not merely skipped: the catalogue defines a bound
        # CRS that PROJ cannot represent, which is a defect in the definition
        # rather than an object this workflow chose not to model.
        logger.error("%s cannot be imported: %s", record.described, exc)
        raise UnreadableDefinitionError(str(exc)) from exc

    try:
        return str(BoundCRS(base, hub, operation).to_wkt()), base
    except CRSError as exc:
        raise UnreadableDefinitionError(
            f"{record.described} is not a valid BOUNDCRS: {exc}"
        ) from exc


def _crs(
    context: OsduBuildContext, auth: str | None, code: str | None, described: str
) -> CRS:
    """Read a CRS from the catalogue, or from what PROJ already knows.

    A bound CRS's base and hub are either defined by this catalogue or already
    defined by the EPSG dataset PROJ ships with; there is nowhere else to look.

    Raises:
        UnreadableDefinitionError: If neither source has a readable definition.
    """
    if not auth or code is None:
        raise UnreadableDefinitionError(f"{described} is not referenced")
    if (found := context.catalog.crs(auth, code)) is not None:
        return df.parse_crs(tr.wkt(found.data), described)
    try:
        return CRS.from_authority(auth, code)
    except CRSError as exc:
        raise UnreadableDefinitionError(
            f"{described} {auth}:{code} is in neither the catalogue nor PROJ's "
            f"own database: {exc}"
        ) from exc


def _operation(
    context: OsduBuildContext, auth: str | None, code: str | None, described: str
) -> CoordinateOperation:
    """Read a transformation from the catalogue, or from what PROJ already knows.

    Raises:
        UnreadableDefinitionError: If neither source has a readable definition.
    """
    if not auth or code is None:
        raise UnreadableDefinitionError(f"{described} is not referenced")
    if (found := context.catalog.operation(auth, code)) is not None:
        return df.parse_operation(tr.wkt(found.data), described)
    try:
        return CoordinateOperation.from_authority(auth, code)
    except CRSError as exc:
        raise UnreadableDefinitionError(
            f"{described} {auth}:{code} is in neither the catalogue nor PROJ's "
            f"own database: {exc}"
        ) from exc


def _single_step(operation: CoordinateOperation) -> CoordinateOperation:
    """Return an operation PROJ can embed, collapsing a chain if it is one."""
    if operation.to_json_dict().get("type") != "ConcatenatedOperation":
        return operation
    return collapse_concatenated(operation)
