"""Exceptions raised while building a custom PROJ database.

Every failure mode that can produce a wrong coordinate downstream gets its own
type, so a caller can tell "this grid is missing" apart from "this operation is
ballpark only" apart from "PROJ changed its schema under us".

Failures that originate in the Georepository API are raised as
:class:`~geodetic_engine.georepository.errors.GeorepositoryError` instead. Both
hierarchies share :class:`~geodetic_engine.errors.GeodeticEngineError`.
"""

from geodetic_engine.errors import GeodeticEngineError


class ProjDbBuildError(GeodeticEngineError):
    """Base class for all custom proj.db build failures."""


class ConfigurationError(ProjDbBuildError):
    """The build configuration is missing or inconsistent."""


class SchemaDriftError(ProjDbBuildError):
    """The base proj.db does not have the columns this builder expects."""


class ForeignAuthorityCollision(ProjDbBuildError):
    """A row would overwrite an object belonging to another authority."""


class MissingReferencedObjectError(ProjDbBuildError):
    """A custom object references an object that is not in the database."""


class MissingGridError(ProjDbBuildError):
    """A grid-based transformation references a grid file that is not available."""


class BallparkOnlyOperationError(ProjDbBuildError):
    """A non-deprecated object only resolves through a ballpark transformation."""


class UnsupportedMethodError(ProjDbBuildError):
    """A coordinate operation method is not evaluable by this PROJ build."""
