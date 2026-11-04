"""Transformations shared by conversions that resolve the same one again.

Private to the package, used by :mod:`geodetic_engine.geojson`, which resolves
a transformation for each document it converts. Resolving one takes PROJ
milliseconds, as long as converting thousands of positions, so a service
converting many small documents between the same CRSs resolves it once.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from functools import lru_cache
from typing import Any

from pyproj.crs import CoordinateOperation

from geodetic_engine._referencing.database import database_identity
from geodetic_engine.geodesy.crs import CoordinateReferenceSystem
from geodetic_engine.geodesy.operation import StatedOperation, operation_references
from geodetic_engine.geodesy.transformation import Transformation


def shared_transformation(
    source: CoordinateReferenceSystem,
    target: CoordinateReferenceSystem,
    operation: Any = None,
) -> Transformation:
    """``Transformation(source, target, operation)``, resolved once while in use.

    Shared by what each CRS is, its PROJJSON and the input it was resolved
    from, by the operation and by the active database, as
    :func:`~geodetic_engine.geodesy.transform` shares its own. A stated
    operation is resolved every time.

    Raises:
        As :class:`~geodetic_engine.geodesy.Transformation`; a failure is not
        kept, so it is raised again the next time.
    """
    references = () if operation is None else operation_references(operation)
    if any(
        isinstance(reference, StatedOperation | CoordinateOperation)
        for reference in references
    ):
        return Transformation(source, target, references)
    key = (
        source.definition,
        source.crs.to_json(),
        target.definition,
        target.crs.to_json(),
        references,
        database_identity(),
    )
    return _resolved(_Pair(key, source, target, references))


@dataclass(frozen=True, slots=True)
class _Pair:
    """The CRSs and operation of a transformation, equal by ``key`` alone."""

    key: tuple[Any, ...]
    source: CoordinateReferenceSystem = field(compare=False)
    target: CoordinateReferenceSystem = field(compare=False)
    references: tuple[Any, ...] = field(compare=False)


@lru_cache(maxsize=64)
def _resolved(pair: _Pair) -> Transformation:
    return Transformation(pair.source, pair.target, pair.references or None)
