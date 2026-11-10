"""The tables of the active proj.db that pyproj does not hand back.

pyproj answers what it can, and its answers are used wherever they exist:
CRS rows and their areas, units, authorities, and every full definition. What
it does not return -- aliases, replacements, the extent of each usage, the
datum a CRS is on, the axes of a coordinate system -- is read here, from the
first ``proj.db`` on PROJ's search path, the database PROJ itself resolves
codes against. Each table is read once per database identity and kept, so a
switch of database is seen on the next call and costs nothing until then.
"""

from __future__ import annotations

import logging
import sqlite3
from collections.abc import Iterator, Mapping
from contextlib import closing, contextmanager
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from pyproj.database import get_authorities

from geodetic_engine._referencing.database import (
    DatabaseIdentity,
    database_identity,
    identifiers_by_object,
    stored_reference_rows,
)
from geodetic_engine._referencing.osdu_id import SCHEME as OSDU_SCHEME
from geodetic_engine._sqlite import read_only
from geodetic_engine.catalog.errors import CatalogError
from geodetic_engine.geodesy import AreaOfUse

logger = logging.getLogger(__name__)

# (auth_name, code), the authority spelled as the database spells it.
type Key = tuple[str, str]
# (table_name, auth_name, code) in the vocabulary of PROJ's object_view.
type TableKey = tuple[str, str, str]

CRS_TABLES = frozenset(
    {
        "geodetic_crs",
        "projected_crs",
        "vertical_crs",
        "compound_crs",
        "engineering_crs",
        "derived_projected_crs",
    }
)
DATUM_TABLES = frozenset({"geodetic_datum", "vertical_datum", "engineering_datum"})
TRANSFORMATION_TABLES = (
    "helmert_transformation",
    "grid_transformation",
    "other_transformation",
)
OPERATION_TABLES = frozenset(
    {"conversion", "concatenated_operation", *TRANSFORMATION_TABLES}
)


def identity() -> DatabaseIdentity:
    """The databases PROJ reads now, which every cache here is keyed by."""
    return database_identity()


def unchanged(databases: DatabaseIdentity) -> None:
    """Refuse to read a listing after the database it was made for was switched.

    A listing's filters were checked against the database active when it was
    made; reading it against another would apply them to the wrong objects.
    """
    if database_identity() != databases:
        raise CatalogError(
            "PROJ's database changed after this listing was made; "
            "call the listing function again"
        )


def database_path(databases: DatabaseIdentity) -> Path:
    """The proj.db PROJ resolves codes against: the first on its search path."""
    for path, *_ in databases:
        candidate = Path(path)
        if candidate.is_file():
            return candidate
    raise CatalogError("there is no proj.db on PROJ's search path to list from")


@contextmanager
def reading(databases: DatabaseIdentity) -> Iterator[sqlite3.Connection]:
    """A read-only connection to the database PROJ resolves codes against."""
    with closing(read_only(database_path(databases))) as connection:
        yield connection


def has_table(connection: sqlite3.Connection, table: str) -> bool:
    """Whether the database has a table or view of this name."""
    return bool(
        connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type IN ('table', 'view') AND name = ?",
            (table,),
        ).fetchone()
    )


def text(value: object) -> str | None:
    """A nullable column as text."""
    return None if value is None else str(value)


def number(value: object) -> float | None:
    """A nullable column as a float."""
    return None if value is None else float(value)  # type: ignore[arg-type]


@lru_cache(maxsize=4)
def authorities(databases: DatabaseIdentity) -> tuple[str, ...]:
    """The authorities PROJ knows in this database, as it spells them."""
    del databases
    return tuple(get_authorities())


@lru_cache(maxsize=4)
def names(databases: DatabaseIdentity) -> Mapping[TableKey, str]:
    """The name of every object, through PROJ's ``object_view``."""
    with reading(databases) as connection:
        rows = connection.execute(
            "SELECT table_name, auth_name, code, name FROM object_view"
        ).fetchall()
    return {(str(t), str(a), str(c)): str(n) for t, a, c, n in rows}


@lru_cache(maxsize=4)
def aliases(databases: DatabaseIdentity) -> Mapping[TableKey, tuple[str, ...]]:
    """Every alternative name the database records, by object."""
    found: dict[TableKey, list[str]] = {}
    with reading(databases) as connection:
        rows = connection.execute(
            "SELECT table_name, auth_name, code, alt_name FROM alias_name "
            "ORDER BY rowid"
        )
        for table, auth, code, alias in rows:
            names_ = found.setdefault((str(table), str(auth), str(code)), [])
            if alias not in names_:
                names_.append(str(alias))
    return {key: tuple(value) for key, value in found.items()}


@lru_cache(maxsize=4)
def replacements(databases: DatabaseIdentity) -> Mapping[TableKey, tuple[str, ...]]:
    """``"AUTH:CODE"`` of what replaces each deprecated object."""
    return _links(
        databases,
        "SELECT table_name, deprecated_auth_name, deprecated_code, "
        "replacement_auth_name, replacement_code FROM deprecation ORDER BY rowid",
    )


@lru_cache(maxsize=4)
def supersessions(databases: DatabaseIdentity) -> Mapping[TableKey, tuple[str, ...]]:
    """``"AUTH:CODE"`` of what supersedes each superseded operation."""
    return _links(
        databases,
        "SELECT superseded_table_name, superseded_auth_name, superseded_code, "
        "replacement_auth_name, replacement_code FROM supersession ORDER BY rowid",
    )


