"""Exceptions raised while talking to a Georepository instance."""

from geodetic_engine.errors import GeodeticEngineError


class GeorepositoryError(GeodeticEngineError):
    """Base class for Georepository access failures."""


class GeorepositoryConfigError(GeorepositoryError):
    """The Georepository connection settings are missing or inconsistent."""


class GeorepositoryAuthError(GeorepositoryError):
    """An OAuth2 token could not be obtained or refreshed."""


class GeorepositoryApiError(GeorepositoryError):
    """The API returned an unusable response."""


class PaginationTruncatedError(GeorepositoryApiError):
    """Paging did not return every advertised result.

    Raised when the server reports more results than were collected, or ignores
    the page parameter. Silently importing a truncated set of authority objects
    would leave the caller with a quietly incomplete view of the register.
    """
