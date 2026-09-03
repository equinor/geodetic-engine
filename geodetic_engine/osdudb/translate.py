"""Reading fields out of OSDU reference data records.

OSDU records share an envelope: a ``CodeSpace`` and ``Code`` that together
identify the object, a ``Name``, an ``InactiveIndicator``, a list of ``Usages``,
and cross references shaped as ``{"AuthorityCode": {"Authority", "Code"}}``.
That envelope is read here. What each kind of record says about geodesy is
stated only in its WKT, and is taken apart in
:mod:`geodetic_engine.osdudb.definition`.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

from geodetic_engine.projdb.records import Extent, Scope

JsonObject = dict[str, Any]

# Keys the WKT2 definition has been published under. OSDU 1.x uses
# ``OGCWellKnownText2``; some exports carry ``Wkt2Ogc`` instead, and a record
# with neither is not a definition at all.
WKT_KEYS: tuple[str, ...] = ("OGCWellKnownText2", "Wkt2Ogc")

# ``AliasNameTypeID`` looks like
# ``namespace:reference-data--AliasNameType:ESRI:``; the naming system is the
# segment after the entity type.
_ALIAS_TYPE_MARKER = "AliasNameType:"


def auth_name(obj: JsonObject) -> str:
    """Return the code space that owns an object, which is its proj.db authority."""
    return str(obj.get("CodeSpace") or "").strip()


def code(obj: JsonObject) -> str | None:
    """Return an object's code as a string, or None when absent.

    ``Code`` is preferred over ``CodeAsNumber`` because it is the authoritative
    spelling, and proj.db stores codes as text.
    """
    for key in ("Code", "CodeAsNumber"):
        raw = obj.get(key)
        if raw not in (None, ""):
            return str(raw)
    return None


def authority_code(link: JsonObject | None) -> tuple[str | None, str | None]:
    """Return the ``(authority, code)`` a cross reference points at.

    Args:
        link: An object carrying an ``AuthorityCode``, such as ``SourceCRS`` or
            ``Datum``. A missing or empty link yields ``(None, None)``.

    Example:
        >>> authority_code({"AuthorityCode": {"Authority": "EPSG", "Code": 4326}})
        ('EPSG', '4326')
        >>> authority_code(None)
        (None, None)
    """
    pair = (link or {}).get("AuthorityCode") or {}
    authority = str(pair.get("Authority") or "").strip() or None
    raw = pair.get("Code")
    return authority, None if raw in (None, "") else str(raw)


def text(obj: JsonObject, *keys: str) -> str | None:
    """Return the first non-empty string among the given keys."""
    for key in keys:
        value = obj.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def number(obj: JsonObject, key: str) -> float | None:
    """Return a numeric field as a float, or None when absent or unparseable.

    Values are kept in the units the catalogue reports them in; unit conversion
    is the responsibility of the caller that knows the unit of measure.
    """
    value = obj.get(key)
    if value is None or isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def wkt(obj: JsonObject) -> str | None:
    """Return the WKT2 definition an OSDU record carries, if it carries one."""
    return text(obj, *WKT_KEYS)


def is_deprecated(obj: JsonObject) -> bool:
    """Whether a record is flagged inactive.

    OSDU has one flag and no replacement reference, so an inactive record is
    recorded as deprecated but cannot be linked to whatever superseded it.
    """
    return bool(obj.get("InactiveIndicator"))


def deprecated_flag(obj: JsonObject) -> int:
    """Return proj.db's 0/1 deprecated flag for a record."""
    return 1 if is_deprecated(obj) else 0


def usages(obj: JsonObject) -> list[JsonObject]:
    """Return a record's usages, falling back to its preferred usage.

    A record always states ``PreferredUsage``; ``Usages`` repeats it along with
    any others. Reading the list and falling back keeps a record that carries
    only the preferred one from losing its extent.
    """
    listed = obj.get("Usages")
    if isinstance(listed, list) and listed:
        return [usage for usage in listed if isinstance(usage, dict)]
    preferred = obj.get("PreferredUsage")
    return [preferred] if isinstance(preferred, dict) and preferred else []


