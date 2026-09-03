"""proj.db record types that do not depend on where the definitions came from.

An object's identity, its scope and its extent mean the same thing whether the
definition was fetched from a Georepository instance or read out of an OSDU
catalogue, so they are modelled once here. The field extraction that turns one
particular source's JSON into these types lives with that source.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from geodetic_engine.projdb.schema import OBJECT_TABLE_NAME


@dataclass(frozen=True, slots=True)
class ObjectKey:
    """Identity of an object in proj.db."""

    table: str
    auth_name: str
    code: str

    @property
    def object_table_name(self) -> str:
        """The name PROJ uses for this table in usage/alias/supersession."""
        return OBJECT_TABLE_NAME[self.table]


@dataclass(frozen=True, slots=True)
class Scope:
    """What an object may be used for, as proj.db's ``scope`` table records it."""

    auth_name: str
    code: str
    scope: str
    deprecated: int = 0


@dataclass(frozen=True, slots=True)
class Extent:
    """Where an object may be used, as proj.db's ``extent`` table records it.

    The bounding box is in degrees on the object's own geographic base, and a
    box that crosses the antimeridian has ``west_lon`` greater than
    ``east_lon``. The values are stored exactly as the source states them; no
    normalisation is applied, because a normalised box is a different area.
    """

    auth_name: str
    code: str
    name: str
    description: str | None = None
    south_lat: float | None = None
    north_lat: float | None = None
    west_lon: float | None = None
    east_lon: float | None = None
    deprecated: int = 0


@dataclass(slots=True)
class UsageAccumulator:
    """Builds ``usage`` rows and the ``scope``/``extent`` rows they reference.

    proj.db's usage table has a ``(auth_name, code)`` primary key that permits
    nulls, but writing explicit codes keeps every usage row traceable back to
    the object that produced it.

    Attributes:
        authority: Authority the generated usage rows are written under. Scope
            and extent rows keep the authority that defined them, which is
            routinely another one.
    """

    authority: str
    usages: list[dict[str, Any]] = field(default_factory=list)
    scopes: dict[tuple[str, str], dict[str, Any]] = field(default_factory=dict)
    extents: dict[tuple[str, str], dict[str, Any]] = field(default_factory=dict)
    _counter: int = 0

    def add(
        self, key: ObjectKey, *, scope: Scope | None, extent: Extent | None
    ) -> None:
        """Record one usage of an object, along with its scope and extent.

        A usage needs both halves to mean anything, so one without a resolvable
        scope or extent is dropped rather than written with a null reference.

        Args:
            key: The object being used.
            scope: What the object may be used for.
            extent: Where the object may be used.
        """
        if scope is None or extent is None:
            return

        scope_key = (scope.auth_name, scope.code)
        extent_key = (extent.auth_name, extent.code)
        if scope_key not in self.scopes:
            self.scopes[scope_key] = {
                "auth_name": scope.auth_name,
                "code": scope.code,
                "scope": scope.scope,
                "deprecated": scope.deprecated,
            }
        if extent_key not in self.extents:
            self.extents[extent_key] = {
                "auth_name": extent.auth_name,
                "code": extent.code,
                "name": extent.name,
                "description": extent.description,
                "south_lat": extent.south_lat,
                "north_lat": extent.north_lat,
                "west_lon": extent.west_lon,
                "east_lon": extent.east_lon,
                "deprecated": extent.deprecated,
            }

        self._counter += 1
        self.usages.append(
            {
                "auth_name": self.authority,
                "code": f"{key.object_table_name}_{key.code}_{self._counter}",
                "object_table_name": key.object_table_name,
                "object_auth_name": key.auth_name,
                "object_code": key.code,
                "extent_auth_name": extent.auth_name,
                "extent_code": extent.code,
                "scope_auth_name": scope.auth_name,
                "scope_code": scope.code,
            }
        )

    def foreign_scope_extent_keys(self) -> set[tuple[str, str]]:
        """Scope and extent keys that belong to another authority."""
        return {
            key
            for key in (*self.scopes, *self.extents)
            if key[0].casefold() != self.authority.casefold()
        }
