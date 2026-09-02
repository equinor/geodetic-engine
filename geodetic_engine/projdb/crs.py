"""Coordinate reference systems.

Every CRS row records which coordinate system supplies its axis order and units
and which datum it is referenced to. Those references are required, not
optional: a CRS whose datum cannot be resolved is skipped and reported rather
than written with a dangling reference that PROJ would later fail on, or worse,
silently treat as a different datum.
"""

from __future__ import annotations

import logging
from collections.abc import Iterator
from typing import Any

from geodetic_engine.projdb import translate as tr
from geodetic_engine.projdb.context import BuildContext
from geodetic_engine.projdb.errors import MissingReferencedObjectError

logger = logging.getLogger(__name__)

# Georepository Kind values mapped onto the proj.db geodetic_crs type vocabulary.
_GEODETIC_KIND = {
    "geographic 2d": "geographic 2D",
    "geographic2d": "geographic 2D",
    "geographic 3d": "geographic 3D",
    "geographic3d": "geographic 3D",
    "geocentric": "geocentric",
}

# EPSG deriving conversion methods that change only how a coordinate is
# expressed, never where the surface is. A CRS derived by one of these is fully
# described by its base datum plus its own coordinate system, which is how
# proj.db stores it, since proj.db has no derived CRS table. Anything else, an
# offset or a rotation in particular, would be lost by that flattening.
_VERTICAL_AXIS_ONLY_METHODS = frozenset(
    {
        "1068",  # Height Depth Reversal
        "1069",  # Change of Vertical Unit
    }
)
_GEODETIC_AXIS_ONLY_METHODS = frozenset(
    {
        "9659",  # Geographic3D to Geographic2D conversion
        "9843",  # Axis Order Reversal (2D)
        "9844",  # Axis Order Reversal (Geographic3D horizontal)
    }
)


def collect_geodetic(context: BuildContext) -> None:
    """Import geographic and geocentric CRSs, including derived ones.

    A derived geographic CRS states a base CRS and a deriving conversion rather
    than a datum, and is flattened onto the base datum in the same way as a
    derived vertical CRS. See :func:`_datum_via_base`.
    """
    rows: list[dict[str, Any]] = []
    for obj, auth, code in _candidates(
        context, "GeodeticCoordRefSystem", "geodetic_crs"
    ):
        kind = _GEODETIC_KIND.get(str(obj.get("Kind") or "").strip().casefold())
        if kind is None:
            context.skip(
                "geodetic_crs",
                auth,
                code,
                obj,
                f"unsupported geodetic CRS kind {obj.get('Kind')!r}",
            )
            continue
        row = _base_crs_row(context, obj, auth, code, "geodetic_crs")
        if row is None:
            continue
        try:
            datum = _geodetic_datum(context, obj, auth, code)
        except MissingReferencedObjectError as exc:
            context.skip("geodetic_crs", auth, code, obj, str(exc))
            continue
        rows.append(
            row
            | {
                "type": kind,
                "datum_auth_name": datum[0],
                "datum_code": datum[1],
                "text_definition": None,
            }
        )
        _finalise(context, "geodetic_crs", obj, auth, code)
    context.writer.insert("geodetic_crs", rows)
    logger.info("geodetic CRS: %d imported", len(rows))


def _geodetic_datum(
    context: BuildContext, obj: dict[str, Any], auth: str, code: str
) -> tuple[str, str]:
    """Resolve the datum of a geodetic CRS, directly or through its base CRS."""
    described = f"geodetic CRS {auth}:{code}"
    if datum_link := (obj.get("Datum") or obj.get("DatumEnsemble")):
        return context.resolve_link(
            datum_link, tables="geodetic_datum", referenced_by=described
        )
    return _datum_via_base(
        context,
        obj,
        described=described,
        crs_table="geodetic_crs",
        safe_methods=_GEODETIC_AXIS_ONLY_METHODS,
    )


