"""What every catalog entry shares: identity, status, and its JSON forms."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, ClassVar

from geodetic_engine.geodesy import AreaOfUse

# Where the official PROJJSON schema of the PROJ this package pins lives. PROJ
# writes it into every object it exports; it is repeated here for the objects
# PROJ cannot export on their own.
PROJJSON_SCHEMA = "https://proj.org/schemas/v0.7/projjson.schema.json"


def projjson_id(auth_name: str, code: str) -> dict[str, Any]:
    """A PROJJSON ``id``, with the code an integer wherever PROJ writes one."""
    numeric = code.isdigit() and str(int(code)) == code
    return {"authority": auth_name, "code": int(code) if numeric else code}


def area_json(areas: tuple[AreaOfUse, ...]) -> dict[str, Any]:
    """PROJJSON's ``area`` and ``bbox``, or ``usages`` when there is more than one."""

    def bbox(area: AreaOfUse) -> dict[str, float]:
        return {
            "south_latitude": area.south,
            "west_longitude": area.west,
            "north_latitude": area.north,
            "east_longitude": area.east,
        }

    if not areas:
        return {}
    if len(areas) == 1:
        return {"area": areas[0].name, "bbox": bbox(areas[0])}
    return {"usages": [{"area": a.name, "bbox": bbox(a)} for a in areas]}


def accuracy_json(accuracy: float) -> str:
    """An accuracy as PROJJSON states one: a string, in metres."""
    return repr(float(accuracy))


def order(auth_name: str, code: str) -> tuple[str, int, int, str]:
    """Sort by authority, then by code: numerically where the code is a number."""
    if code.isdigit():
        return (auth_name.casefold(), 0, int(code), "")
    return (auth_name.casefold(), 1, 0, code)


@dataclass(frozen=True, slots=True)
class ObjectRef:
    """Another object in the active database, by authority, code and name.

    The form PROJJSON uses for a datum ensemble member or an operation method,
    and how every entry points at the objects it uses: a CRS at its datums, an
    operation at its source and target CRS, a datum at its ellipsoid. Any
    filter that takes a reference, and every ``code`` filter, accepts one, so
    following a reference is one more listing call::

        crs = catalog.crs(code="EPSG:25832").one()
        catalog.datums(code=crs.datums)

    Attributes:
        auth_name: Authority, for example ``"EPSG"``.
        code: Code within that authority, as text.
        name: Name of the object.
    """

    auth_name: str
    code: str
    name: str

    @property
    def authority_code(self) -> str:
        """``"AUTH:CODE"``, for example ``"EPSG:6258"``."""
        return f"{self.auth_name}:{self.code}"

    def to_json_dict(self) -> dict[str, Any]:
        """The reference as PROJJSON writes one: ``name`` and ``id``."""
        return {"name": self.name, "id": projjson_id(self.auth_name, self.code)}

    def __str__(self) -> str:
        return f"{self.name} ({self.authority_code})"


@dataclass(frozen=True, slots=True)
class CatalogEntry:
    """Anything a :class:`~geodetic_engine.catalog.Listing` holds.

    Every entry exports itself two ways: :meth:`to_json_dict`, its full
    definition, and :meth:`to_summary_dict`, the item it contributes to a
    listing's JSON.
    """

    def to_json_dict(self) -> dict[str, Any]:
        """The entry's full definition as JSON."""
        raise NotImplementedError

    def to_json(self, *, indent: int | None = 2) -> str:
        """:meth:`to_json_dict` as text.

        Args:
            indent: Indentation, or None for one line.
        """
        return json.dumps(self.to_json_dict(), indent=indent, ensure_ascii=False)

    def to_summary_dict(self) -> dict[str, Any]:
        """The entry as one item of a listing's JSON."""
        raise NotImplementedError

    def _row(self) -> dict[str, Any]:
        raise NotImplementedError


