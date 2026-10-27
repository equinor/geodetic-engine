"""Exceptions raised while reading a GeoJSON document.

CRS and coordinate operation failures keep the types
:mod:`geodetic_engine.geodesy` raises for them; these cover the document itself.
Each names where in the document the problem is, so that it can be found in a
document holding many features.
"""

from __future__ import annotations

from typing import Any

from geodetic_engine.errors import GeodeticEngineError


class GeoJsonError(GeodeticEngineError):
    """Base class for all GeoJSON document failures.

    The message starts with :attr:`path`.

    Attributes:
        path: Where in the document, as a JSON path such as
            ``$.features[3].geometry.coordinates[0][2]``. ``$`` is the
            document itself.
        reason: What is wrong there.
    """

    def __init__(self, path: str, reason: str) -> None:
        super().__init__(f"{path}: {reason}")
        self.path = path
        self.reason = reason

    def __reduce__(self) -> tuple[Any, ...]:
        return type(self), (self.path, self.reason)


class InvalidGeoJsonError(GeoJsonError):
    """The document is not GeoJSON, or not OSDU AnyCrs GeoJSON.

    A member has the wrong type, a position is not two or three finite numbers,
    a line has fewer than two positions, a linear ring is not closed or has
    fewer than four, or the document mixes plain and AnyCrs types.
    """


class UnsupportedGeoJsonError(GeoJsonError):
    """The document is valid, but states something not converted yet.

    For example, a JSON-FG ``coordRefSys`` or ``place``, a GeoJSON 2008 ``crs``
    member naming a CRS other than CRS84, positions with a measure, or heights
    in a vertical CRS. Converting it as if it did not would put positions in
    the wrong place, so it is refused instead.
    """


class GeoJsonCrsError(GeoJsonError):
    """The CRS of a document's positions is not stated, or is stated twice.

    An AnyCrs document that states no CRS, or a CRS passed as ``source_crs``
    that differs from the one the document states.
    """
