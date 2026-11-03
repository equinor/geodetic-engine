"""Exceptions raised while reading a unit."""

from geodetic_engine.errors import GeodeticEngineError


class UnitError(GeodeticEngineError, ValueError):
    """A unit was not recognised, or is not a unit of the quantity needed.

    Never resolved to a default: a length read in the wrong unit moves every
    position by a plausible looking amount.
    """
