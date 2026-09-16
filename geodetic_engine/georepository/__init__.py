"""Client for the Georepository geodetic registry API.

Implemented from the Georepository OpenAPI document so that it works against
any instance, not one particular deployment.

Example:
    >>> from geodetic_engine.georepository import (  # doctest: +SKIP
    ...     GeorepositoryClient,
    ...     GeorepositoryConfig,
    ... )
    >>> config = GeorepositoryConfig(  # doctest: +SKIP
    ...     api_url="https://georepository.example.com",
    ...     client_id="...",
    ...     client_secret="...",
    ... )
    >>> with GeorepositoryClient(config) as client:  # doctest: +SKIP
    ...     for datum in client.iter_collection("Datum", authorities={"Example"}):
    ...         print(datum["Code"], datum["Name"])
"""

from geodetic_engine.georepository.auth import GeorepositoryCredential
from geodetic_engine.georepository.client import GeorepositoryClient
from geodetic_engine.georepository.config import (
    DEFAULT_PAGE_SIZE,
    DEFAULT_SCOPE,
    GeorepositoryConfig,
)
from geodetic_engine.georepository.errors import (
    GeorepositoryApiError,
    GeorepositoryAuthError,
    GeorepositoryConfigError,
    GeorepositoryError,
    PaginationTruncatedError,
)

__all__ = [
    "DEFAULT_PAGE_SIZE",
    "DEFAULT_SCOPE",
    "GeorepositoryApiError",
    "GeorepositoryAuthError",
    "GeorepositoryClient",
    "GeorepositoryConfig",
    "GeorepositoryConfigError",
    "GeorepositoryCredential",
    "GeorepositoryError",
    "PaginationTruncatedError",
]
