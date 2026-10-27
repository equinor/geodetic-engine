"""Coordinate conversion of GeoJSON documents (work in progress, issue #21).

``convert_geojson()`` will convert every position of an RFC 7946 or OSDU
AnyCrs GeoJSON document to another CRS, keeping its structure, ``id``,
``properties`` and foreign members. Until it lands, this package exports only
the exceptions a document can fail with.

Public entry points:

* :class:`GeoJsonError` -- base class of every failure to read a document,
  naming where in it the problem is.
* :class:`InvalidGeoJsonError`, :class:`UnsupportedGeoJsonError` and
  :class:`GeoJsonCrsError` -- not GeoJSON, not converted yet, and a CRS not
  stated or stated twice.
"""

from geodetic_engine.geojson.errors import (
    GeoJsonCrsError,
    GeoJsonError,
    InvalidGeoJsonError,
    UnsupportedGeoJsonError,
)

__all__ = [
    "GeoJsonCrsError",
    "GeoJsonError",
    "InvalidGeoJsonError",
    "UnsupportedGeoJsonError",
]
