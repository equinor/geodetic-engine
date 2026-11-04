"""Write Shapely geometries as GeoJSON geometry objects.

Private to the package, shared by :mod:`geodetic_engine.geodesy.geometry`,
which writes GeoJSON geometries, and :mod:`geodetic_engine.geojson`, which
writes a geometry cut at the antimeridian back into its document. Each number
is written as Shapely holds it, so it reads back bit for bit. Shapely is
imported only to write, so that converting GeoJSON loads it only for a
document that is cut.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, cast, overload

from geodetic_engine.geodesy.errors import GeometryError

if TYPE_CHECKING:
    import shapely
    from shapely.geometry.base import BaseGeometry


@overload
def geojson_object(geometry: shapely.Geometry) -> dict[str, Any]: ...


@overload
def geojson_object(geometry: None) -> None: ...


def geojson_object(geometry: shapely.Geometry | None) -> dict[str, Any] | None:
    """``geometry`` as a GeoJSON geometry object; None for None.

    Raises:
        GeometryError: If the geometry has M values, which GeoJSON has no
            place for.
    """
    import shapely

    if geometry is None:
        return None
    if shapely.has_m(geometry):
        raise GeometryError(
            "GeoJSON positions have no place for M values; write wkt or wkb"
        )
    interface = cast("BaseGeometry", geometry).__geo_interface__
    return cast("dict[str, Any]", _lists(interface))


def _lists(value: Any) -> Any:
    """``value`` with tuples as lists and a LinearRing as a LineString."""
    kind = type(value)
    if kind is tuple or kind is list:
        # A coordinate is a float, read as it is: most values are.
        return [item if type(item) is float else _lists(item) for item in value]
    if isinstance(value, Mapping):
        out = {key: _lists(item) for key, item in value.items()}
        if out.get("type") == "LinearRing":
            out["type"] = "LineString"
        return out
    if isinstance(value, tuple | list):
        return [_lists(item) for item in value]
    return value