def _links(
    databases: DatabaseIdentity, query: str
) -> Mapping[TableKey, tuple[str, ...]]:
    found: dict[TableKey, list[str]] = {}
    with reading(databases) as connection:
        for table, auth, code, to_auth, to_code in connection.execute(query):
            targets = found.setdefault((str(table), str(auth), str(code)), [])
            target = f"{to_auth}:{to_code}"
            if target not in targets:
                targets.append(target)
    return {key: tuple(value) for key, value in found.items()}


@lru_cache(maxsize=4)
def usages(databases: DatabaseIdentity) -> Mapping[TableKey, tuple[AreaOfUse, ...]]:
    """The area of every usage of every object, in the database's order.

    Named by the extent's description, as PROJ names an area of use.
    """
    found: dict[TableKey, list[AreaOfUse]] = {}
    with reading(databases) as connection:
        rows = connection.execute(
            "SELECT u.object_table_name, u.object_auth_name, u.object_code, "
            "e.description, e.west_lon, e.south_lat, e.east_lon, e.north_lat "
            "FROM usage u JOIN extent e "
            "ON e.auth_name = u.extent_auth_name AND e.code = u.extent_code "
            "ORDER BY u.rowid"
        )
        for table, auth, code, description, west, south, east, north in rows:
            if None in (west, south, east, north):
                continue
            area = AreaOfUse(
                west=float(west),
                south=float(south),
                east=float(east),
                north=float(north),
                name=str(description or ""),
            )
            areas = found.setdefault((str(table), str(auth), str(code)), [])
            if area not in areas:
                areas.append(area)
    return {key: tuple(value) for key, value in found.items()}


@dataclass(frozen=True, slots=True)
class UnitRow:
    """One row of ``unit_of_measure``."""

    name: str
    type: str
    conversion_factor: float | None
    deprecated: bool


@lru_cache(maxsize=4)
def unit_rows(databases: DatabaseIdentity) -> Mapping[Key, UnitRow]:
    """Every unit of measure, by authority code."""
    with reading(databases) as connection:
        rows = connection.execute(
            "SELECT auth_name, code, name, type, conv_factor, deprecated "
            "FROM unit_of_measure"
        ).fetchall()
    return {
        (str(auth), str(code)): UnitRow(
            name=str(name),
            type=str(kind),
            conversion_factor=number(factor),
            deprecated=bool(deprecated),
        )
        for auth, code, name, kind, factor, deprecated in rows
    }


@dataclass(frozen=True, slots=True)
class AxisRow:
    """One axis of a coordinate system, as ``axis`` states it."""

    name: str
    abbreviation: str
    orientation: str
    unit: Key | None


@dataclass(frozen=True, slots=True)
class CoordinateSystemRow:
    """One row of ``coordinate_system``, with its axes in order."""

    kind: str
    dimension: int
    axes: tuple[AxisRow, ...]


@lru_cache(maxsize=4)
def coordinate_system_rows(
    databases: DatabaseIdentity,
) -> Mapping[Key, CoordinateSystemRow]:
    """Every coordinate system, by authority code."""
    axes: dict[Key, list[AxisRow]] = {}
    with reading(databases) as connection:
        for (
            name,
            abbreviation,
            orientation,
            auth,
            code,
            uom_auth,
            uom_code,
        ) in connection.execute(
            "SELECT name, abbrev, orientation, coordinate_system_auth_name, "
            "coordinate_system_code, uom_auth_name, uom_code FROM axis "
            "ORDER BY coordinate_system_auth_name, coordinate_system_code, "
            "coordinate_system_order"
        ):
            unit = None if uom_code is None else (str(uom_auth), str(uom_code))
            axes.setdefault((str(auth), str(code)), []).append(
                AxisRow(
                    name=str(name),
                    abbreviation=str(abbreviation or ""),
                    orientation=str(orientation),
                    unit=unit,
                )
            )
        rows = connection.execute(
            "SELECT auth_name, code, type, dimension FROM coordinate_system"
        ).fetchall()
    return {
        (str(auth), str(code)): CoordinateSystemRow(
            kind=str(kind),
            dimension=int(dimension),
            axes=tuple(axes.get((str(auth), str(code)), ())),
        )
        for auth, code, kind, dimension in rows
    }


def coordinate_system_name(
    row: CoordinateSystemRow, units: Mapping[Key, UnitRow]
) -> str:
    """A name for a coordinate system, in the pattern EPSG names them.

    proj.db keeps no name for a coordinate system, so one is composed from
    what it does keep: type, dimension, axes, orientations and units.
    """
    unit_names: list[str] = []
    for axis in row.axes:
        found = units.get(axis.unit) if axis.unit else None
        if found is not None and found.name not in unit_names:
            unit_names.append(found.name)
    return (
        f"{row.kind} {row.dimension}D CS. "
        f"Axes: {', '.join(axis.name.lower() for axis in row.axes)} "
        f"({','.join(axis.abbreviation for axis in row.axes)}). "
        f"Orientations: {','.join(axis.orientation for axis in row.axes)}. "
        f"UoM: {', '.join(unit_names)}."
    )


@lru_cache(maxsize=4)
def osdu_identifiers(
    databases: DatabaseIdentity,
) -> Mapping[TableKey, tuple[str, ...]]:
    """The OSDU record ids a build of this package recorded, by object."""
    del databases
    return identifiers_by_object(OSDU_SCHEME)


@lru_cache(maxsize=4)
def with_persistable_reference(databases: DatabaseIdentity) -> frozenset[TableKey]:
    """The objects a build recorded a catalogue persistableReference for."""
    del databases
    return frozenset(
        (row.table_name, row.auth_name, row.code)
        for row in stored_reference_rows(OSDU_SCHEME)
    )