def scope_of(
    usage: JsonObject, *, derived: tuple[str, str] | None = None
) -> Scope | None:
    """Read the scope of one usage.

    Args:
        usage: One entry of a record's ``Usages``.
        derived: Authority and code to record the scope under when the
            catalogue states one without an ``AuthorityCode``. See
            :func:`extent_of`.

    Returns:
        The scope, or None when it can neither be identified nor derived.
    """
    scope = usage.get("Scope") or {}
    authority, scope_code = authority_code(scope)
    if not authority or scope_code is None:
        if derived is None or not text(scope, "Name", "Description"):
            return None
        authority, scope_code = derived
    return Scope(
        auth_name=authority,
        code=scope_code,
        scope=text(scope, "Name", "Description") or "not known",
    )


def extent_of(
    usage: JsonObject, *, derived: tuple[str, str] | None = None
) -> Extent | None:
    """Read the extent of one usage.

    The bounding box is carried through in the degrees the catalogue states,
    including a box that crosses the antimeridian.

    OSDU states a bound CRS's extent as the intersection of the extents of the
    CRS and the transformation it binds together, which is narrower than either
    and which no authority has given a code. Such an extent is recorded under
    ``derived`` rather than dropped: it is the area the bound CRS is actually
    valid within, and losing it would leave the CRS looking usable everywhere
    its base CRS is.

    Args:
        usage: One entry of a record's ``Usages``.
        derived: Authority and code to record the extent under when the
            catalogue states one without an ``AuthorityCode``.

    Returns:
        The extent, or None when it can neither be identified nor derived.
    """
    extent = usage.get("Extent") or {}
    bounds = {
        edge: number(extent, f"BoundingBox{edge}")
        for edge in (
            "SouthBoundLatitude",
            "NorthBoundLatitude",
            "WestBoundLongitude",
            "EastBoundLongitude",
        )
    }
    authority, extent_code = authority_code(extent)
    if not authority or extent_code is None:
        if derived is None or any(value is None for value in bounds.values()):
            return None
        authority, extent_code = derived
    return Extent(
        auth_name=authority,
        code=extent_code,
        name=text(extent, "Name") or "unknown",
        description=text(extent, "Description"),
        south_lat=bounds["SouthBoundLatitude"],
        north_lat=bounds["NorthBoundLatitude"],
        west_lon=bounds["WestBoundLongitude"],
        east_lon=bounds["EastBoundLongitude"],
    )


def aliases(obj: JsonObject) -> Iterator[tuple[str, str]]:
    """Yield the ``(alias, naming system)`` pairs a record carries.

    An alias equal to the record's own name carries no information and is not
    yielded.

    Example:
        >>> record = {
        ...     "Name": "ED50",
        ...     "NameAlias": [
        ...         {
        ...             "AliasName": "European Datum 1950",
        ...             "AliasNameTypeID": "ns:reference-data--AliasNameType:ESRI:",
        ...         }
        ...     ],
        ... }
        >>> list(aliases(record))
        [('European Datum 1950', 'ESRI')]
    """
    name = text(obj, "Name")
    for record in obj.get("NameAlias") or []:
        if not isinstance(record, dict):
            continue
        alias = text(record, "AliasName")
        if not alias or alias == name:
            continue
        yield alias, naming_system(text(record, "AliasNameTypeID"))


def naming_system(alias_type_id: str | None) -> str:
    """Return the naming system an ``AliasNameTypeID`` names.

    Example:
        >>> naming_system("ns:reference-data--AliasNameType:EPSGname:")
        'EPSGname'
        >>> naming_system(None)
        ''
    """
    if not alias_type_id or _ALIAS_TYPE_MARKER not in alias_type_id:
        return ""
    return alias_type_id.split(_ALIAS_TYPE_MARKER, 1)[1].strip(": ").strip()
