"""Build a custom PROJ database from a Georepository instance.

The public surface is deliberately small: a configuration object, a build
function, a validation function, and the errors they raise.
"""

from geodetic_engine.projdb.build import BuildReport, build
from geodetic_engine.projdb.config import (
    AuthorityPreference,
    ProjDbBuildConfig,
    load_config,
)
from geodetic_engine.projdb.errors import (
    BallparkOnlyOperationError,
    ConfigurationError,
    ForeignAuthorityCollision,
    MissingGridError,
    MissingReferencedObjectError,
    ProjDbBuildError,
    SchemaDriftError,
    UnsupportedMethodError,
)
from geodetic_engine.projdb.validate import validate

__all__ = [
    "AuthorityPreference",
    "BallparkOnlyOperationError",
    "BuildReport",
    "ConfigurationError",
    "ForeignAuthorityCollision",
    "MissingGridError",
    "MissingReferencedObjectError",
    "ProjDbBuildConfig",
    "ProjDbBuildError",
    "SchemaDriftError",
    "UnsupportedMethodError",
    "build",
    "load_config",
    "validate",
]
