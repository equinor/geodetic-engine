"""Bound CRSs, and the transformation each one carries.

A bound CRS is a CRS packaged together with the single transformation that ties
it to a hub, almost always WGS 84. It is early binding made explicit: the
operation is part of the CRS definition rather than something chosen at
transformation time, which is exactly the guarantee this workflow wants.

proj.db has no bound CRS table. PROJ stores one as an ordinary ``geodetic_crs``
or ``projected_crs`` row whose ``text_definition`` holds the whole ``BOUNDCRS``
WKT, and whose coordinate system and datum columns are therefore NULL, which the
table's own CHECK constraints require.

The WKT is assembled here with pyproj rather than taken from the register's own
bound CRS export, so that the transformation actually embedded is one this
package has inspected. That matters for a bound CRS defined over a concatenated
operation: PROJ cannot embed a chain, so the chain is first collapsed into a
single equivalent step and refused if it does not compose. See
:mod:`geodetic_engine.geodesy.utils.helmert`.
"""

from __future__ import annotations

import logging
from typing import Any

from pyproj import CRS
from pyproj.crs import BoundCRS, CoordinateOperation
from pyproj.exceptions import CRSError

from geodetic_engine.geodesy.errors import NotCollapsibleError
from geodetic_engine.geodesy.utils import collapse_concatenated
from geodetic_engine.projdb import translate as tr
from geodetic_engine.projdb.context import BuildContext

logger = logging.getLogger(__name__)

# The register's bound CRS collection. Its summaries carry a null self link, so
# detail objects are addressed by code rather than followed from the summary.
_ENDPOINT = "BoundCoordRefSystem"


def collect_bound(context: BuildContext) -> None:
    """Import bound CRSs as text definitions on the CRS tables.

    Each one needs three definitions from the register: its base CRS, the
    transformation, and the transformation's target. Any bound CRS whose parts
    cannot be resolved, or whose transformation cannot be reduced to a single
    step, is skipped and reported rather than written in a weakened form.
    """
    geodetic_rows: list[dict[str, Any]] = []
    projected_rows: list[dict[str, Any]] = []

    for summary in context.client.iter_collection(
        _ENDPOINT, authorities=context.config.authorities
    ):
        code = tr.code(summary)
        if code is None:
            continue
        auth = tr.auth_name(summary)
        # The bound CRS collection advertises a null self link, so the detail
        # URL is built from the code rather than followed from the summary.
        obj = context.client.get_object(
            f"{context.config.georepository.api_url.rstrip('/')}"
            f"/api/v1/{_ENDPOINT}/{code}"
        )

        built = _definition(context, obj, auth, code)
        if built is None:
            continue
        definition, base = built

        # The register leaves Kind unset on a bound CRS, so which table it
        # belongs in is taken from the base CRS PROJ actually parsed.
        table = "projected_crs" if base.is_projected else "geodetic_crs"
        if not context.is_new(table, auth, code):
            continue

        row = {
            "auth_name": auth,
            "code": code,
            "name": tr.text(obj, "Name") or "unknown",
            "description": tr.text(obj, "Remark", "Description"),
            # A text definition replaces the structured columns; proj.db's CHECK
            # constraints require them to be NULL when one is present.
            "coordinate_system_auth_name": None,
            "coordinate_system_code": None,
            "text_definition": definition,
            "deprecated": tr.deprecated_flag(obj),
        }
        if table == "projected_crs":
            projected_rows.append(
                row
                | {
                    "geodetic_crs_auth_name": None,
                    "geodetic_crs_code": None,
                    "conversion_auth_name": None,
                    "conversion_code": None,
                }
            )
        else:
            geodetic_rows.append(
                row
                | {
                    "type": _geodetic_type(base),
                    "datum_auth_name": None,
                    "datum_code": None,
                }
            )
        context.annotate(context.record(table, auth, code), obj)

    context.writer.insert("geodetic_crs", geodetic_rows)
    context.writer.insert("projected_crs", projected_rows)
    logger.info(
        "bound CRS: %d imported (%d geodetic, %d projected)",
        len(geodetic_rows) + len(projected_rows),
        len(geodetic_rows),
        len(projected_rows),
    )


def _geodetic_type(base: CRS) -> str:
    """The geodetic_crs.type vocabulary term matching the base CRS."""
    if base.is_geocentric:
        return "geocentric"
    return "geographic 3D" if len(base.axis_info) > 2 else "geographic 2D"


def _definition(
    context: BuildContext,
    obj: dict[str, Any],
    auth: str,
    code: str,
) -> tuple[str, CRS] | None:
    """Build the BOUNDCRS WKT for one bound CRS, with the base CRS it wraps."""
    described = f"bound CRS {auth}:{code}"
    # The table is not yet known, since it is decided by the base CRS; a skip
    # recorded before that point is attributed to the geodetic CRS table.
    table = "geodetic_crs"

    base = _crs_from_register(context, obj.get("BaseCoordRefSystem"))
    if base is None:
        context.skip(table, auth, code, obj, f"{described} has no readable base CRS")
        return None

    transformation_obj = context.client.resolve(obj.get("Transformation"))
    if not transformation_obj:
        context.skip(
            table, auth, code, obj, f"{described} states no transformation to a hub"
        )
        return None

    operation = _operation_from_register(context, transformation_obj)
    if operation is None:
        context.skip(
            table,
            auth,
            code,
            obj,
            f"{described} has a transformation the register would not export as WKT",
        )
        return None

    hub = _crs_from_register(context, transformation_obj.get("TargetCrs"))
    if hub is None:
        context.skip(
            table,
            auth,
            code,
            obj,
            f"{described} has a transformation with no readable target CRS",
        )
        return None

    try:
        operation = _single_step(operation)
    except NotCollapsibleError as exc:
        # Logged as an error, not merely skipped: the register defines a bound
        # CRS that PROJ cannot represent, which is a defect in the definition
        # rather than an object this workflow chose not to model.
        logger.error("%s cannot be imported: %s", described, exc)
        context.skip(table, auth, code, obj, str(exc))
        return None

    try:
        return str(BoundCRS(base, hub, operation).to_wkt()), base
    except CRSError as exc:
        logger.error("%s could not be assembled as a BOUNDCRS: %s", described, exc)
        context.skip(table, auth, code, obj, f"{described} is not a valid BOUNDCRS")
        return None


def _single_step(operation: CoordinateOperation) -> CoordinateOperation:
    """Return an operation PROJ can embed, collapsing a chain if it is one."""
    if operation.to_json_dict().get("type") != "ConcatenatedOperation":
        return operation
    return collapse_concatenated(operation)


def _crs_from_register(context: BuildContext, link: Any) -> CRS | None:
    """Fetch a CRS's own WKT from the register and parse it."""
    obj = context.client.resolve(link)
    if not obj:
        return None
    wkt = context.client.wkt(obj)
    if not wkt:
        return None
    try:
        return CRS.from_wkt(wkt)
    except CRSError as exc:
        logger.warning(
            "could not parse WKT for %s:%s: %s", tr.auth_name(obj), tr.code(obj), exc
        )
        return None


def _operation_from_register(
    context: BuildContext, obj: dict[str, Any]
) -> CoordinateOperation | None:
    """Fetch a transformation's own WKT from the register and parse it."""
    wkt = context.client.wkt(obj)
    if not wkt:
        return None
    try:
        return CoordinateOperation.from_string(wkt)
    except CRSError as exc:
        logger.warning(
            "could not parse WKT for transformation %s:%s: %s",
            tr.auth_name(obj),
            tr.code(obj),
            exc,
        )
        return None
