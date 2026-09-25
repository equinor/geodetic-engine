"""Exceptions raised while reading or writing an OSDU persistableReference.

A persistableReference arrives from another system, so every failure here is
either input this package cannot read or a definition it will not model. Both
are raised rather than worked around. A reference that cannot be translated
exactly is more useful as an error than as a CRS that is nearly right, because
a datum shift that is nearly right still puts a position tens of metres out.

These share :class:`~geodetic_engine.errors.GeodeticEngineError` with the rest
of the package.
"""

from geodetic_engine.errors import GeodeticEngineError


class PersistableReferenceError(GeodeticEngineError):
    """Base class for every persistableReference failure."""


class MalformedReferenceError(PersistableReferenceError):
    """The payload is not a readable persistableReference.

    Covers a payload that is not JSON, is too large or too deeply nested to be
    a single definition, states no recognisable kind, or whose embedded ESRI
    WKT does not parse.
    """


class UnsupportedReferenceError(PersistableReferenceError):
    """The payload is well formed but states a kind this package does not model."""


class UnsupportedMethodError(PersistableReferenceError):
    """A transformation states a method this package will not translate.

    Raised instead of approximating with a method that takes the same
    parameters. The methods that are supported are listed in
    :mod:`geodetic_engine.persistablereference.methods`.
    """


class UnresolvableGridError(PersistableReferenceError):
    """A grid-based transformation names a dataset that resolves to no PROJ grid.

    The dataset name is ESRI's. It is matched against the grid names PROJ's own
    database knows, and a name that matches none of them, or more than one, is
    refused rather than guessed at.
    """
