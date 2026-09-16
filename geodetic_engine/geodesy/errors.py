"""Exceptions raised while resolving or applying a coordinate transformation.

Every way a transformation can fail to be trustworthy gets its own type, so a
caller can tell "the grid is not installed" apart from "PROJ would only give me
a ballpark answer" apart from "the operation you asked for is not the one that
would be applied". None of these is recoverable by substituting a different
answer, so none of them is signalled by a return value.

Failures raised while building a custom proj.db are
:class:`~geodetic_engine.projdb.errors.ProjDbBuildError` instead. Both
hierarchies share :class:`~geodetic_engine.errors.GeodeticEngineError`.
"""

from geodetic_engine.errors import GeodeticEngineError


class GeodesyError(GeodeticEngineError):
    """Base class for all coordinate transformation failures."""


class UnresolvableCRSError(GeodesyError):
    """A CRS could not be constructed from the given input."""


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
    """PROJ could not produce a finite result for one or more coordinates."""


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


class UnembeddableOperationError(GeodesyError):
    """An operation cannot be stated in the form a bound CRS requires.

    A bound CRS carries its transformation as a single ``ABRIDGEDTRANSFORMATION``
    with no units, so an operation has to be both one step and expressed in the
    units that form assumes before it can be embedded.
    """


class NotCollapsibleError(UnembeddableOperationError):
    """A concatenated operation cannot be reduced to a single equivalent step.

    Raised when the chain contains a step that is not a Helmert, mixes
    domains or conventions that do not compose, or when the composed parameters
    fail to reproduce the original chain within tolerance. Emitting the
    composed operation anyway would ship a transformation that is not the one
    the authority defined.

    A subclass of :class:`UnembeddableOperationError`, so code that catches that
    catches this too.
    """
