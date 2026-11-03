"""The OSDU CRS conversion service's ``convertGeoJson``, request in, response out.

Private to :mod:`geodetic_engine.geojson`, which exports
:func:`convert_geojson_request`.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Final

from geodetic_engine.geodesy import CoordinateReferenceSystem
from geodetic_engine.geojson._convert import convert_geojson
from geodetic_engine.geojson._scope import is_wgs84_lonlat

_COLLECTIONS: Final = ("FeatureCollection", "AnyCrsFeatureCollection")


def convert_geojson_request(body: Mapping[str, Any]) -> dict[str, Any]:
    """Answer a ``convertGeoJson`` request as the OSDU service would.

    The request and response are those of ``POST /v4/convertGeoJson``, so
    results can be compared with the service's, or used in their place. The
    output follows the service's rule: plain GeoJSON when ``toCRS`` is WGS 84
    longitude and latitude, with heights in metres, AnyCrs otherwise. A
    position that cannot be transformed is NaN, as there, and counted out of
    ``successCount``.

    Unlike the service, the package's rules hold: a datum change needs an
    operation, named by ``transformation`` or bound in an early-bound CRS; a
    missing grid is an error; heights keep ``persistableReferenceUnitZ``.

    Args:
        body: The request: ``featureCollection``, ``toCRS``, and optionally
            ``toUnitZ`` and ``transformation``.

    Returns:
        The response: ``featureCollection``, ``successCount``, ``totalCount``
        and ``operationsApplied``.

    Raises:
        TypeError: If ``body`` is not a mapping.
        ValueError: If a member is missing, or the collection is not one.
        GeoJsonError, GeodesyError: As :func:`convert_geojson` raises them.
    """
    if not isinstance(body, Mapping):
        raise TypeError(f"a request is a JSON object, not {type(body).__name__}")
    collection = body.get("featureCollection")
    to_crs = body.get("toCRS")
    if (
        not isinstance(collection, Mapping)
        or collection.get("type") not in _COLLECTIONS
    ):
        raise ValueError(
            "featureCollection is a FeatureCollection or AnyCrsFeatureCollection"
        )
    if not isinstance(to_crs, str) or not to_crs.strip():
        raise ValueError("toCRS is a CRS record id or persistableReference")
    target = CoordinateReferenceSystem.from_user_input(to_crs)
    plain = is_wgs84_lonlat(target)
    result = convert_geojson(
        collection,
        to_crs,
        operation=body.get("transformation") or None,
        # Plain GeoJSON heights are in metres, whatever toUnitZ says.
        target_unit_z=None if plain else body.get("toUnitZ") or None,
        output="geojson" if plain else "anycrs",
        on_error="nan",
    )
    return {
        "featureCollection": result.document,
        "successCount": result.success_count,
        "totalCount": result.total_count,
        "operationsApplied": list(result.operations_applied),
    }
