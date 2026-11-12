"""Listing coordinate systems."""

from __future__ import annotations

import re
from collections.abc import Iterable, Iterator, Mapping
from dataclasses import dataclass
from enum import StrEnum
from functools import lru_cache
from typing import Any, ClassVar

from pyproj import CRS

from geodetic_engine._referencing.database import DatabaseIdentity
from geodetic_engine.catalog import _filters, _registry
from geodetic_engine.catalog._entry import (
    PROJJSON_SCHEMA,
    ObjectRef,
    RegistryEntry,
    columns,
    order,
    projjson_id,
)
from geodetic_engine.catalog._filters import ReferenceFilter, TextFilter
from geodetic_engine.catalog._listing import Listing
from geodetic_engine.catalog._registry import Key
from geodetic_engine.catalog._units import unit_json
from geodetic_engine.catalog.errors import InvalidFilterError


class CoordinateSystemKind(StrEnum):
    """The kind of a coordinate system: PROJJSON's ``subtype``."""

    CARTESIAN = "Cartesian"
    """Straight, perpendicular axes, such as easting and northing."""

    ELLIPSOIDAL = "ellipsoidal"
    """Latitude and longitude, and ellipsoidal height in 3D."""

    VERTICAL = "vertical"
    """One axis, height or depth."""

    SPHERICAL = "spherical"
    """Angles from the centre of a sphere, as planetocentric CRSs use."""

    ORDINAL = "ordinal"
    """Axes that count, such as the rows and columns of a bin grid."""

    PARAMETRIC = "parametric"
    """One axis of a parameter such as pressure."""

    AFFINE = "affine"
    """Straight axes that need not be perpendicular."""

    TEMPORAL_DATE_TIME = "TemporalDateTime"
    """Time, as dates and times."""

    TEMPORAL_COUNT = "TemporalCount"
    """Time, as a count of units."""

    TEMPORAL_MEASURE = "TemporalMeasure"
    """Time, as a measure."""


# Every axis direction PROJJSON v0.7 allows.
_DIRECTIONS = {
    direction.casefold(): direction
    for direction in (
        *("north", "northNorthEast", "northEast", "eastNorthEast"),
        *("east", "eastSouthEast", "southEast", "southSouthEast"),
        *("south", "southSouthWest", "southWest", "westSouthWest"),
        *("west", "westNorthWest", "northWest", "northNorthWest"),
        *("up", "down", "geocentricX", "geocentricY", "geocentricZ"),
        *("columnPositive", "columnNegative", "rowPositive", "rowNegative"),
        *("displayRight", "displayLeft", "displayUp", "displayDown"),
        *("forward", "aft", "port", "starboard", "clockwise", "counterClockwise"),
        *("towards", "awayFrom", "future", "past", "unspecified"),
    )
}
# "North along 90°E": a polar axis, and the meridian it points along.
_ALONG = re.compile(r"^(north|south) along (\d+(?:\.\d+)?)°([EW])$", re.IGNORECASE)


def direction(orientation: str) -> tuple[str, float | None]:
    """An axis orientation as proj.db states it, as PROJJSON's direction.

    Returns the direction and, for a polar axis, the longitude of the meridian
    it points along, as PROJ reads them. An orientation with no PROJJSON
    direction, such as EPSG's ``"Along receiver lines"``, is ``"unspecified"``.
    """
    if match := _ALONG.match(orientation.strip()):
        longitude = float(match[2]) * (-1 if match[3].upper() == "W" else 1)
        return match[1].lower(), longitude
    compact = re.sub(r"[-\s]+(\w)", lambda m: m[1].upper(), orientation.strip())
    return _DIRECTIONS.get(compact.casefold(), "unspecified"), None


@dataclass(frozen=True, slots=True)
class Axis:
    """One axis of a coordinate system.

    Attributes:
        name: Name, for example ``"Easting"``.
        abbreviation: Abbreviation, for example ``"E"``.
        direction: Positive direction, as PROJJSON names it, for example
            ``"east"``.
        unit: The unit of the axis, or None when the database states none.
        meridian: For an axis pointing along a meridian, as the axes of a
            polar stereographic projection do, that meridian's longitude in
            degrees; None otherwise.
    """

    name: str
    abbreviation: str
    direction: str
    unit: ObjectRef | None
    meridian: float | None = None

    def to_json_dict(self) -> dict[str, Any]:
        """The axis in PROJJSON's names, with the unit as a reference."""
        found: dict[str, Any] = {
            "name": self.name,
            "abbreviation": self.abbreviation,
            "direction": self.direction,
        }
        if self.meridian is not None:
            found["meridian"] = {"longitude": self.meridian}
        if self.unit is not None:
            found["unit"] = self.unit.to_json_dict()
        return found


