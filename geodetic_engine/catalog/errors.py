"""Exceptions raised by :mod:`geodetic_engine.catalog`."""

from __future__ import annotations

from geodetic_engine.errors import GeodeticEngineError


class CatalogError(GeodeticEngineError):
    """Root of every error a catalog listing raises.

    Also raised on its own when there is no ``proj.db`` on PROJ's search path
    to list from.
    """


class UnknownAuthorityError(CatalogError, ValueError):
    """An ``authority`` filter names no authority the active database holds.

    The message lists the authorities it does hold, since a misspelt
    authority would otherwise read as an empty result.
    """


class InvalidFilterError(CatalogError, ValueError):
    """A filter value is malformed, or names an object the database lacks.

    Raised when the listing function is called, before anything is listed:
    an area of interest outside the valid range, an unknown kind, a range
    whose bounds are the wrong way round, or a reference such as
    ``datum="EPSG:4326"`` that names no datum.
    """


class NotFoundError(CatalogError, LookupError):
    """:meth:`~geodetic_engine.catalog.Listing.one` found no entry."""


class NotUniqueError(CatalogError, LookupError):
    """:meth:`~geodetic_engine.catalog.Listing.one` found more than one entry."""


class GridIndexUnavailableError(CatalogError):
    """The list of grids on PROJ's content delivery network could not be read.

    Raised by :func:`~geodetic_engine.catalog.grids` with
    ``check_remote=True`` only, which is the one listing that uses the
    network.
    """
