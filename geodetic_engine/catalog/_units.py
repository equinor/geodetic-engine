"""Listing units of measure."""

from __future__ import annotations

import math
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from enum import StrEnum
from functools import lru_cache
from typing import Any, ClassVar

from pyproj.database import get_units_map

from geodetic_engine._referencing.database import DatabaseIdentity
from geodetic_engine.catalog import _filters, _registry
from geodetic_engine.catalog._entry import RegistryEntry, columns, order, projjson_id
from geodetic_engine.catalog._filters import RangeFilter, ReferenceFilter, TextFilter
from geodetic_engine.catalog._listing import Listing
from geodetic_engine.catalog._registry import Key, UnitRow


class UnitKind(StrEnum):
    """What a unit measures, as pyproj categorises units."""

    LINEAR = "linear"
    """Length, such as metre or US survey foot."""

    LINEAR_PER_TIME = "linear_per_time"
    """Velocity, such as metres per year."""

    ANGULAR = "angular"
    """Angle, such as degree or grad."""

    ANGULAR_PER_TIME = "angular_per_time"
    """Angular rate, such as arc-seconds per year."""

    SCALE = "scale"
    """Ratio, such as unity or parts per million."""

    SCALE_PER_TIME = "scale_per_time"
    """Rate of a ratio, such as parts per billion per year."""

    TIME = "time"
    """Time, such as year or second."""

    PARAMETRIC = "parametric"
    """A parameter such as pressure."""


_PROJJSON_TYPES = {
    UnitKind.LINEAR: "LinearUnit",
    UnitKind.LINEAR_PER_TIME: "LinearUnit",
    UnitKind.ANGULAR: "AngularUnit",
    UnitKind.ANGULAR_PER_TIME: "AngularUnit",
    UnitKind.SCALE: "ScaleUnit",
    UnitKind.SCALE_PER_TIME: "ScaleUnit",
    UnitKind.TIME: "TimeUnit",
    UnitKind.PARAMETRIC: "ParametricUnit",
}

# proj.db's unit_of_measure.type, to PROJJSON's unit type.
_SQL_TYPES = {
    "length": "LinearUnit",
    "angle": "AngularUnit",
    "scale": "ScaleUnit",
    "time": "TimeUnit",
    "parametric": "ParametricUnit",
}

# The units PROJJSON writes by name alone, as PROJ does.
_SHORTHAND = frozenset({"metre", "degree", "unity"})


def unit_json(row: UnitRow, key: Key | None) -> dict[str, Any] | str:
    """A unit from proj.db in PROJJSON's form, as PROJ writes it in an axis.

    Args:
        row: The unit.
        key: Its authority code, to state as ``id``; None leaves the id out,
            as PROJ does inside a coordinate system.
    """
    # PROJ reads EPSG's "degree (supplier to define representation)" as degree.
    name = "degree" if row.name.startswith("degree (supplier") else row.name
    if key is None and name in _SHORTHAND:
        return name
    found: dict[str, Any] = {"type": _SQL_TYPES.get(row.type, "Unit"), "name": name}
    factor = row.conversion_factor
    if factor is None and row.type == "angle":
        # PROJ reads sexagesimal units, which have no factor, in degrees.
        factor = math.pi / 180
    if factor is not None:
        found["conversion_factor"] = factor
    if key is not None:
        found["id"] = projjson_id(*key)
    return found


@dataclass(frozen=True, slots=True, kw_only=True)
class UnitEntry(RegistryEntry):
    """A unit of measure the active database holds.

    Listed by :func:`~geodetic_engine.catalog.units`, from
    :func:`pyproj.database.get_units_map`.

    For reading a length or angle unit an OSDU payload states,
    :mod:`geodetic_engine.units` is the module to use; this lists what PROJ
    knows.

    Attributes:
        kind: What the unit measures.
        conversion_factor: Multiplier to the SI unit of the same quantity:
            metres, radians, unity or seconds. None for a unit that is no
            plain multiple, such as EPSG's sexagesimal degrees.
        proj_short_name: PROJ's own short name for the unit, as a PROJ string
            writes it (``"us-ft"``), or None.
    """

    _COLUMNS: ClassVar[tuple[str, ...]] = (
        "kind",
        "conversion_factor",
        "proj_short_name",
    )

    kind: UnitKind
    conversion_factor: float | None
    proj_short_name: str | None = None

    @property
    def projjson_type(self) -> str:
        """``"LinearUnit"``, ``"AngularUnit"``, ``"ScaleUnit"`` and so on."""
        return _PROJJSON_TYPES.get(self.kind, "Unit")

    def to_json_dict(self) -> dict[str, Any]:
        """The unit as PROJJSON's unit object, with its id.

        PROJ exports a unit only inside another object, so this is the form
        it writes there. It has no ``$schema``: PROJJSON's schema does not
        admit a unit as a document of its own.

        Example:
            >>> from geodetic_engine import catalog
            >>> foot = catalog.units(code="EPSG:9003").one().to_json_dict()
            >>> foot["type"], foot["name"], foot["id"]
            ('LinearUnit', 'US survey foot', {'authority': 'EPSG', 'code': 9003})
        """
        found: dict[str, Any] = {"type": self.projjson_type, "name": self.name}
        if self.conversion_factor is not None:
            found["conversion_factor"] = self.conversion_factor
        found["id"] = projjson_id(self.auth_name, self.code)
        return found

    def _summary(self) -> dict[str, Any]:
        summary: dict[str, Any] = {"kind": self.kind.value}
        if self.conversion_factor is not None:
            summary["conversion_factor"] = self.conversion_factor
        if self.proj_short_name:
            summary["proj_short_name"] = self.proj_short_name
        return summary


