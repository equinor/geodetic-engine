"""Exceptions raised while resolving or applying a coordinate transformation.

Every way a transformation can fail to be trustworthy gets its own type, so a
caller can tell "the grid is not installed" apart from "PROJ would only give me
a ballpark answer" apart from "the operation you asked for is not the one that
would be applied". None of these is recoverable by substituting a different
answer, so none of them is signalled by a return value.

Failures raised while building a custom proj.db are
:class:`~geodetic_engine.projdb.errors.ProjDbBuildError` instead. Both
hierarchies share :class:`~geodetic_engine.errors.GeodeticEngineError`.

:class:`GeodesyError`, :class:`UnembeddableOperationError` and
:class:`NotCollapsibleError` are defined in the private lower layer, which
raises them too, and are the same classes here.
"""

from geodetic_engine._referencing.errors import GeodesyError as GeodesyError
from geodetic_engine._referencing.errors import (
    NotCollapsibleError as NotCollapsibleError,
)
from geodetic_engine._referencing.errors import (
    UnembeddableOperationError as UnembeddableOperationError,
)


class UnresolvableCRSError(GeodesyError):
    """A CRS could not be constructed from the given input."""


class UnsupportedCRSError(GeodesyError):
    """A CRS was resolved, but the requested computation does not apply to it.

    For example projection factors asked for in a geocentric, engineering or
    vertical CRS, which have no map projection to take them from.
    """


class BallparkTransformationError(GeodesyError):
    """The only path between the two CRSs is a ballpark approximation.

    Raised rather than returning the approximate coordinates. A ballpark result
    carries no usable accuracy statement, so a caller cannot tell how wrong it
    is, which is worse than having no answer at all.
    """


class OperationNotAvailableError(GeodesyError):
    """The requested EPSG coordinate operation cannot be applied to this pair.

    Raised instead of falling back to whichever operation PROJ would have
    chosen. Silently substituting an operation is how a caller ends up with
    coordinates that look right and are metres out.
    """


class AmbiguousOperationError(GeodesyError):
    """No operation was requested and more than one defensible choice exists.

    Raised when the transformation involves a datum change, where the choice of
    operation is a decision about accuracy and area of validity that this
    library will not make on the caller's behalf.
    """


class MissingGridError(GeodesyError):
    """The transformation needs a grid file that is not installed."""


class MissingCoordinateEpochError(GeodesyError):
    """A time-dependent transformation was asked for without a coordinate epoch.

    Raised when the operation actually reads the epoch: a deformation model, a
    point motion, or a Helmert with rates of change. Omitting it there would
    silently displace every result.

    A dynamic reference frame alone is not enough. EPSG declares WGS 72 dynamic,
    but the Helmert from it to WGS 84 gives the same coordinates at every epoch,
    and demanding one would block valid work without preventing any error.
    """


class TransformationFailedError(GeodesyError):
    """PROJ could not produce a finite result for one or more coordinates.

    Attributes:
        point: The index of the point that failed, counted from 0 in the batch
            or stream, or None when the failure is not one point's.
        detail: What is wrong with that point: the message without its
            ``point N`` prefix. The whole message when :attr:`point` is None.
    """

    def __init__(self, message: str, *, point: int | None = None) -> None:
        super().__init__(message if point is None else f"point {point} {message}")
        self.point = point
        self.detail = message


class CoordinateOutOfRangeError(TransformationFailedError):
    """An input coordinate is outside the range its axis can represent.

    Raised before PROJ is called, because PROJ's own message for this names
    neither the CRS, its units, nor the value order the number was read in --
    and the overwhelmingly common cause is passing projected coordinates in
    metres to a geographic CRS in degrees, or passing latitude first when this
    package reads values in ``xy`` order.

    A subclass of :class:`TransformationFailedError`, so code that already
    catches that keeps working.
    """
