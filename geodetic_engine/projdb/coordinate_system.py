"""Coordinate systems and their axes.

A coordinate system carries the axis order and units that every coordinate in a
CRS is expressed in, so axis order and unit references are taken from the
authority verbatim and never inferred.
"""

from __future__ import annotations

import logging
from typing import Any

from geodetic_engine.projdb import translate as tr
from geodetic_engine.projdb.context import BuildContext

logger = logging.getLogger(__name__)

ENDPOINT = "CoordSystem"
TABLE = "coordinate_system"


def collect(context: BuildContext) -> None:
    """Import coordinate systems and axes for the configured authorities.

    Axes are written in the order the authority declares through the ``Order``
    field; they are never re-sorted, because axis order is part of the CRS
    definition.

    Args:
        context: The active build context.
    """
    system_rows: list[dict[str, Any]] = []
    axis_rows: list[dict[str, Any]] = []

    for summary in context.client.iter_collection(
        ENDPOINT, authorities=context.config.authorities
    ):
        obj = context.client.detail(summary)
        auth = tr.auth_name(obj)
        code = tr.code(obj)
        if not context.is_new(TABLE, auth, code):
            continue
        assert code is not None

        axes = obj.get("Axis") or []
        dimension = int(obj.get("Dimension") or len(axes))
        if not axes:
            context.skip(TABLE, auth, code, obj, "coordinate system has no axes")
            continue
        if len(axes) != dimension:
            context.skip(
                TABLE,
                auth,
                code,
                obj,
                f"declares dimension {dimension} but has {len(axes)} axes",
            )
            continue

        system_rows.append(
            {
                "auth_name": auth,
                "code": code,
                "type": _cs_type(obj),
                "dimension": dimension,
            }
        )
        key = context.record(TABLE, auth, code)
        context.annotate(key, obj)

        for order, axis in enumerate(
            sorted(axes, key=lambda a: int(a.get("Order") or 0)), start=1
        ):
            unit_auth, unit_code = context.resolve_link(
                axis.get("Unit"),
                tables="unit_of_measure",
                referenced_by=f"axis {order} of coordinate system {auth}:{code}",
            )
            axis_rows.append(
                {
                    "auth_name": auth,
                    "code": f"{code}_{order}",
                    "name": tr.text(axis, "Name") or "unknown",
                    "abbrev": tr.text(axis, "Abbreviation") or "",
                    "orientation": tr.text(axis, "Orientation") or "",
                    "coordinate_system_auth_name": auth,
                    "coordinate_system_code": code,
                    "coordinate_system_order": int(axis.get("Order") or order),
                    "uom_auth_name": unit_auth,
                    "uom_code": unit_code,
                }
            )

    context.writer.insert(TABLE, system_rows)
    context.writer.insert("axis", axis_rows)
    logger.info(
        "coordinate systems: %d imported with %d axes",
        len(system_rows),
        len(axis_rows),
    )


def _cs_type(obj: dict[str, Any]) -> str:
    """Map the authority's coordinate system type onto PROJ's vocabulary."""
    raw = str(obj.get("Type") or "").strip().casefold()
    return {
        "cartesian": "Cartesian",
        "ellipsoidal": "ellipsoidal",
        "vertical": "vertical",
        "spherical": "spherical",
        "ordinal": "ordinal",
        "parametric": "parametric",
        "temporal": "temporal",
        "temporalcount": "temporalCount",
        "temporalmeasure": "temporalMeasure",
        "datetimetemporal": "DateTimeTemporal",
    }.get(raw, raw)
