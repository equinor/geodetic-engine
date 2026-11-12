"""Reading and checking the filters every listing function shares.

Every filter is checked when the listing function is called, so a malformed
one fails at the call that gave it, not at the first use of the listing.
"""

from __future__ import annotations

import math
import re
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from enum import StrEnum
from functools import lru_cache
from typing import Any, cast

from pyproj.aoi import AreaOfInterest, BBox
from pyproj.aoi import AreaOfUse as PyprojAreaOfUse

from geodetic_engine._referencing.database import DatabaseIdentity
from geodetic_engine.catalog import _registry
from geodetic_engine.catalog._entry import ObjectRef, RegistryEntry
from geodetic_engine.catalog.errors import InvalidFilterError, UnknownAuthorityError
from geodetic_engine.geodesy import AreaOfUse

type TextFilter = str | re.Pattern[str]
"""Free text: a case-insensitive substring, or a pattern to ``search()`` with."""

type ReferenceFilter = (
    str
    | int
    | ObjectRef
    | RegistryEntry
    | Iterable[str | int | ObjectRef | RegistryEntry]
)
"""One object or several: ``"AUTH:CODE"``, a bare code, an entry, an
:class:`ObjectRef`, or a name or alias."""

type AreaFilter = (
    tuple[float, float, float, float]
    | AreaOfInterest
    | AreaOfUse
    | PyprojAreaOfUse
    | BBox
)
"""``(west, south, east, north)`` in degrees, or a pyproj or geodesy box."""

type RangeFilter = float | tuple[float | None, float | None]
"""One number, matched exactly, or ``(low, high)`` with None for an open end."""

type Reference = str | int | ObjectRef | RegistryEntry
type Bounds = tuple[float, float, float, float]
# (auth_name case folded, code): authorities are matched without case.
type Folded = tuple[str, str]


def given[T](value: T) -> T:
    """An iterable filter value as a tuple, so that it can be read twice."""
    if value is None or isinstance(
        value, (str, bytes, int, float, ObjectRef, RegistryEntry, re.Pattern, Mapping)
    ):
        return value
    if isinstance(value, Iterable):
        return cast("T", tuple(value))
    return value


def folded(auth_name: str, code: str) -> Folded:
    """An authority code as filters compare it."""
    return (auth_name.casefold(), str(code))


def authorities(
    value: str | Iterable[str] | None, databases: DatabaseIdentity
) -> tuple[str, ...] | None:
    """The authorities a filter names, as the database spells them."""
    if value is None:
        return None
    wanted = [value] if isinstance(value, str) else list(value)
    if not wanted or not all(isinstance(item, str) for item in wanted):
        raise InvalidFilterError(f"authority must be a name or names, not {value!r}")
    known = {name.casefold(): name for name in _registry.authorities(databases)}
    found = []
    for item in wanted:
        name = known.get(item.strip().casefold())
        if name is None:
            raise UnknownAuthorityError(
                f"the active database has no authority {item!r}; "
                f"it has {', '.join(sorted(known.values()))}"
            )
        if name not in found:
            found.append(name)
    return tuple(found)


def text(value: TextFilter | None, what: str) -> Callable[[Iterable[str]], bool] | None:
    """A matcher for free text: a case-insensitive substring, or a pattern."""
    if value is None:
        return None
    if isinstance(value, re.Pattern):
        pattern = value
        return lambda texts: any(pattern.search(t) for t in texts)
    if not isinstance(value, str) or not value.strip():
        raise InvalidFilterError(f"{what} must be text or a re.Pattern, not {value!r}")
    needle = value.strip().casefold()
    return lambda texts: any(needle in t.casefold() for t in texts)


def _items(value: ReferenceFilter) -> list[Reference]:
    if isinstance(value, (str, int, ObjectRef, RegistryEntry)):
        return [value]
    return list(value)


def authority_code(value: str, databases: DatabaseIdentity) -> Folded | None:
    """``"AUTH:CODE"`` read as a code, when ``AUTH`` is a known authority."""
    auth, separator, code = value.strip().partition(":")
    if not separator or not code.strip():
        return None
    known = {name.casefold() for name in _registry.authorities(databases)}
    if auth.strip().casefold() not in known:
        return None
    return folded(auth.strip(), code.strip())


