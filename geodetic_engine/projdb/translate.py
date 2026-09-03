"""Shared translation from Georepository JSON into proj.db rows.

Georepository objects share a common envelope (``Code``, ``Name``,
``DataSource``, ``Deprecations``, ``Usage``, ``Links``), so the field access and
the deprecation, usage, alias and supersession handling that every concept needs
lives here. Concept-specific shapes live in the concept modules.

The proj.db row types these produce are source-neutral and live in
:mod:`geodetic_engine.projdb.records`.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

from geodetic_engine.projdb.records import Extent, ObjectKey, Scope

JsonObject = dict[str, Any]


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


def scope_of(obj: JsonObject) -> Scope | None:
    """Read a resolved Georepository ``Scope`` object.

    Returns:
        The scope, or None when the object carries no authority and code and so
        cannot be referenced from a usage row.
    """
    key = _key_of(obj)
    if key is None:
        return None
    return Scope(
        auth_name=key[0],
        code=key[1],
        scope=text(obj, "ScopeDetails", "Name", "Remark") or "not known",
        deprecated=deprecated_flag(obj),
    )


def extent_of(obj: JsonObject) -> Extent | None:
    """Read a resolved Georepository ``Extent`` object.

    The bounding box is carried through in the degrees the register states,
    including a box that crosses the antimeridian.

    Returns:
        The extent, or None when the object carries no authority and code.
    """
    key = _key_of(obj)
    if key is None:
        return None
    return Extent(
        auth_name=key[0],
        code=key[1],
        name=text(obj, "Name") or "unknown",
        description=text(obj, "Description", "Remark"),
        south_lat=number(obj, "BoundingBoxSouthBoundLatitude"),
        north_lat=number(obj, "BoundingBoxNorthBoundLatitude"),
        west_lon=number(obj, "BoundingBoxWestBoundLongitude"),
        east_lon=number(obj, "BoundingBoxEastBoundLongitude"),
        deprecated=deprecated_flag(obj),
    )


def _key_of(obj: JsonObject) -> tuple[str, str] | None:
    obj_auth = auth_name(obj)
    obj_code = code(obj)
    if not obj_auth or obj_code is None:
        return None
    return obj_auth, obj_code


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
