"""Build a custom PROJ database from an OSDU coordinate reference catalogue.

The catalogue is a single OSDU manifest file holding
``reference-data--CoordinateReferenceSystem`` and
``reference-data--CoordinateTransformation`` records, so a build needs no
credentials and no network. :func:`download_official_catalog` fetches the one
OSDU publishes.

The database this produces is the same artefact the Georepository workflow in
:mod:`geodetic_engine.projdb` produces, written by the same writer against the
same frozen schema and checked by the same validator; only the source of the
definitions differs.

The public surface is deliberately small: a configuration object, a build
function, a validation function, and the errors they raise.

A release wheel also ships the official catalogue already built into PROJ's
database, and PROJ reads it unless another database was chosen.
:func:`active_database` reports which database PROJ reads and why, and
:func:`use_bundled_database` switches the shipped one on or off.
"""

from geodetic_engine._bundled_database import (
    ActiveDatabase,
    active_database,
    use_bundled_database,
)
from geodetic_engine.osdudb.build import BuildReport, build
from geodetic_engine.osdudb.catalog import OsduCatalog
from geodetic_engine.osdudb.config import OsduBuildConfig, load_config
from geodetic_engine.osdudb.download import CatalogDownload, download_official_catalog
from geodetic_engine.osdudb.errors import (
    CatalogDownloadError,
    ConfigurationError,
    ForeignAuthorityCollision,
    MissingReferencedObjectError,
    OsduCatalogError,
    ProjDbBuildError,
    SchemaDriftError,
    UnreadableDefinitionError,
    UnsupportedMethodError,
)
from geodetic_engine.projdb.settings import AuthorityPreference
from geodetic_engine.projdb.validate import validate

__all__ = [
    "ActiveDatabase",
    "AuthorityPreference",
    "BuildReport",
    "CatalogDownload",
    "CatalogDownloadError",
    "ConfigurationError",
    "ForeignAuthorityCollision",
    "MissingReferencedObjectError",
    "OsduBuildConfig",
    "OsduCatalog",
    "OsduCatalogError",
    "ProjDbBuildError",
    "SchemaDriftError",
    "UnreadableDefinitionError",
    "UnsupportedMethodError",
    "active_database",
    "build",
    "download_official_catalog",
    "load_config",
    "use_bundled_database",
    "validate",
]