@dataclass(frozen=True, slots=True)
class Codes:
    """The ``code`` filter: full authority codes, and bare codes."""

    full: frozenset[Folded]
    bare: frozenset[str]

    def admits(self, auth_name: str, code: str) -> bool:
        return code in self.bare or folded(auth_name, code) in self.full


def codes(
    value: ReferenceFilter | None,
    databases: DatabaseIdentity,
    allowed: tuple[str, ...] | None,
) -> Codes | None:
    """The ``code`` filter, checked against the ``authority`` filter."""
    if value is None:
        return None
    items = _items(value)
    if not items:
        raise InvalidFilterError("code is empty")
    full: set[Folded] = set()
    bare: set[str] = set()
    for item in items:
        if isinstance(item, (ObjectRef, RegistryEntry)):
            full.add(folded(item.auth_name, item.code))
        elif isinstance(item, bool):
            raise InvalidFilterError(f"{item!r} is not a code")
        elif isinstance(item, int):
            bare.add(str(item))
        elif isinstance(item, str) and item.strip():
            parsed = authority_code(item, databases)
            if parsed is None and ":" in item:
                raise InvalidFilterError(
                    f"{item!r} names no authority the active database holds"
                )
            if parsed is None:
                bare.add(item.strip())
            else:
                full.add(parsed)
        else:
            raise InvalidFilterError(f"{item!r} is not a code")
    if allowed is not None:
        permitted = {name.casefold() for name in allowed}
        outside = sorted(f"{a}:{c}" for a, c in full if a not in permitted)
        if outside:
            raise InvalidFilterError(
                f"code {', '.join(outside)} is outside authority {', '.join(allowed)}"
            )
    return Codes(full=frozenset(full), bare=frozenset(bare))


@lru_cache(maxsize=16)
def registered(
    tables: frozenset[str], databases: DatabaseIdentity
) -> Mapping[Folded, tuple[str, ...]]:
    """The objects in some tables, with their names and aliases."""
    found: dict[Folded, list[str]] = {}
    for (table, auth, code), stated in _registry.names(databases).items():
        if table in tables:
            found.setdefault(folded(auth, code), []).append(stated)
    for (table, auth, code), alternatives in _registry.aliases(databases).items():
        key = folded(auth, code)
        if table in tables and key in found:
            found[key].extend(alternatives)
    return {key: tuple(value) for key, value in found.items()}


def references(
    value: ReferenceFilter | None,
    what: str,
    known: Mapping[Folded, tuple[str, ...]],
    databases: DatabaseIdentity,
) -> frozenset[Folded] | None:
    """The objects a reference filter names, each checked to exist.

    Each item is an entry or :class:`ObjectRef`, ``"AUTH:CODE"``, a bare code
    (an integer, or digits as text) in any authority, or a name or alias,
    matched exactly but for case. An item that names nothing in ``known`` is
    an error rather than an empty listing, so that ``datum="EPSG:4326"`` -- a
    CRS code -- does not pass unnoticed.
    """
    if value is None:
        return None
    items = _items(value)
    if not items:
        raise InvalidFilterError(f"{what} is empty")
    found: set[Folded] = set()
    for item in items:
        if isinstance(item, bool):
            raise InvalidFilterError(f"{item!r} is not a {what}")
        if isinstance(item, (ObjectRef, RegistryEntry)):
            matches = {folded(item.auth_name, item.code)} & known.keys()
        elif isinstance(item, int) or (
            isinstance(item, str) and item.strip().isdigit()
        ):
            matches = {key for key in known if key[1] == str(item).strip()}
        elif isinstance(item, str) and item.strip():
            parsed = authority_code(item, databases)
            if parsed is not None:
                matches = {parsed} & known.keys()
            else:
                wanted = item.strip().casefold()
                matches = {
                    key
                    for key, stated in known.items()
                    if any(label.casefold() == wanted for label in stated)
                }
                if not matches:
                    raise InvalidFilterError(
                        f"the active database has no {what} named {item!r}"
                    )
        else:
            raise InvalidFilterError(f"{item!r} is not a {what}")
        if not matches:
            raise InvalidFilterError(f"{item} is not a {what} in the active database")
        found |= matches
    return frozenset(found)