@dataclass(frozen=True, slots=True, kw_only=True)
class CoordinateSystemEntry(RegistryEntry):
    """A coordinate system the active database holds.

    Listed by :func:`~geodetic_engine.catalog.coordinate_systems`.

    proj.db keeps no name for a coordinate system, so ``name`` is composed,
    in the pattern EPSG names them, from the type, axes and units; nor does it
    record deprecation, so ``deprecated`` is always False.

    Attributes:
        kind: The kind, PROJJSON's ``subtype``.
        dimension: The number of axes.
        axes: The axes, in declared order.
    """

    _COLUMNS: ClassVar[tuple[str, ...]] = ("kind", "dimension", "axis_abbreviations")

    kind: CoordinateSystemKind
    dimension: int
    axes: tuple[Axis, ...]

    @property
    def projjson_type(self) -> str:
        """Always ``"CoordinateSystem"``."""
        return "CoordinateSystem"

    @property
    def axis_abbreviations(self) -> tuple[str, ...]:
        """The axis abbreviations in declared order, for example ``("E", "N")``."""
        return tuple(axis.abbreviation for axis in self.axes)

    def to_json_dict(self) -> dict[str, Any]:
        """The coordinate system as PROJJSON.

        PROJ exports a coordinate system only as part of a CRS. So this is
        PROJ's export of it from a CRS that uses it, and for one no CRS uses
        it is assembled from the database in the same form.

        Example:
            >>> from geodetic_engine import catalog
            >>> cs = catalog.coordinate_systems(code="EPSG:4400").one()
            >>> [axis["abbreviation"] for axis in cs.to_json_dict()["axis"]]
            ['E', 'N']
        """
        user = _users(_registry.identity()).get((self.auth_name, self.code))
        system = None if user is None else CRS.from_authority(*user).coordinate_system
        if system is not None:
            definition: dict[str, Any] = system.to_json_dict()
            return definition
        return self._assembled()

    def _assembled(self) -> dict[str, Any]:
        units = _registry.unit_rows(_registry.identity())
        axes = []
        for axis in self.axes:
            found: dict[str, Any] = {
                "name": axis.name,
                "abbreviation": axis.abbreviation,
                "direction": axis.direction,
            }
            if axis.meridian is not None:
                found["meridian"] = {"longitude": axis.meridian}
            row = (
                None
                if axis.unit is None
                else units.get((axis.unit.auth_name, axis.unit.code))
            )
            if row is not None:
                found["unit"] = unit_json(row, None)
            axes.append(found)
        return {
            "$schema": PROJJSON_SCHEMA,
            "type": "CoordinateSystem",
            "subtype": self.kind.value,
            "axis": axes,
            "id": projjson_id(self.auth_name, self.code),
        }

    def _summary(self) -> dict[str, Any]:
        return {
            "subtype": self.kind.value,
            "dimension": self.dimension,
            "axis": [axis.to_json_dict() for axis in self.axes],
        }


@lru_cache(maxsize=4)
def _users(databases: DatabaseIdentity) -> Mapping[Key, Key]:
    """A CRS that uses each coordinate system, to export it through PROJ."""
    from geodetic_engine.catalog._crs import structures

    found: dict[Key, Key] = {}
    for key, shape in sorted(structures(databases).items(), key=lambda i: order(*i[0])):
        if shape.coordinate_system is not None and shape.table != "compound_crs":
            found.setdefault(shape.coordinate_system, key)
    return found


@lru_cache(maxsize=4)
def entries(databases: DatabaseIdentity) -> tuple[CoordinateSystemEntry, ...]:
    """Every coordinate system in the database, built once per database."""
    units = _registry.unit_rows(databases)
    kinds = {kind.value.casefold(): kind for kind in CoordinateSystemKind}
    built = []
    for (auth, code), row in _registry.coordinate_system_rows(databases).items():
        kind = kinds.get(row.kind.casefold())
        if kind is None:
            continue
        axes = []
        for axis in row.axes:
            heading, meridian = direction(axis.orientation)
            unit = None
            if axis.unit is not None:
                stated = units.get(axis.unit)
                unit = ObjectRef(*axis.unit, stated.name if stated else "")
            axes.append(
                Axis(
                    name=axis.name,
                    abbreviation=axis.abbreviation,
                    direction=heading,
                    unit=unit,
                    meridian=meridian,
                )
            )
        built.append(
            CoordinateSystemEntry(
                auth_name=auth,
                code=code,
                name=_registry.coordinate_system_name(row, units),
                deprecated=False,
                kind=kind,
                dimension=row.dimension,
                axes=tuple(axes),
            )
        )
    return tuple(sorted(built, key=lambda e: order(e.auth_name, e.code)))