def collect_projected(context: BuildContext) -> None:
    """Import projected CRSs, requiring both a base CRS and a conversion."""
    rows: list[dict[str, Any]] = []
    for obj, auth, code in _candidates(
        context, "ProjectedCoordRefSystem", "projected_crs"
    ):
        row = _base_crs_row(context, obj, auth, code, "projected_crs")
        if row is None:
            continue
        base = obj.get("BaseCoordRefSystem") or {}
        projection = obj.get("Projection") or {}
        try:
            base_ref = context.resolve_link(
                base,
                tables="geodetic_crs",
                referenced_by=f"projected CRS {auth}:{code}",
            )
            conversion_ref = context.resolve_link(
                projection,
                tables="conversion_table",
                referenced_by=f"projected CRS {auth}:{code}",
            )
        except MissingReferencedObjectError as exc:
            context.skip("projected_crs", auth, code, obj, str(exc))
            continue
        rows.append(
            row
            | {
                "geodetic_crs_auth_name": base_ref[0],
                "geodetic_crs_code": base_ref[1],
                "conversion_auth_name": conversion_ref[0],
                "conversion_code": conversion_ref[1],
                "text_definition": tr.text(obj, "Wkt"),
            }
        )
        _finalise(context, "projected_crs", obj, auth, code)
    context.writer.insert("projected_crs", rows)
    logger.info("projected CRS: %d imported", len(rows))


def collect_vertical(context: BuildContext) -> None:
    """Import vertical CRSs, including ones derived from another vertical CRS.

    A derived vertical CRS states a base CRS and a deriving conversion instead
    of a datum. proj.db has no derived CRS table, so such a CRS is stored as an
    ordinary vertical CRS carrying the base CRS's datum and its own coordinate
    system. That is only faithful when the conversion changes nothing but the
    axis direction or unit, which the coordinate system already captures; see
    :data:`_AXIS_ONLY_CONVERSION_METHODS`.
    """
    rows: list[dict[str, Any]] = []
    for obj, auth, code in _candidates(
        context, "VerticalCoordRefSystem", "vertical_crs"
    ):
        row = _base_crs_row(context, obj, auth, code, "vertical_crs")
        if row is None:
            continue
        try:
            datum = _vertical_datum(context, obj, auth, code)
        except MissingReferencedObjectError as exc:
            context.skip("vertical_crs", auth, code, obj, str(exc))
            continue
        rows.append(row | {"datum_auth_name": datum[0], "datum_code": datum[1]})
        _finalise(context, "vertical_crs", obj, auth, code)
    context.writer.insert("vertical_crs", rows)
    logger.info("vertical CRS: %d imported", len(rows))


def _vertical_datum(
    context: BuildContext, obj: dict[str, Any], auth: str, code: str
) -> tuple[str, str]:
    """Resolve the datum of a vertical CRS, directly or through its base CRS."""
    described = f"vertical CRS {auth}:{code}"
    if datum_link := obj.get("Datum"):
        return context.resolve_link(
            datum_link, tables="vertical_datum", referenced_by=described
        )
    return _datum_via_base(
        context,
        obj,
        described=described,
        crs_table="vertical_crs",
        safe_methods=_VERTICAL_AXIS_ONLY_METHODS,
    )


def _datum_via_base(
    context: BuildContext,
    obj: dict[str, Any],
    *,
    described: str,
    crs_table: str,
    safe_methods: frozenset[str],
) -> tuple[str, str]:
    """Take a derived CRS's datum from the CRS it is derived from.

    Args:
        context: The active build context.
        obj: The derived CRS.
        described: Human readable identity, for error messages.
        crs_table: proj.db table holding the base CRS.
        safe_methods: Deriving conversion methods that may be flattened away.

    Returns:
            The base CRS's ``(datum_auth_name, datum_code)``.

    Raises:
        MissingReferencedObjectError: If there is no base CRS, the base CRS has
            no datum, or the deriving conversion does more than restate the
            axes.
    """
    base_link = obj.get("BaseCoordRefSystem")
    if not base_link:
        raise MissingReferencedObjectError(
            f"{described} has neither a datum nor a base CRS"
        )

    method = _deriving_method(context, obj)
    if method not in safe_methods:
        raise MissingReferencedObjectError(
            f"{described} is derived from another CRS by EPSG method {method}, "
            "which changes more than the axis order, direction or unit. "
            "Storing it on the base datum would drop that conversion and shift "
            "every coordinate it produces."
        )

    base_auth, base_code = context.resolve_link(
        base_link, tables=crs_table, referenced_by=described
    )
    datum = context.datum_of(crs_table, base_auth, base_code)
    if datum is None:
        raise MissingReferencedObjectError(
            f"{described} is derived from {base_auth}:{base_code}, which has no "
            "datum in the database"
        )
    logger.debug(
        "%s: datum %s:%s taken from base CRS %s:%s",
        described,
        datum[0],
        datum[1],
        base_auth,
        base_code,
    )
    return datum