@lru_cache(maxsize=4)
def entries(databases: DatabaseIdentity) -> tuple[UnitEntry, ...]:
    """Every unit in the database, built once per database."""
    aliases = _registry.aliases(databases)
    replacements = _registry.replacements(databases)
    kinds = {kind.value: kind for kind in UnitKind}
    built = []
    for authority in _registry.authorities(databases):
        for unit in get_units_map(auth_name=authority, allow_deprecated=True).values():
            key = ("unit_of_measure", unit.auth_name, unit.code)
            built.append(
                UnitEntry(
                    auth_name=unit.auth_name,
                    code=unit.code,
                    name=unit.name,
                    deprecated=bool(unit.deprecated),
                    aliases=aliases.get(key, ()),
                    replaced_by=replacements.get(key, ()),
                    kind=kinds.get(unit.category, UnitKind.SCALE),
                    conversion_factor=unit.conv_factor,
                    proj_short_name=unit.proj_short_name or None,
                )
            )
    return tuple(sorted(built, key=lambda e: order(e.auth_name, e.code)))


def units(
    *,
    authority: str | Iterable[str] | None = None,
    name: TextFilter | None = None,
    code: ReferenceFilter | None = None,
    kind: str | UnitKind | Iterable[str | UnitKind] | None = None,
    conversion_factor: RangeFilter | None = None,
    deprecated: bool | None = False,
    limit: int | None = None,
    offset: int = 0,
) -> Listing[UnitEntry]:
    """List the units of measure the active database holds.

    Read through :func:`pyproj.database.get_units_map`. Every filter given
    must hold.

    Args:
        authority: Only these authorities, for example ``"EPSG"``.
        name: Text the name or an alias contains, without case, or a pattern.
        code: Only these codes, as for :func:`~geodetic_engine.catalog.crs`.
        kind: Only these kinds, for example ``"linear"``.
        conversion_factor: The factor to SI, exactly or as ``(low, high)``
            with None for an open end. A unit with no factor never matches.
        deprecated: False (default) leaves deprecated units out, True lists
            only them, None lists both.
        limit: At most this many entries.
        offset: Skip this many entries first.

    Returns:
        The units, ordered by authority and code.

    Raises:
        UnknownAuthorityError: If ``authority`` names an authority the
            database does not hold.
        InvalidFilterError: If a filter is malformed.

    Example:
        >>> from geodetic_engine import catalog
        >>> feet = catalog.units(authority="EPSG", kind="linear", name="foot")
        >>> "US survey foot" in [unit.name for unit in feet]
        True
    """
    code, kind = _filters.given(code), _filters.given(kind)
    databases = _registry.identity()
    authorities = _filters.authorities(authority, databases)
    names = _filters.text(name, "name")
    codes = _filters.codes(code, databases, authorities)
    kinds = _filters.choices(kind, UnitKind, "unit kind")
    factors = _filters.value_range(conversion_factor, "conversion_factor")
    deprecated_ = _filters.flag(deprecated, "deprecated")
    limit, offset = _filters.paging(limit, offset)
    wanted = None if authorities is None else frozenset(authorities)

    def admits(entry: UnitEntry) -> bool:
        if wanted is not None and entry.auth_name not in wanted:
            return False
        if not _filters.admits_flag(deprecated_, entry.deprecated):
            return False
        if codes is not None and not codes.admits(entry.auth_name, entry.code):
            return False
        if kinds is not None and entry.kind not in kinds:
            return False
        if not _filters.in_range(factors, entry.conversion_factor):
            return False
        return names is None or names((entry.name, *entry.aliases))

    def produce() -> Iterator[UnitEntry]:
        yield from filter(admits, entries(databases))

    filters = _filters.echo(
        authority=authorities,
        name=name,
        code=code,
        kind=kind,
        conversion_factor=conversion_factor,
        deprecated=deprecated_,
        limit=limit,
        offset=offset or None,
    )
    return Listing(
        "units",
        filters,
        produce,
        columns=columns(UnitEntry),
        databases=databases,
        limit=limit,
        offset=offset,
    )
