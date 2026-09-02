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
    """A dynamic CRS was used without a coordinate epoch.

    Coordinates in a dynamic reference frame are meaningless without the epoch
    they were observed at, since the ground itself moves. Assuming an epoch
    would silently displace every result.
    """


class TransformationFailedError(GeodesyError):
    """PROJ could not produce a finite result for one or more coordinates."""


class NotCollapsibleError(GeodesyError):
    """A concatenated operation cannot be reduced to a single equivalent step.

    Raised when the chain contains a step that is not a plain Helmert, mixes
    domains or conventions that do not compose, or when the composed parameters
    fail to reproduce the original chain within tolerance. Emitting the
    composed operation anyway would ship a transformation that is not the one
    the authority defined.
    """
