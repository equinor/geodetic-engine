"""The exceptions the lower layer raises, re-exported by :mod:`geodetic_engine.geodesy`.

Defined here, not in ``geodesy``, because the Helmert algebra that raises them
sits below ``persistablereference``, which must not import ``geodesy``.
"""

from geodetic_engine.errors import GeodeticEngineError


class GeodesyError(GeodeticEngineError):
    """Base class for all coordinate transformation failures."""


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