def coordinate_systems(
    *,
    authority: str | Iterable[str] | None = None,
    name: TextFilter | None = None,
    code: ReferenceFilter | None = None,
    kind: (
        str | CoordinateSystemKind | Iterable[str | CoordinateSystemKind] | None
    ) = None,
    dimension: int | Iterable[int] | None = None,
    unit: ReferenceFilter | None = None,
    axes: Iterable[str] | None = None,
    limit: int | None = None,
    offset: int = 0,
) -> Listing[CoordinateSystemEntry]:
    """List the coordinate systems the active database holds.

    pyproj has no listing of coordinate systems, so they are read from
    ``proj.db``. Every filter given must hold.

    Args:
        authority: Only these authorities, for example ``"EPSG"``.
        name: Text the composed name contains, without case, or a pattern;
            for example ``"easting"``.
        code: Only these codes, as for :func:`~geodetic_engine.catalog.crs`.
        kind: Only these kinds, for example ``"Cartesian"``.
        dimension: Only this number of axes, or one of several.
        unit: Only coordinate systems with an axis in one of these units, by
            code, entry or name.
        axes: Only these axis abbreviations, in this order and without case,
            for example ``("E", "N")``.
        limit: At most this many entries.
        offset: Skip this many entries first.

    Returns:
        The coordinate systems, ordered by authority and code.

    Raises:
        UnknownAuthorityError: If ``authority`` names an authority the
            database does not hold.
        InvalidFilterError: If a filter is malformed, or ``unit`` names no
            unit.

    Example:
        >>> from geodetic_engine import catalog
        >>> found = catalog.coordinate_systems(
        ...     authority="EPSG", axes=("E", "N"), unit="metre"
        ... )
        >>> "EPSG:4400" in [entry.authority_code for entry in found]
        True
    """
    code, kind, unit = _filters.given(code), _filters.given(kind), _filters.given(unit)
    dimension, axes = _filters.given(dimension), _filters.given(axes)
    databases = _registry.identity()
    authorities = _filters.authorities(authority, databases)
    names = _filters.text(name, "name")
    codes = _filters.codes(code, databases, authorities)
    kinds = _filters.choices(kind, CoordinateSystemKind, "coordinate system kind")
    units = _filters.references(
        unit,
        "unit",
        _filters.registered(frozenset({"unit_of_measure"}), databases),
        databases,
    )
    if dimension is None:
        dimensions = None
    else:
        dimensions = frozenset([dimension] if isinstance(dimension, int) else dimension)
        if not dimensions or not all(
            isinstance(d, int) and not isinstance(d, bool) and d > 0 for d in dimensions
        ):
            raise InvalidFilterError(
                f"dimension must be a whole number, not {dimension!r}"
            )
    if axes is None:
        abbreviations = None
    else:
        if isinstance(axes, str) or not all(isinstance(a, str) for a in axes):
            raise InvalidFilterError(f"axes must be abbreviations, not {axes!r}")
        abbreviations = tuple(a.strip().casefold() for a in axes)
    limit, offset = _filters.paging(limit, offset)
    wanted = None if authorities is None else frozenset(authorities)

    def admits(entry: CoordinateSystemEntry) -> bool:
        if wanted is not None and entry.auth_name not in wanted:
            return False
        if codes is not None and not codes.admits(entry.auth_name, entry.code):
            return False
        if kinds is not None and entry.kind not in kinds:
            return False
        if dimensions is not None and entry.dimension not in dimensions:
            return False
        if abbreviations is not None and abbreviations != tuple(
            a.casefold() for a in entry.axis_abbreviations
        ):
            return False
        if units is not None and not any(
            axis.unit is not None
            and _filters.folded(axis.unit.auth_name, axis.unit.code) in units
            for axis in entry.axes
        ):
            return False
        return names is None or names((entry.name,))

    def produce() -> Iterator[CoordinateSystemEntry]:
        yield from filter(admits, entries(databases))

    filters = _filters.echo(
        authority=authorities,
        name=name,
        code=code,
        kind=kind,
        dimension=dimension,
        unit=unit,
        axes=axes,
        limit=limit,
        offset=offset or None,
    )
    return Listing(
        "coordinate_systems",
        filters,
        produce,
        columns=columns(CoordinateSystemEntry),
        databases=databases,
        limit=limit,
        offset=offset,
    )