def choices[E: StrEnum](
    value: str | E | Iterable[str | E] | None, kinds: type[E], what: str
) -> frozenset[E] | None:
    """Members of an enumeration, given by member or by value without case."""
    if value is None:
        return None
    items = [value] if isinstance(value, str) else list(value)
    if not items:
        raise InvalidFilterError(f"{what} is empty")
    by_value = {member.value.casefold(): member for member in kinds}
    found = set()
    for item in items:
        member = by_value.get(str(item).strip().casefold())
        if member is None:
            raise InvalidFilterError(
                f"{item!r} is not a {what}; choose from "
                f"{', '.join(repr(m.value) for m in kinds)}"
            )
        found.add(member)
    return frozenset(found)


def labels(value: str | Iterable[str] | None, what: str) -> frozenset[str] | None:
    """Exact names, compared without case."""
    if value is None:
        return None
    items = [value] if isinstance(value, str) else list(value)
    if not items or not all(isinstance(item, str) and item.strip() for item in items):
        raise InvalidFilterError(f"{what} must be a name or names, not {value!r}")
    return frozenset(item.strip().casefold() for item in items)


def flag(value: object, what: str) -> bool | None:
    """A three-way filter: True, False, or None for either."""
    if value is None or isinstance(value, bool):
        return value
    raise InvalidFilterError(f"{what} must be True, False or None, not {value!r}")


def admits_flag(wanted: bool | None, actual: bool) -> bool:
    """Whether a value passes a three-way filter."""
    return wanted is None or wanted == actual


def value_range(value: RangeFilter | None, what: str) -> tuple[float, float] | None:
    """A closed range: one number, or ``(low, high)`` with None for open."""
    if value is None:
        return None
    if isinstance(value, bool):
        raise InvalidFilterError(f"{what} must be a number or (low, high)")
    if isinstance(value, (int, float)):
        exact = float(value)
        if math.isnan(exact):
            raise InvalidFilterError(f"{what} is not a number")
        return (exact, exact)
    try:
        low, high = value
    except (TypeError, ValueError):
        raise InvalidFilterError(
            f"{what} must be a number or (low, high), not {value!r}"
        ) from None
    bounds = []
    for bound, default in ((low, -math.inf), (high, math.inf)):
        if bound is None:
            bounds.append(default)
        elif isinstance(bound, (int, float)) and not isinstance(bound, bool):
            if math.isnan(bound):
                raise InvalidFilterError(f"{what} has a bound that is not a number")
            bounds.append(float(bound))
        else:
            raise InvalidFilterError(f"{what} has a bound {bound!r} that is no number")
    if bounds[0] > bounds[1]:
        raise InvalidFilterError(f"{what} {value!r} has its bounds the wrong way round")
    return (bounds[0], bounds[1])


def in_range(span: tuple[float, float] | None, value: float | None) -> bool:
    """Whether a value is inside a range; a missing value never is."""
    if span is None:
        return True
    return value is not None and span[0] <= value <= span[1]


def area_of_interest(value: AreaFilter | None) -> Bounds | None:
    """West, south, east, north in degrees, checked.

    West greater than east is an area across the antimeridian, as in PROJ.
    """
    if value is None:
        return None
    if isinstance(value, (AreaOfUse, PyprojAreaOfUse, BBox)):
        bounds = (value.west, value.south, value.east, value.north)
    elif isinstance(value, AreaOfInterest):
        bounds = (
            value.west_lon_degree,
            value.south_lat_degree,
            value.east_lon_degree,
            value.north_lat_degree,
        )
    else:
        try:
            west, south, east, north = value
            bounds = (float(west), float(south), float(east), float(north))
        except (TypeError, ValueError):
            raise InvalidFilterError(
                "area_of_interest must be (west, south, east, north) in degrees, "
                f"not {value!r}"
            ) from None
    west, south, east, north = (float(b) for b in bounds)
    if not all(math.isfinite(b) for b in (west, south, east, north)):
        raise InvalidFilterError(f"area_of_interest {value!r} is not finite")
    if not (-180 <= west <= 180 and -180 <= east <= 180):
        raise InvalidFilterError(
            f"area_of_interest {value!r} has a longitude outside -180..180"
        )
    if not (-90 <= south <= north <= 90):
        raise InvalidFilterError(
            f"area_of_interest {value!r} needs -90 <= south <= north <= 90"
        )
    return (west, south, east, north)


def _created(west: float, south: float, east: float, north: float) -> Bounds:
    """A box as PROJ's ``GeographicBoundingBox::create`` widens a degenerate one."""
    if west == east:
        west = math.nextafter(west, -math.inf) if west > -180 else west
        east = math.nextafter(east, math.inf) if east < 180 else east
    if south == north:
        south = math.nextafter(south, -math.inf) if south > -90 else south
        north = math.nextafter(north, math.inf) if north < 90 else north
    return (west, south, east, north)


