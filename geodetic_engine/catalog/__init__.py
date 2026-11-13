"""Discover what the active PROJ database holds: CRSs, operations, datums and more.

One function per kind of object, each taking the same shared filters
(``authority``, ``name``, ``code``, ``deprecated``, ``limit``, ``offset``)
and its own, and returning a lazy :class:`Listing` of entries:

* :func:`crs` -- coordinate reference systems.
* :func:`operations` -- conversions, transformations and concatenated
  operations, as the registry publishes them.
* :func:`datums` -- datums and datum ensembles.
* :func:`ellipsoids`, :func:`prime_meridians`, :func:`units`,
  :func:`methods`, :func:`coordinate_systems` -- the building blocks.
* :func:`grids` -- the grid files operations read, and whether each is
  installed.
* :func:`authorities` and :func:`database` -- what answered, for provenance.

Everything is read from the ``proj.db`` PROJ resolves codes against, through
pyproj wherever pyproj can answer. An entry's ``to_json_dict()`` is the
object's official PROJJSON, as PROJ exports it; a listing's
:meth:`~Listing.to_json_dict` wraps short PROJJSON-shaped items, with what
PROJJSON has no place for, such as deprecation, in an envelope described by
:func:`listing_schema`.

This module lists what the registry holds. To see which operations PROJ
would apply between two CRSs, including chains it assembles itself, use
:func:`geodetic_engine.geodesy.available_operations`.

Example:
    >>> from geodetic_engine import catalog
    >>> utm = catalog.crs(
    ...     authority="EPSG", kind="projected", datum="ETRS89",
    ...     area_of_interest=(10.0, 59.0, 11.0, 60.0), name="UTM",
    ... )
    >>> [entry.authority_code for entry in utm]
    ['EPSG:3044', 'EPSG:25832']
"""

from __future__ import annotations

import json
from importlib.resources import files
from typing import Any

from geodetic_engine.catalog._coordinate_systems import (
    Axis,
    CoordinateSystemEntry,
    CoordinateSystemKind,
    coordinate_systems,
)
from geodetic_engine.catalog._crs import CrsEntry, CrsKind, crs
from geodetic_engine.catalog._database import (
    Authority,
    DatabaseInfo,
    authorities,
    database,
)
from geodetic_engine.catalog._datums import DatumEntry, DatumKind, datums
from geodetic_engine.catalog._ellipsoids import EllipsoidEntry, ellipsoids
from geodetic_engine.catalog._entry import (
    CatalogEntry,
    LocatedEntry,
    ObjectRef,
    RegistryEntry,
)
from geodetic_engine.catalog._filters import (
    AreaFilter,
    RangeFilter,
    ReferenceFilter,
    TextFilter,
)
from geodetic_engine.catalog._grids import GridEntry, GridKind, grids
from geodetic_engine.catalog._listing import LISTING_SCHEMA_URL, Listing
from geodetic_engine.catalog._methods import MethodEntry, MethodKind, methods
from geodetic_engine.catalog._operations import (
    OperationEntry,
    OperationKind,
    operations,
)
from geodetic_engine.catalog._prime_meridians import (
    PrimeMeridianEntry,
    prime_meridians,
)
from geodetic_engine.catalog._units import UnitEntry, UnitKind, units
from geodetic_engine.catalog.errors import (
    CatalogError,
    GridIndexUnavailableError,
    InvalidFilterError,
    NotFoundError,
    NotUniqueError,
    UnknownAuthorityError,
)


def listing_schema() -> dict[str, Any]:
    """The JSON Schema of :meth:`Listing.to_json_dict`.

    Draft-07, like PROJJSON's own schema, which it refers to for ``id``,
    ``bbox`` and every ``definition``. Published at
    :data:`LISTING_SCHEMA_URL`; this is the copy shipped in the package, for
    validating offline.

    Example:
        >>> from geodetic_engine import catalog
        >>> catalog.listing_schema()["$id"] == catalog.LISTING_SCHEMA_URL
        True
    """
    text = files(__name__).joinpath("listing.schema.json").read_text(encoding="utf-8")
    schema: dict[str, Any] = json.loads(text)
    return schema


__all__ = [
    "LISTING_SCHEMA_URL",
    "AreaFilter",
    "Authority",
    "Axis",
    "CatalogEntry",
    "CatalogError",
    "CoordinateSystemEntry",
    "CoordinateSystemKind",
    "CrsEntry",
    "CrsKind",
    "DatabaseInfo",
    "DatumEntry",
    "DatumKind",
    "EllipsoidEntry",
    "GridEntry",
    "GridIndexUnavailableError",
    "GridKind",
    "InvalidFilterError",
    "Listing",
    "LocatedEntry",
    "MethodEntry",
    "MethodKind",
    "NotFoundError",
    "NotUniqueError",
    "ObjectRef",
    "OperationEntry",
    "OperationKind",
    "PrimeMeridianEntry",
    "RangeFilter",
    "ReferenceFilter",
    "RegistryEntry",
    "TextFilter",
    "UnitEntry",
    "UnitKind",
    "UnknownAuthorityError",
    "authorities",
    "coordinate_systems",
    "crs",
    "database",
    "datums",
    "ellipsoids",
    "grids",
    "listing_schema",
    "methods",
    "operations",
    "prime_meridians",
    "units",
]
