"""Coordinate reference systems, and the axis and unit contract they declare.

Two different orderings are in play throughout this package, and conflating
them is the single easiest way to ship wrong coordinates:

* The **declared axis order** is what the EPSG dataset says the CRS's axes are,
  in the order EPSG defines them. ``EPSG:4326`` declares ``(Lat, Lon)``. This
  module reports that order faithfully and never reinterprets it.
* The **coordinate value order** is the order this package accepts and returns
  numbers in, which is always ``xy`` (longitude then latitude, easting then
  northing). See :mod:`geodetic_engine.geodesy.transformation`.

So for ``EPSG:4326``, :attr:`CoordinateReferenceSystem.axis_abbreviations` is
``("Lat", "Lon")`` while the coordinate values are ordered ``(lon, lat)``. That
is deliberate: the metadata describes the CRS, not this package's calling
convention.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from functools import lru_cache
from typing import Any

from pyproj import CRS
from pyproj.exceptions import CRSError

from geodetic_engine.geodesy.database import bound_definition
from geodetic_engine.geodesy.errors import UnresolvableCRSError

logger = logging.getLogger(__name__)

# A dynamic reference frame is one whose coordinates move with time, and PROJ
# records that by giving the datum a reference epoch. An ensemble or a static
# datum has no such key.
_DYNAMIC_MARKER = "frame_reference_epoch"
_EASTINGS = frozenset({"east", "west"})
_NORTHINGS = frozenset({"north", "south"})
_VERTICALS = frozenset({"up", "down"})


@dataclass(frozen=True, slots=True)
class AxisSpec:
    """One axis of a CRS, exactly as the EPSG dataset declares it.

    Attributes:
        name: Full axis name, for example ``"Geodetic latitude"``.
        abbrev: Axis abbreviation, for example ``"Lat"``.
        direction: Positive direction, for example ``"north"``.
        unit_name: Name of the axis unit, for example ``"degree"``.
        unit_code: Authority code of the unit, for example ``"9122"``.
        unit_conversion_factor: Multiplier from this unit to the SI unit of the
            same quantity (radians for angles, metres for lengths).
    """

    name: str
    abbrev: str
    direction: str
    unit_name: str
    unit_code: str
    unit_conversion_factor: float


class CoordinateReferenceSystem:
    """A resolved CRS together with the axis roles and units it declares.

    Wraps :class:`pyproj.CRS` so that a caller never has to read PROJ's source
    to find out how many axes a CRS has, what they mean, or what units they are
    in. Construction is cached, since resolving a CRS is expensive relative to
    transforming a point.

    Example:
        >>> crs = CoordinateReferenceSystem.from_user_input("EPSG:4326")
        >>> crs.axis_abbreviations
        ('Lat', 'Lon')
        >>> crs.axis_units
        ('degree', 'degree')
        >>> crs.dimension
        2

        The declared order above is EPSG's. Coordinate *values* for this CRS
        are still ordered ``(lon, lat)`` everywhere in this package.
    """

    __slots__ = ("_axes", "_crs", "_definition")

    def __init__(self, crs: CRS, definition: str) -> None:
        """Wrap an already-resolved :class:`pyproj.CRS`.

        Prefer :meth:`from_user_input`, which caches. This constructor exists
        for the case where a :class:`pyproj.CRS` is already in hand.

        Args:
            crs: The resolved CRS.
            definition: The input it was resolved from, kept for error messages
                and for :func:`repr`.
        """
        self._crs = crs
        self._definition = definition
        self._axes = tuple(
            AxisSpec(
                name=axis.name,
                abbrev=axis.abbrev,
                direction=axis.direction,
                unit_name=axis.unit_name,
                unit_code=axis.unit_code,
                unit_conversion_factor=axis.unit_conversion_factor,
            )
            for axis in crs.axis_info
        )

    @classmethod
    def from_user_input(cls, value: Any) -> CoordinateReferenceSystem:
        """Resolve a CRS from an EPSG code, WKT, PROJ string or CRS object.

        Args:
            value: An authority code such as ``"EPSG:4326"`` or ``4326``, a WKT
                string, a PROJ string, a PROJJSON string, or an existing
                :class:`pyproj.CRS` or :class:`CoordinateReferenceSystem`.

        Returns:
            The resolved CRS.

        Raises:
            UnresolvableCRSError: If PROJ cannot construct a CRS from the input.

        Example:
            >>> CoordinateReferenceSystem.from_user_input(4326).authority_code
            'EPSG:4326'
        """
        if isinstance(value, cls):
            return value
        if isinstance(value, CRS):
            return cls(_rebound(value), value.srs)
        return _cached(_normalize(value))

    @property
    def crs(self) -> CRS:
        """The underlying :class:`pyproj.CRS`."""
        return self._crs

    @property
    def definition(self) -> str:
        """The input this CRS was resolved from."""
        return self._definition

    @property
    def name(self) -> str:
        """Human readable CRS name, for example ``"WGS 84"``."""
        return self._crs.name

    @property
    def axes(self) -> tuple[AxisSpec, ...]:
        """The CRS's axes in EPSG-declared order, not in coordinate value order."""
        return self._axes

    @property
    def axis_abbreviations(self) -> tuple[str, ...]:
        """Axis abbreviations in EPSG-declared order, for example ``("Lat", "Lon")``."""
        return tuple(axis.abbrev for axis in self._axes)

    @property
    def axis_units(self) -> tuple[str, ...]:
        """Axis unit names in EPSG-declared order, ``("degree", "degree")`` for 4326."""
        return tuple(axis.unit_name for axis in self._axes)

    @property
    def dimension(self) -> int:
        """Number of axes the CRS declares."""
        return len(self._axes)

    @property
    def authority_code(self) -> str | None:
        """``"AUTH:CODE"`` if the CRS is identified in an authority, else None."""
        authority = self._crs.to_authority()
        return None if authority is None else f"{authority[0]}:{authority[1]}"

    @property
    def is_dynamic(self) -> bool:
        """Whether any datum in this CRS is a dynamic reference frame.

        Coordinates in a dynamic frame require a coordinate epoch to be
        meaningful, so this drives the epoch requirement in
        :mod:`geodetic_engine.geodesy.transformation`.
        """
        return _has_dynamic_frame(self._crs.to_json_dict())

    @property
    def value_axis_order(self) -> tuple[int, ...]:
        """Indices of the declared axes, in coordinate value order.

        Element ``i`` is the position, among :attr:`axes`, of the axis whose
        value comes ``i``-th in the ``xy`` ordering this package uses. This is
        the bridge between what the EPSG dataset declares and the order values
        are actually in, and it is the thing a caller needs in order to label a
        coordinate correctly.

        Axes are identified by direction, falling back to the abbreviation for
        polar CRSs whose axes share a direction: ``EPSG:32661`` declares both
        of its axes pointing south, and only the ``N`` and ``E`` abbreviations
        distinguish them.

        Returns:
            A permutation of ``range(self.dimension)``.

        Example:
            >>> crs = CoordinateReferenceSystem.from_user_input("EPSG:4326")
            >>> crs.axis_abbreviations
            ('Lat', 'Lon')
            >>> crs.value_axis_order
            (1, 0)

            So the first coordinate value is the axis at declared index 1,
            longitude.
        """
        directions = [axis.direction.lower() for axis in self._axes]
        east = next((i for i, d in enumerate(directions) if d in _EASTINGS), None)
        north = next((i for i, d in enumerate(directions) if d in _NORTHINGS), None)
        if east is None or north is None or east == north:
            # Both axes of a polar CRS share a direction, so resolve the pair
            # together from the abbreviations rather than mixing the two
            # sources and risking them naming the same axis.
            abbreviations = [axis.abbrev.upper() for axis in self._axes]
            by_letter = (
                _first_index(abbreviations, "E"),
                _first_index(abbreviations, "N"),
            )
            if by_letter[0] is not None and by_letter[0] != by_letter[1]:
                east, north = by_letter
        if east is None or north is None or east == north:
            return tuple(range(len(self._axes)))
        vertical = [i for i, d in enumerate(directions) if d in _VERTICALS]
        placed = {east, north, *vertical}
        rest = [i for i in range(len(self._axes)) if i not in placed]
        return (east, north, *vertical, *rest)

    @property
    def value_axis_abbreviations(self) -> tuple[str, ...]:
        """Axis abbreviations in coordinate value order, ``("Lon", "Lat")`` for 4326."""
        return tuple(self._axes[index].abbrev for index in self.value_axis_order)

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, CoordinateReferenceSystem):
            return NotImplemented
        return bool(self._crs == other._crs)

    def __hash__(self) -> int:
        return hash(self._definition)

    def __repr__(self) -> str:
        return f"CoordinateReferenceSystem({self.authority_code or self.name!r})"


