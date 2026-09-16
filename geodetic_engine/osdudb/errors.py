"""Exceptions raised while building a proj.db from an OSDU catalogue.

Failures that are properties of the database being built rather than of the
catalogue - a schema drift, an authority collision, a missing referenced object
- are raised as the :mod:`geodetic_engine.projdb.errors` types, because they
mean the same thing whatever the source. Only the catalogue's own failure modes
are added here.
"""

from geodetic_engine.projdb.errors import (
    ConfigurationError,
    ForeignAuthorityCollision,
    MissingReferencedObjectError,
    ProjDbBuildError,
    SchemaDriftError,
    UnsupportedMethodError,
)


class OsduCatalogError(ProjDbBuildError):
    """The OSDU catalogue file cannot be read or is not a manifest."""


class UnreadableDefinitionError(ProjDbBuildError):
    """An OSDU record carries no WKT, or WKT that PROJ will not parse.

    OSDU states a CRS's structure only in its ``OGCWellKnownText2``, so a record
    without readable WKT is not a definition and cannot be imported.
    """


__all__ = [
    "ConfigurationError",
    "ForeignAuthorityCollision",
    "MissingReferencedObjectError",
    "OsduCatalogError",
    "ProjDbBuildError",
    "SchemaDriftError",
    "UnreadableDefinitionError",
    "UnsupportedMethodError",
]
