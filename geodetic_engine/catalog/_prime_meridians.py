"""Listing prime meridians."""

from __future__ import annotations

import math
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from functools import lru_cache
from typing import Any, ClassVar

from pyproj.crs import PrimeMeridian

from geodetic_engine._referencing.database import DatabaseIdentity
from geodetic_engine.catalog import _filters, _registry
from geodetic_engine.catalog._entry import RegistryEntry, columns, order
from geodetic_engine.catalog._filters import RangeFilter, ReferenceFilter, TextFilter
from geodetic_engine.catalog._listing import Listing


@dataclass(frozen=True, slots=True, kw_only=True)
class PrimeMeridianEntry(RegistryEntry):
    """A prime meridian the active database holds.

    Listed by :func:`~geodetic_engine.catalog.prime_meridians`, with its
    longitude as PROJ reads it: EPSG states several in sexagesimal degrees,
    which PROJ reads as decimal degrees, and Paris in grads.

    Attributes:
        longitude: Longitude from Greenwich, in :attr:`unit`.
        unit: Name of the angular unit of :attr:`longitude`, for example
            ``"degree"`` or ``"grad"``.
        longitude_degrees: The same longitude, in decimal degrees.
    """

    _COLUMNS: ClassVar[tuple[str, ...]] = ("longitude", "unit", "longitude_degrees")

    longitude: float
    unit: str
    longitude_degrees: float

    @property
    def projjson_type(self) -> str:
        """Always ``"PrimeMeridian"``."""
        return "PrimeMeridian"

    def to_json_dict(self) -> dict[str, Any]:
        """The prime meridian's PROJJSON, exactly as PROJ exports it.

        Example:
            >>> from geodetic_engine import catalog
            >>> paris = catalog.prime_meridians(authority="EPSG", name="Paris")
            >>> paris.first().to_json_dict()["longitude"]["unit"]["name"]
            'grad'
        """
        definition: dict[str, Any] = PrimeMeridian.from_authority(
            self.auth_name, self.code
        ).to_json_dict()
        return definition

    def _summary(self) -> dict[str, Any]:
        return {"longitude": self.longitude_degrees}


@lru_cache(maxsize=4)
def entries(databases: DatabaseIdentity) -> tuple[PrimeMeridianEntry, ...]:
    """Every prime meridian in the database, read through PROJ once."""
    aliases = _registry.aliases(databases)
    replacements = _registry.replacements(databases)
    with _registry.reading(databases) as connection:
        rows = connection.execute(
            "SELECT auth_name, code, name, deprecated FROM prime_meridian"
        ).fetchall()
    built = []
    for auth, code, name, deprecated in rows:
        meridian = PrimeMeridian.from_authority(str(auth), str(code))
        key = ("prime_meridian", str(auth), str(code))
        built.append(
            PrimeMeridianEntry(
                auth_name=str(auth),
                code=str(code),
                name=str(name),
                deprecated=bool(deprecated),
                aliases=aliases.get(key, ()),
                replaced_by=replacements.get(key, ()),
                longitude=float(meridian.longitude),
                unit=meridian.unit_name,
                longitude_degrees=math.degrees(
                    float(meridian.longitude) * float(meridian.unit_conversion_factor)
                ),
            )
        )
    return tuple(sorted(built, key=lambda e: order(e.auth_name, e.code)))


def prime_meridians(
    *,
    authority: str | Iterable[str] | None = None,
    name: TextFilter | None = None,
    code: ReferenceFilter | None = None,
    longitude: RangeFilter | None = None,
    unit: str | Iterable[str] | None = None,
    deprecated: bool | None = False,
    limit: int | None = None,
    offset: int = 0,
) -> Listing[PrimeMeridianEntry]:
    """List the prime meridians the active database holds.

    Every filter given must hold. The datums using one are
    ``catalog.datums(prime_meridian=entry)``.

    Args:
        authority: Only these authorities, for example ``"EPSG"``.
        name: Text the name or an alias contains, without case, or a pattern.
        code: Only these codes, as for :func:`~geodetic_engine.catalog.crs`.
        longitude: In decimal degrees, exactly or as ``(low, high)`` with None
            for an open end.
        unit: Only meridians whose longitude is stated in these units, by
            name without case, for example ``"grad"``.
        deprecated: False (default) leaves deprecated meridians out, True
            lists only them, None lists both.
        limit: At most this many entries.
        offset: Skip this many entries first.

    Returns:
        The prime meridians, ordered by authority and code.

    Raises:
        UnknownAuthorityError: If ``authority`` names an authority the
            database does not hold.
        InvalidFilterError: If a filter is malformed.

    Example:
        >>> from geodetic_engine import catalog
        >>> oslo = catalog.prime_meridians(authority="EPSG", name="Oslo").one()
        >>> round(oslo.longitude_degrees, 6)
        10.722917
    """
    code, unit = _filters.given(code), _filters.given(unit)
    databases = _registry.identity()
    authorities = _filters.authorities(authority, databases)
    names = _filters.text(name, "name")
    codes = _filters.codes(code, databases, authorities)
    longitudes = _filters.value_range(longitude, "longitude")
    units = _filters.labels(unit, "unit")
    deprecated_ = _filters.flag(deprecated, "deprecated")
    limit, offset = _filters.paging(limit, offset)
    wanted = None if authorities is None else frozenset(authorities)

    def admits(entry: PrimeMeridianEntry) -> bool:
        if wanted is not None and entry.auth_name not in wanted:
            return False
        if not _filters.admits_flag(deprecated_, entry.deprecated):
            return False
        if codes is not None and not codes.admits(entry.auth_name, entry.code):
            return False
        if not _filters.in_range(longitudes, entry.longitude_degrees):
            return False
        if units is not None and entry.unit.casefold() not in units:
            return False
        return names is None or names((entry.name, *entry.aliases))

    def produce() -> Iterator[PrimeMeridianEntry]:
        yield from filter(admits, entries(databases))

    filters = _filters.echo(
        authority=authorities,
        name=name,
        code=code,
        longitude=longitude,
        unit=unit,
        deprecated=deprecated_,
        limit=limit,
        offset=offset or None,
    )
    return Listing(
        "prime_meridians",
        filters,
        produce,
        columns=columns(PrimeMeridianEntry),
        databases=databases,
        limit=limit,
        offset=offset,
    )
