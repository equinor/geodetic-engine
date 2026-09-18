"""Reading and writing OSDU persistableReference definitions.

OSDU identifies the CRS, transformation or unit of a record with a
``persistableReference``: a JSON envelope wrapping ESRI WKT, frequently
URL-encoded and frequently embedded as a string inside another document. It is
self-contained, carrying the full definition rather than a code to look up, and
this package reads it that way -- the parameters are what is built from, and the
authority code beside them is provenance.

Public entry points:

* :func:`parse_persistable_reference` -- read a payload into the reference it
  states.
* :class:`CrsReference` -- a CRS, on its own or bound to a hub, with
  ``to_crs()``.
* :class:`OperationReference` -- a transformation, single or concatenated, with
  ``to_operation()``.
* :class:`UnitReference` -- a unit, with ``to_si()`` and ``from_si()``.
* :func:`looks_like_reference` -- whether a string is shaped like one, cheap
  enough to run on any CRS definition a caller passes in.

These hand back pyproj objects.
:meth:`~geodetic_engine.geodesy.crs.CoordinateReferenceSystem.from_persistable_reference`
is the same thing wrapped in this package's own CRS type, and
:func:`~geodetic_engine.geodesy.transformation.transform` accepts a payload
directly wherever it accepts a CRS.

A definition this package cannot translate exactly is refused. ESRI states
several methods that differ from a supported one only by a convention or a
term, so translating a near miss would give coordinates that look right and are
metres out.

Example:
    >>> from geodetic_engine.persistablereference import parse_persistable_reference
    >>> reference = parse_persistable_reference(payload)  # doctest: +SKIP
    >>> reference.authority_code  # doctest: +SKIP
    AuthorityCode(authority='OSDU', code='23032023')
    >>> reference.to_crs().is_bound  # doctest: +SKIP
    True
"""

from geodetic_engine.persistablereference.emit import (
    geogtran,
    to_persistable_reference,
)
from geodetic_engine.persistablereference.envelope import (
    AuthorityCode,
    Kind,
    looks_like_reference,
)
from geodetic_engine.persistablereference.errors import (
    MalformedReferenceError,
    PersistableReferenceError,
    UnresolvableGridError,
    UnsupportedMethodError,
    UnsupportedReferenceError,
)
from geodetic_engine.persistablereference.reference import (
    AnyReference,
    CrsReference,
    OperationReference,
    Reference,
    UnitReference,
    operation_from_geogtran,
    parse_persistable_reference,
)

__all__ = [
    "AnyReference",
    "AuthorityCode",
    "CrsReference",
    "Kind",
    "MalformedReferenceError",
    "OperationReference",
    "PersistableReferenceError",
    "Reference",
    "UnitReference",
    "UnresolvableGridError",
    "UnsupportedMethodError",
    "UnsupportedReferenceError",
    "geogtran",
    "looks_like_reference",
    "operation_from_geogtran",
    "parse_persistable_reference",
    "to_persistable_reference",
]
