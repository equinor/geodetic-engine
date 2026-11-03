"""OSDU record ids: kept at this path, defined in the lower layer.

The module moved to ``geodetic_engine._referencing.osdu_id`` so that
:mod:`geodetic_engine.persistablereference` can read ids without importing
``geodesy``. Its names are the same objects here.
"""

from geodetic_engine._referencing.osdu_id import (
    CRS_ENTITY,
    SCHEME,
    TRANSFORMATION_ENTITY,
    OsduId,
    parse_osdu_id,
    resolve_osdu_id,
    unresolved_reason,
)

__all__ = [
    "CRS_ENTITY",
    "SCHEME",
    "TRANSFORMATION_ENTITY",
    "OsduId",
    "parse_osdu_id",
    "resolve_osdu_id",
    "unresolved_reason",
]