def _deriving_method(context: BuildContext, obj: dict[str, Any]) -> str | None:
    """Return the EPSG method code of a derived CRS's deriving conversion."""
    conversion = context.client.resolve(obj.get("Conversion"))
    return tr.link_code(conversion.get("Method") or {})


def collect_engineering(context: BuildContext) -> None:
    """Import engineering CRSs."""
    rows: list[dict[str, Any]] = []
    for obj, auth, code in _candidates(
        context, "EngineeringCoordRefSystem", "engineering_crs"
    ):
        row = _base_crs_row(context, obj, auth, code, "engineering_crs")
        if row is None:
            continue
        datum_link = obj.get("Datum") or {}
        try:
            datum = context.resolve_link(
                datum_link,
                tables="engineering_datum",
                referenced_by=f"engineering CRS {auth}:{code}",
            )
        except MissingReferencedObjectError as exc:
            context.skip("engineering_crs", auth, code, obj, str(exc))
            continue
        rows.append(row | {"datum_auth_name": datum[0], "datum_code": datum[1]})
        _finalise(context, "engineering_crs", obj, auth, code)
    context.writer.insert("engineering_crs", rows)
    logger.info("engineering CRS: %d imported", len(rows))


def collect_compound(context: BuildContext) -> None:
    """Import compound CRSs from their horizontal and vertical components.

    The components must already exist; a compound CRS that loses its vertical
    component would silently become a 2D CRS.
    """
    rows: list[dict[str, Any]] = []
    for obj, auth, code in _candidates(
        context, "CompoundCoordRefSystem", "compound_crs"
    ):
        horizontal = obj.get("HorizontalCrs") or {}
        vertical = obj.get("VerticalCrs") or {}
        try:
            horizontal_ref = context.resolve_link(
                horizontal,
                tables=("geodetic_crs", "projected_crs"),
                referenced_by=f"compound CRS {auth}:{code}",
            )
            vertical_ref = context.resolve_link(
                vertical,
                tables="vertical_crs",
                referenced_by=f"compound CRS {auth}:{code}",
            )
        except MissingReferencedObjectError as exc:
            context.skip("compound_crs", auth, code, obj, str(exc))
            continue
        rows.append(
            {
                "auth_name": auth,
                "code": code,
                "name": tr.text(obj, "Name") or "unknown",
                "description": tr.text(obj, "Remark", "Description"),
                "horiz_crs_auth_name": horizontal_ref[0],
                "horiz_crs_code": horizontal_ref[1],
                "vertical_crs_auth_name": vertical_ref[0],
                "vertical_crs_code": vertical_ref[1],
                "deprecated": tr.deprecated_flag(obj),
            }
        )
        _finalise(context, "compound_crs", obj, auth, code)
    context.writer.insert("compound_crs", rows)
    logger.info("compound CRS: %d imported", len(rows))


def _candidates(
    context: BuildContext, endpoint: str, table: str
) -> Iterator[tuple[dict[str, Any], str, str]]:
    """Yield detail objects for new authority objects on a collection endpoint."""
    for summary in context.client.iter_collection(
        endpoint, authorities=context.config.authorities
    ):
        obj = context.client.detail(summary)
        auth, code = tr.auth_name(obj), tr.code(obj)
        if code is None or not context.is_new(table, auth, code):
            continue
        yield obj, auth, code


def _base_crs_row(
    context: BuildContext,
    obj: dict[str, Any],
    auth: str,
    code: str,
    table: str,
) -> dict[str, Any] | None:
    coord_sys = obj.get("CoordSys") or {}
    try:
        cs_ref = context.resolve_link(
            coord_sys,
            tables="coordinate_system",
            referenced_by=f"{table} {auth}:{code}",
        )
    except MissingReferencedObjectError as exc:
        context.skip(table, auth, code, obj, str(exc))
        return None
    return {
        "auth_name": auth,
        "code": code,
        "name": tr.text(obj, "Name") or "unknown",
        "description": tr.text(obj, "Remark", "Description"),
        "coordinate_system_auth_name": cs_ref[0],
        "coordinate_system_code": cs_ref[1],
        "deprecated": tr.deprecated_flag(obj),
    }


def _finalise(
    context: BuildContext, table: str, obj: dict[str, Any], auth: str, code: str
) -> None:
    context.annotate(context.record(table, auth, code), obj)
