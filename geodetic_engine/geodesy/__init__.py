"""Coordinate transformation with the geodetic guarantees this package exists for.

Public entry points:

* :func:`transform` -- transform points in one call.
* :class:`Transformation` -- resolve once, transform many times.
* :func:`available_operations` -- list every coordinate operation PROJ
  offers between two CRSs, to choose an operation from.
* :class:`CoordinateReferenceSystem` -- resolve a CRS and read the axis roles
  and units the EPSG dataset declares for it.
* :class:`TransformationResult` -- coordinates with the provenance that
  produced them.

Coordinate **values** are always ordered ``xy``: longitude before latitude,
easting before northing, then height. A CRS's EPSG-declared axis order is
reported separately, is often different, and is never silently reinterpreted.
Points are passed as plain lists, tuples or 2D numpy arrays -- there is no
coordinate wrapper type to construct first. A result's
:attr:`~TransformationResult.coordinates` behaves the same way on the way out
(indexing, iteration, equality), with
:class:`~geodetic_engine.geodesy.result.Coordinates`'s ``to_list()``,
``to_numpy()`` and ``to_dataframe()`` added for exporting it in a specific
format.

Example:
    >>> from geodetic_engine.geodesy import transform
    >>> result = transform(
    ...     "EPSG:4326", "EPSG:25832", [(10.7522, 59.9139)],
    ...     operation="EPSG:16032",
    ... )
    >>> result.operation.authority_code
    'EPSG:16032'
    >>> result.target_axes, result.coordinate_order
    (('E', 'N'), 'xy')
"""

from geodetic_engine.geodesy.crs import AxisSpec, CoordinateReferenceSystem
from geodetic_engine.geodesy.errors import (
    AmbiguousOperationError,
    BallparkTransformationError,
    GeodesyError,
    MissingCoordinateEpochError,
    MissingGridError,
    NotCollapsibleError,
    OperationNotAvailableError,
    TransformationFailedError,
    UnresolvableCRSError,
)
from geodetic_engine.geodesy.operation import (
    AppliedOperation,
    AreaOfUse,
    GridUsage,
    OperationCandidate,
    OperationRequest,
    OperationRoute,
)
from geodetic_engine.geodesy.result import Coordinates, TransformationResult
from geodetic_engine.geodesy.transformation import (
    Transformation,
    available_operations,
    transform,
)

__all__ = [
    "AmbiguousOperationError",
    "AppliedOperation",
    "AreaOfUse",
    "AxisSpec",
    "BallparkTransformationError",
    "CoordinateReferenceSystem",
    "Coordinates",
    "GeodesyError",
    "GridUsage",
    "MissingCoordinateEpochError",
    "MissingGridError",
    "NotCollapsibleError",
    "OperationCandidate",
    "OperationNotAvailableError",
    "OperationRequest",
    "OperationRoute",
    "Transformation",
    "TransformationFailedError",
    "TransformationResult",
    "UnresolvableCRSError",
    "available_operations",
    "transform",
]