def _normalize(value: Any) -> str:
    """Render user input as the cache key string PROJ will be given."""
    if isinstance(value, int):
        return f"EPSG:{value}"
    if isinstance(value, str):
        return value.strip()
    raise UnresolvableCRSError(
        f"cannot resolve a CRS from {type(value).__name__}; "
        "give an authority code, WKT, a PROJ string or a pyproj.CRS"
    )


@lru_cache(maxsize=256)
def _cached(definition: str) -> CoordinateReferenceSystem:
    """Resolve and cache a CRS by its textual definition."""
    try:
        crs = CRS.from_user_input(definition)
    except CRSError as error:
        raise UnresolvableCRSError(
            f"could not resolve {definition!r} as a CRS: {error}"
        ) from error
    return CoordinateReferenceSystem(_rebound(crs), definition)


def _rebound(crs: CRS) -> CRS:
    """Restore the bound CRS the database defines, which PROJ unwraps.

    PROJ discards the ``BOUNDCRS`` wrapper when it builds a CRS from a code,
    keeping the binding for operation selection but not on the object. Reading
    the stored definition back gives an object that can say which operation it
    carries. See :mod:`geodetic_engine.geodesy.database`.
    """
    if crs.is_bound:
        return crs
    # An exact identification only: a fuzzy match could rebind a CRS onto a
    # different authority's bound definition.
    authority = crs.to_authority(min_confidence=100)
    if authority is None:
        return crs
    definition = bound_definition(*authority)
    if definition is None:
        return crs
    try:
        rebound = CRS.from_wkt(definition)
    except CRSError as error:
        logger.warning(
            "%s:%s is stored as a bound CRS that could not be read back: %s",
            *authority,
            error,
        )
        return crs
    return rebound if rebound.is_bound else crs


def _has_dynamic_frame(definition: object) -> bool:
    """Search a PROJJSON tree for a datum carrying a frame reference epoch."""
    if isinstance(definition, dict):
        if _DYNAMIC_MARKER in definition:
            return True
        return any(_has_dynamic_frame(value) for value in definition.values())
    if isinstance(definition, list):
        return any(_has_dynamic_frame(item) for item in definition)
    return False


def _first_index(values: list[str], wanted: str) -> int | None:
    """Position of the first matching entry, or None."""
    return values.index(wanted) if wanted in values else None