@dataclass(frozen=True, slots=True, kw_only=True)
class RegistryEntry(CatalogEntry):
    """What every object an authority registers has: its id, name and status.

    The base of every catalog entry but :class:`GridEntry`, since a grid file
    has no authority code of its own.

    Attributes:
        auth_name: Authority, for example ``"EPSG"``.
        code: Code within that authority, as text.
        name: Name, as the authority publishes it.
        deprecated: Whether the authority has deprecated the object.
        aliases: Other names the database records for it, for example
            ``"ETRS89"`` for the European Terrestrial Reference System 1989
            ensemble. The ``name`` filter matches these too.
        replaced_by: ``"AUTH:CODE"`` of the objects that replace a deprecated
            one, when the authority states any.
    """

    _COLUMNS: ClassVar[tuple[str, ...]] = ()

    auth_name: str
    code: str
    name: str
    deprecated: bool
    aliases: tuple[str, ...] = field(default=(), repr=False)
    replaced_by: tuple[str, ...] = field(default=(), repr=False)

    @property
    def authority_code(self) -> str:
        """``"AUTH:CODE"``, for example ``"EPSG:25832"``."""
        return f"{self.auth_name}:{self.code}"

    @property
    def projjson_type(self) -> str:
        """The PROJJSON ``type`` of the object, for example ``"ProjectedCRS"``."""
        raise NotImplementedError

    def to_json_dict(self) -> dict[str, Any]:
        """The object's full definition as PROJJSON."""
        raise NotImplementedError

    def to_summary_dict(self) -> dict[str, Any]:
        """The entry as one item of a listing's JSON.

        PROJJSON's names and value shapes wherever PROJJSON has the property
        (``type``, ``name``, ``id``, ``area``, ``bbox``, ``accuracy``), and
        the facts PROJJSON has no place for (``deprecated``, ``replaced_by``)
        beside them. Every item validates against the schema
        :func:`~geodetic_engine.catalog.listing_schema` returns.
        """
        summary: dict[str, Any] = {
            "type": self.projjson_type,
            "name": self.name,
            "id": projjson_id(self.auth_name, self.code),
        }
        summary.update(self._summary())
        summary["deprecated"] = self.deprecated
        if self.replaced_by:
            summary["replaced_by"] = [authority_id(code) for code in self.replaced_by]
        if self.aliases:
            summary["aliases"] = list(self.aliases)
        return summary

    def _summary(self) -> dict[str, Any]:
        return {}

    def _row(self) -> dict[str, Any]:
        row: dict[str, Any] = {
            "authority_code": self.authority_code,
            "auth_name": self.auth_name,
            "code": self.code,
            "name": self.name,
        }
        row.update(table_row(self, self._COLUMNS))
        row["deprecated"] = self.deprecated
        return row

    def __str__(self) -> str:
        return f"{self.name} ({self.authority_code})"


@dataclass(frozen=True, slots=True, kw_only=True)
class LocatedEntry(RegistryEntry):
    """A registered object that states where it is valid.

    Attributes:
        areas_of_use: The area of every usage the database records, in its
            order. Most objects have one. An ``area_of_interest`` filter
            accepts an object when any of them qualifies.
    """

    areas_of_use: tuple[AreaOfUse, ...] = field(default=(), repr=False)

    @property
    def area_of_use(self) -> AreaOfUse | None:
        """The first of :attr:`areas_of_use`, the one PROJ reports, or None."""
        return self.areas_of_use[0] if self.areas_of_use else None

    def _summary(self) -> dict[str, Any]:
        return area_json(self.areas_of_use)


def authority_id(authority_code: str) -> dict[str, Any]:
    """``"AUTH:CODE"`` as a PROJJSON ``id``."""
    auth_name, _, code = authority_code.partition(":")
    return projjson_id(auth_name, code)


def columns(cls: type[RegistryEntry]) -> tuple[str, ...]:
    """The DataFrame columns of an entry class, in the order rows fill them."""
    expanded: list[str] = []
    for column in cls._COLUMNS:
        if column == "area_of_use":
            expanded += ["area_of_use", "west", "south", "east", "north"]
        else:
            expanded.append(column)
    return ("authority_code", "auth_name", "code", "name", *expanded, "deprecated")


def table_row(entry: object, columns: tuple[str, ...]) -> dict[str, Any]:
    """Flatten an entry's attributes into DataFrame cells."""
    row: dict[str, Any] = {}
    for column in columns:
        value = getattr(entry, column)
        if isinstance(value, AreaOfUse):
            row["area_of_use"] = value.name
            row.update(
                west=value.west, south=value.south, east=value.east, north=value.north
            )
            continue
        if column == "area_of_use":
            row.update(area_of_use=None, west=None, south=None, east=None, north=None)
            continue
        row[column] = _cell(value)
    return row


def _cell(value: object) -> object:
    if isinstance(value, ObjectRef):
        return str(value)
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, tuple):
        return tuple(_cell(item) for item in value)
    return value
