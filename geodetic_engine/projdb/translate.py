"""Shared translation from Georepository JSON into proj.db rows.

Georepository objects share a common envelope (``Code``, ``Name``,
``DataSource``, ``Deprecations``, ``Usage``, ``Links``), so the field access and
the deprecation, usage, alias and supersession handling that every concept needs
lives here. Concept-specific shapes live in the concept modules.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import Any

from geodetic_engine.projdb.schema import OBJECT_TABLE_NAME

JsonObject = dict[str, Any]


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


def auth_name(obj: JsonObject) -> str:
    """Return the authority that owns an object."""
    return str(obj.get("DataSource") or "")


def code(obj: JsonObject) -> str | None:
    """Return an object's code as a string, or None when absent."""
    raw = obj.get("Code")
    return None if raw is None else str(raw)


def link_code(link: JsonObject | None) -> str | None:
    """Return the code carried by a ``ChildLink``, without fetching it."""
    if not link:
        return None
    raw = link.get("Code")
    return None if raw is None else str(raw)


def href(link: JsonObject | None) -> str | None:
    """Return the URL of a ``ChildLink``, if any."""
    if not link:
        return None
    value = link.get("href")
    return str(value) if value else None


def text(obj: JsonObject, *keys: str) -> str | None:
    """Return the first non-empty string among the given keys.

    Georepository is inconsistent about ``Remark`` versus ``Remarks`` versus
    ``Description`` depending on the object type.
    """
    for key in keys:
        value = obj.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def is_deprecated(obj: JsonObject) -> bool:
    """Whether an object carries any deprecation record."""
    return bool(obj.get("Deprecations"))


def deprecated_flag(obj: JsonObject) -> int:
    """Return proj.db's 0/1 deprecated flag for an object."""
    return 1 if is_deprecated(obj) else 0


def number(obj: JsonObject, key: str) -> float | None:
    """Return a numeric field as a float, or None when absent or unparseable.

    Values are kept in the units the API reports them in; unit conversion is the
    responsibility of the caller that knows the associated unit of measure.
    """
    value = obj.get(key)
    if value is None or isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def epoch(obj: JsonObject, key: str) -> str | None:
    """Return a coordinate or reference epoch as recorded, without rounding.

    Epochs are stored verbatim because a dynamic datum's reference epoch is part
    of the datum's identity; reformatting it risks losing precision.
    """
    value = obj.get(key)
    if value in (None, ""):
        return None
    return str(value)


@dataclass(slots=True)
class UsageAccumulator:
    """Builds ``usage`` rows and the ``scope``/``extent`` rows they reference.

    proj.db's usage table has a ``(auth_name, code)`` primary key that permits
    nulls, but writing explicit codes keeps every usage row traceable back to
    the object that produced it.
    """

    authority: str
    usages: list[dict[str, Any]] = field(default_factory=list)
    scopes: dict[tuple[str, str], dict[str, Any]] = field(default_factory=dict)
    extents: dict[tuple[str, str], dict[str, Any]] = field(default_factory=dict)
    _counter: int = 0

    def add(
        self,
        key: ObjectKey,
        *,
        scope_obj: JsonObject,
        extent_obj: JsonObject,
    ) -> None:
        """Record one usage of an object, along with its scope and extent.

        Args:
            key: The object being used.
            scope_obj: Resolved Georepository ``Scope`` object.
            extent_obj: Resolved Georepository ``Extent`` object.
        """
        scope_key = _key_of(scope_obj)
        extent_key = _key_of(extent_obj)
        if scope_key is None or extent_key is None:
            return

        if scope_key not in self.scopes:
            self.scopes[scope_key] = {
                "auth_name": scope_key[0],
                "code": scope_key[1],
                "scope": text(scope_obj, "ScopeDetails", "Name", "Remark")
                or "not known",
                "deprecated": deprecated_flag(scope_obj),
            }
        if extent_key not in self.extents:
            self.extents[extent_key] = _extent_row(extent_obj, extent_key)

        self._counter += 1
        self.usages.append(
            {
                "auth_name": self.authority,
                "code": f"{key.object_table_name}_{key.code}_{self._counter}",
                "object_table_name": key.object_table_name,
                "object_auth_name": key.auth_name,
                "object_code": key.code,
                "extent_auth_name": extent_key[0],
                "extent_code": extent_key[1],
                "scope_auth_name": scope_key[0],
                "scope_code": scope_key[1],
            }
        )

    def foreign_scope_extent_keys(self) -> set[tuple[str, str]]:
        """Scope and extent keys that belong to another authority."""
        return {
            key
            for key in (*self.scopes, *self.extents)
            if key[0].casefold() != self.authority.casefold()
        }


def _key_of(obj: JsonObject) -> tuple[str, str] | None:
    obj_auth = auth_name(obj)
    obj_code = code(obj)
    if not obj_auth or obj_code is None:
        return None
    return obj_auth, obj_code


def _extent_row(obj: JsonObject, key: tuple[str, str]) -> dict[str, Any]:
    return {
        "auth_name": key[0],
        "code": key[1],
        "name": text(obj, "Name") or "unknown",
        "description": text(obj, "Description", "Remark"),
        "south_lat": number(obj, "BoundingBoxSouthBoundLatitude"),
        "north_lat": number(obj, "BoundingBoxNorthBoundLatitude"),
        "west_lon": number(obj, "BoundingBoxWestBoundLongitude"),
        "east_lon": number(obj, "BoundingBoxEastBoundLongitude"),
        "deprecated": deprecated_flag(obj),
    }


def supersession_candidates(
    key: ObjectKey, obj: JsonObject
) -> Iterator[tuple[ObjectKey, str]]:
    """Yield the replacement codes recorded against a superseded object.

    Only the code is returned. Which authority and table own that code is not
    stated by the register and must be resolved against the database, because a
    custom object is routinely superseded by an EPSG one.

    Args:
        key: The superseded object.
        obj: The raw Georepository object.

    Yields:
        ``(superseded_key, replacement_code)`` pairs, without duplicates.
    """
    seen: set[str] = set()
    replacements = [
        (item.get("ReplacedBy") or {}) for item in obj.get("Deprecations") or []
    ]
    replacements += [
        (item.get("SupersededBy") or {}) for item in obj.get("Supersessions") or []
    ]
    for link in replacements:
        replacement_code = link_code(link)
        if replacement_code is None or replacement_code in seen:
            continue
        seen.add(replacement_code)
        yield key, replacement_code
