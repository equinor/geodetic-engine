"""Comparing CRSs, combining them, and reading their codes, axes and longitude.

Private to the package, shared by :mod:`geodetic_engine.geodesy.geometry` and
:mod:`geodetic_engine.geojson`, which both check a CRS stated in data against
the one a caller gives, both write a CRS back by its code, both put positions
in a CRS's declared axis order, both name a CRS in their messages, and both
cut positions at a geographic CRS's antimeridian.
"""

from __future__ import annotations

import math
from functools import lru_cache
from typing import Any

from pyproj import CRS
from pyproj.crs import CompoundCRS
from pyproj.exceptions import CRSError

from geodetic_engine.geodesy.crs import CoordinateReferenceSystem
from geodetic_engine.geodesy.errors import UnsupportedCRSError


def same_crs(
    first: CoordinateReferenceSystem, second: CoordinateReferenceSystem
) -> bool:
    """Whether a CRS repeats another: the same positions at the same values.

    Equivalent by PROJ once both declare their axes in the order this package
    gives values in: longitude or easting first, then latitude or northing,
    then any height. So EPSG:4326 repeats OGC:CRS84, and EPSG:2193, declared
    northing first, repeats an OSDU persistableReference for it, declared
    easting first. PROJ's own ``ignore_axis_order`` reaches geographic CRSs
    only.

    Everything else counts: axis directions, units, the number of axes, and a
    transformation a CRS is bound with. A CRS counting longitude west does not
    repeat CRS84, and neither does a CRS that only states the same authority
    code: a persistableReference is defined by its parameters, not its code.

    Comparing takes milliseconds, so each pair of definitions is compared once.
    """
    return _same_definitions(first.crs.to_json(), second.crs.to_json())


@lru_cache(maxsize=128)
def _same_definitions(first: str, second: str) -> bool:
    """:func:`same_crs` for two CRSs given by their PROJJSON, all it reads."""
    if first == second:
        return True
    return _in_value_order(CRS.from_json(first)).equals(
        _in_value_order(CRS.from_json(second)), ignore_axis_order=True
    )


def compound(
    horizontal: CoordinateReferenceSystem, vertical: CoordinateReferenceSystem
) -> CoordinateReferenceSystem:
    """The compound CRS of a horizontal CRS and a vertical one.

    Raises:
        UnsupportedCRSError: If ``vertical`` is not a vertical CRS, or
            ``horizontal`` is not two-dimensional.
    """
    if not vertical.crs.is_vertical:
        raise UnsupportedCRSError(f"{label(vertical)} is not a vertical CRS")
    if horizontal.dimension != 2:
        raise UnsupportedCRSError(
            f"{label(horizontal)} has {horizontal.dimension} axes; a compound "
            "CRS joins a two-dimensional horizontal CRS to a vertical one"
        )
    name = f"{horizontal.name} + {vertical.name}"
    try:
        joined = CompoundCRS(name=name, components=[horizontal.crs, vertical.crs])
    except CRSError as error:
        raise UnsupportedCRSError(f"cannot join {name!r}: {error}") from error
    return CoordinateReferenceSystem(joined, name)


def longitude(crs: CoordinateReferenceSystem) -> tuple[float, float] | None:
    """Half a turn of a geographic CRS's longitude, and its poles' latitude.

    Both in the CRS's own units, or None if its first two values are not a
    longitude and a latitude.
    """
    if not crs.is_geographic or crs.dimension < 2:
        return None
    lon_axis, lat_axis = (crs.axes[index] for index in crs.value_axis_order[:2])
    if lon_axis.direction.lower() not in ("east", "west"):
        return None
    if lat_axis.direction.lower() not in ("north", "south"):
        return None
    return (
        math.pi / lon_axis.unit_conversion_factor,
        (math.pi / 2) / lat_axis.unit_conversion_factor,
    )


def integer_code(crs: CoordinateReferenceSystem) -> tuple[str, int] | None:
    """The authority and integer code that identify ``crs``, if it has them.

    Only a code PROJ is certain of counts: a CRS defined by WKT that merely
    resembles a registered one has none, since writing that code would claim
    a definition the data does not have.
    """
    identity = crs.crs.to_authority(min_confidence=100)
    if identity is None:
        return None
    authority, code = identity
    if not code.isdigit():
        return None
    return authority, int(code)


def axis_permutation(
    crs: CoordinateReferenceSystem, width: int, *, to_values: bool
) -> list[int]:
    """Columns that reorder positions of ``width`` values for ``crs``.

    From its declared axis order into value order, or back for ``to_values``
    false. Values past the CRS's axes stay where they are.

    Raises:
        ValueError: If the CRS's first ``width`` axes are not its first
            ``width`` values, so that the positions have no declared order.
    """
    order = crs.value_axis_order
    count = min(width, len(order))
    moved = list(order[:count])
    if sorted(moved) != list(range(count)):
        raise ValueError(
            f"positions of {width} values cannot be put in the declared order of "
            f"{label(crs)}, which has {len(order)} axes"
        )
    if not to_values:
        moved = [moved.index(axis) for axis in range(count)]
    return [*moved, *range(count, width)]


def label(crs: CoordinateReferenceSystem) -> str:
    """``crs`` as a message names it: by its authority and code, else its name."""
    return crs.authority_code or repr(crs.name)


def _in_value_order(crs: CRS) -> CRS:
    """``crs`` with every coordinate system in it declared in value order."""
    definition = crs.to_json_dict()
    if not _declare_in_value_order(definition):
        return crs
    try:
        return CRS.from_json_dict(definition)
    except CRSError:
        return crs


def _declare_in_value_order(node: object) -> bool:
    """Reorder each CRS's axes in a PROJJSON node into value order, in place.

    Every node is visited, whatever an earlier one did.

    Returns:
        Whether any axis moved.
    """
    moved = False
    if isinstance(node, list):
        for item in node:
            moved = _declare_in_value_order(item) or moved
        return moved
    if not isinstance(node, dict):
        return False
    system = node.get("coordinate_system")
    if isinstance(system, dict) and isinstance(system.get("axis"), list):
        order = _value_order(node)
        if order is not None and order != tuple(range(len(order))):
            axes = system["axis"]
            system["axis"] = [axes[index] for index in order]
            # An identifier would claim the authority's own axis order.
            node.pop("id", None)
            node.pop("ids", None)
            moved = True
    for key, child in node.items():
        if key != "coordinate_system":
            moved = _declare_in_value_order(child) or moved
    return moved


def _value_order(node: dict[str, Any]) -> tuple[int, ...] | None:
    try:
        crs = CRS.from_json_dict(node)
    except CRSError:
        return None
    return CoordinateReferenceSystem(crs, crs.name).value_axis_order
