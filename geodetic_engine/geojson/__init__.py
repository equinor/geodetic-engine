"""Coordinate conversion of GeoJSON documents.

:func:`convert_geojson` converts every position of an RFC 7946 GeoJSON, OGC
JSON-FG or OSDU AnyCrs GeoJSON document to another CRS, keeping its
structure, ``id``, ``properties`` and foreign members. The positions of each
source CRS are transformed in one batch, with the guarantees points have.

Public entry points:

* :func:`convert_geojson` -- convert a document; returns a
  :class:`GeoJsonResult`.
* :func:`convert_geojson_request` -- the OSDU CRS conversion service's
  ``convertGeoJson``, request in and response out.
* :class:`GeoJsonError` -- base class of every failure to read a document,
  naming where in it the problem is.
* :class:`InvalidGeoJsonError`, :class:`UnsupportedGeoJsonError` and
  :class:`GeoJsonCrsError` -- not GeoJSON, not converted yet, and a CRS not
  stated or stated twice.
"""

from geodetic_engine.geojson._convert import GeoJsonResult, convert_geojson
from geodetic_engine.geojson._osdu import convert_geojson_request
from geodetic_engine.geojson.errors import (
    GeoJsonCrsError,
    GeoJsonError,
    InvalidGeoJsonError,
    UnsupportedGeoJsonError,
)

__all__ = [
    "GeoJsonCrsError",
    "GeoJsonError",
    "GeoJsonResult",
    "InvalidGeoJsonError",
    "UnsupportedGeoJsonError",
    "convert_geojson",
    "convert_geojson_request",
]