def _box_intersects(box: Bounds, other: Bounds) -> bool:
    """PROJ's ``GeographicBoundingBox::intersects``."""
    west, south, east, north = box
    o_west, o_south, o_east, o_north = other
    if north < o_south or south > o_north:
        return False
    if west == -180 and east == 180 and o_west > o_east:
        return True
    if o_west == -180 and o_east == 180 and west > east:
        return True
    if west <= east:
        if o_west <= o_east:
            return max(west, o_west) < min(east, o_east)
        if o_west > 180 or o_east < -180:
            return False
        return _box_intersects(box, (o_west, o_south, 180.0, o_north)) or (
            _box_intersects(box, (-180.0, o_south, o_east, o_north))
        )
    if o_west <= o_east:
        return _box_intersects(other, box)
    return True


def _box_contains(box: Bounds, other: Bounds) -> bool:
    """PROJ's ``GeographicBoundingBox::contains``."""
    west, south, east, north = box
    o_west, o_south, o_east, o_north = other
    if not (south <= o_south and north >= o_north):
        return False
    if west == -180 and east == 180:
        return o_west != o_east
    if o_west == -180 and o_east == 180:
        return False
    if west < east:
        return o_west < o_east and west <= o_west and east >= o_east
    if o_west < o_east:
        return o_west >= west or o_east <= east
    return west <= o_west and east >= o_east


def intersects(area: AreaOfUse, bounds: Bounds) -> bool:
    """Whether an area of use and an area of interest share any point.

    Decided exactly as PROJ decides it when it searches CRSs by area, so that
    every listing agrees with :func:`~geodetic_engine.catalog.crs`: edges
    count between two boxes that do not cross the antimeridian, and do not
    when either does.
    """
    west, south, east, north = bounds
    if area.west <= area.east and west <= east:
        return not (
            area.east < west
            or area.west > east
            or area.north < south
            or area.south > north
        )
    extent = _created(area.west, area.south, area.east, area.north)
    return _box_intersects(_created(*bounds), extent)


def contains(area: AreaOfUse, bounds: Bounds) -> bool:
    """Whether an area of use contains the whole area of interest, as PROJ decides."""
    west, south, east, north = bounds
    if area.west <= area.east and west <= east:
        return not (
            west < area.west
            or east > area.east
            or south < area.south
            or north > area.north
        )
    extent = _created(area.west, area.south, area.east, area.north)
    return _box_contains(extent, _created(*bounds))


def located(areas: tuple[AreaOfUse, ...], bounds: Bounds | None, whole: bool) -> bool:
    """Whether any area of use qualifies for an area of interest."""
    if bounds is None:
        return True
    test = contains if whole else intersects
    return any(test(area, bounds) for area in areas)


def paging(limit: int | None, offset: int) -> tuple[int | None, int]:
    """``limit`` and ``offset``, checked."""
    for value, what in ((limit, "limit"), (offset, "offset")):
        if value is None and what == "limit":
            continue
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise InvalidFilterError(
                f"{what} must be a whole number >= 0, not {value!r}"
            )
    return limit, offset


def echo(**given: Any) -> Mapping[str, Any]:
    """The filters a listing was asked for, as JSON values, for its export.

    A filter left at None is left out. Not ``deprecated``: it defaults to
    False, so None there asks for something, the deprecated objects as well.
    """
    return {
        name: _json(value)
        for name, value in given.items()
        if value is not None or name == "deprecated"
    }


def _json(value: Any) -> Any:
    # Inside a value None means something, such as the open end of a range.
    if value is None or isinstance(value, (bool, int, float, str)):
        return value.value if isinstance(value, StrEnum) else value
    if isinstance(value, re.Pattern):
        return {"pattern": value.pattern}
    if isinstance(value, (ObjectRef, RegistryEntry)):
        return value.authority_code
    if isinstance(value, (AreaOfUse, PyprojAreaOfUse, BBox, AreaOfInterest)):
        bounds = area_of_interest(value)
        return list(bounds) if bounds is not None else None
    if isinstance(value, Mapping):
        return {str(k): _json(v) for k, v in value.items()}
    if isinstance(value, Iterable):
        return [_json(item) for item in value]
    return str(value)
