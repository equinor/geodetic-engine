"""The persistableReference strings an OSDU catalogue publishes, read from proj.db.

A proj.db built by :mod:`geodetic_engine.osdudb` records, against the id of
every catalogue record, the ``PersistableReference`` that record publishes,
exactly as published. That includes the EPSG records the base proj.db already
defines, which are most of what OSDU data names. This module reads them back:
for one object, as a listing, or as an export file.

A stored reference is the catalogue's own string, so it is what to write into
an OSDU record that names the object. It is not generated:
:func:`~geodetic_engine.persistablereference.to_persistable_reference` builds a
payload from PROJ's definition instead, which describes the same CRS but is not
byte-for-byte the string the catalogue publishes, and is never substituted for
a reference the catalogue does not state.

An object is named the way the rest of the package names one:

* by the OSDU id of the record that publishes it, with or without its data
  partition or a version separator,
* by ``"AUTH:CODE"``, which finds the reference of every record naming that
  object,
* or by a :class:`~geodetic_engine.geodesy.CoordinateReferenceSystem` or
  :class:`pyproj.CRS`, by the id or code it was resolved from.

When there is no reference, lookups return None and
:func:`missing_reference_reason` says why: the database records none at all,
no catalogue record names the object, or the record names it but states none,
or states one the build could not use.

Lookups read the first proj.db on PROJ's data path, the same one PROJ resolves
codes against, unless a ``database`` is given.

Example:
    >>> from geodetic_engine.persistablereference import persistable_reference_from_catalog
    >>> reference = persistable_reference_from_catalog("EPSG:23031")  # doctest: +SKIP
    >>> reference.kind, reference.identifier  # doctest: +SKIP
    (<Kind.LATE_BOUND_CRS: 'LBC'>, 'reference-data--CoordinateReferenceSystem:Projected:EPSG::23031')
    >>> print(reference.pretty())  # doctest: +SKIP
    {
      "authCode": {
        "auth": "EPSG",
        "code": "23031"
      },
      ...
"""  # noqa: E501

from __future__ import annotations

import csv
import json
import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import IO, TYPE_CHECKING, Any, Literal

from pyproj import CRS

from geodetic_engine.geodesy.database import (
    CRS_OBJECT_TABLES,
    OPERATION_OBJECT_TABLES,
    StoredReferenceRow,
    database_file,
    persistable_references_present,
    recorded_identifiers,
    stored_reference_rows,
    unrecorded_reference_reasons,
)
from geodetic_engine.geodesy.osdu_id import (
    CRS_ENTITY,
    SCHEME,
    TRANSFORMATION_ENTITY,
    OsduId,
    parse_osdu_id,
    unresolved_reason,
)
from geodetic_engine.persistablereference import esriwkt
from geodetic_engine.persistablereference.envelope import (
    AuthorityCode,
    Kind,
    json_object,
)
from geodetic_engine.persistablereference.errors import (
    AmbiguousCatalogReferenceError,
    MalformedReferenceError,
)
from geodetic_engine.persistablereference.reference import (
    AnyReference,
    parse_persistable_reference,
)

if TYPE_CHECKING:
    from geodetic_engine.geodesy.crs import CoordinateReferenceSystem

__all__ = [
    "CatalogPersistableReference",
    "Entity",
    "ExportFormat",
    "export_persistable_references_from_catalog",
    "missing_reference_reason",
    "persistable_reference_from_catalog",
    "persistable_references_from_catalog",
    "pretty_persistable_reference",
]

type Entity = Literal["crs", "operation"]
"""Which kind of object to look in: a CRS or a coordinate operation."""

type ExportFormat = Literal["json", "jsonl", "csv"]
"""The file formats :func:`export_persistable_references_from_catalog` writes."""

_ENTITY_TABLES: dict[str, frozenset[str]] = {
    "crs": CRS_OBJECT_TABLES,
    "operation": OPERATION_OBJECT_TABLES,
}
_ENTITY_OF_ID = {CRS_ENTITY: "crs", TRANSFORMATION_ENTITY: "operation"}

# An authority code as a caller writes one: an authority, one colon, a code. A
# code may hold brackets and slashes, as PROJ's own operation codes do
# (``ETRS89_TO_ETRS89-DEU[ETRS89/DREF91/2016]``), but no colon, which is what
# keeps a bare OSDU ``data.ID`` such as ``Projected:EPSG::23031`` apart.
_AUTHORITY_CODE = re.compile(
    r"\s*(?P<auth>[A-Za-z][\w.\-]*)\s*:\s*(?P<code>[^\s:]+)\s*"
)

# Columns of a CSV export and of each JSON entry, in order.
_FIELDS = (
    "identifier",
    "table_name",
    "auth_name",
    "code",
    "deprecated",
    "kind",
    "name",
    "stated_authority",
    "version",
    "sha256",
    "source",
    "persistable_reference",
)


@dataclass(frozen=True, slots=True)
class CatalogPersistableReference:
    """A persistableReference a catalogue publishes, as proj.db recorded it.

    Attributes:
        identifier: The OSDU id of the record that publishes it, without the
            data partition, for example
            ``reference-data--CoordinateReferenceSystem:Projected:EPSG::23031``.
        table_name: The table of the object the id names, in the vocabulary of
            PROJ's ``object_view``, for example ``"projected_crs"``.
        auth_name: The object's authority, for example ``"EPSG"``.
        code: The object's code.
        deprecated: Whether the catalogue marks the record inactive.
        kind: The kind of definition the reference states.
        name: The name the reference states, which is ESRI's or the
            catalogue's, not necessarily the object's name in proj.db.
        stated_authority: The ``authCode`` the reference states. Provenance:
            it can disagree with ``auth_name`` and ``code``, and the build
            reports where it does.
        version: The producer version the reference states, such as
            ``"PE_10_9_1"``.
        sha256: Hex SHA-256 of :attr:`payload`, encoded as UTF-8.
        source: The catalogue the build read it from.
        payload: The reference, exactly as the catalogue published it.
    """

    identifier: str
    table_name: str
    auth_name: str
    code: str
    deprecated: bool
    kind: Kind
    name: str | None
    stated_authority: AuthorityCode | None
    version: str | None
    sha256: str
    source: str | None
    payload: str = field(repr=False)

    @property
    def authority_code(self) -> str:
        """The object's ``"AUTH:CODE"``, as PROJ resolves it."""
        return f"{self.auth_name}:{self.code}"

    @property
    def is_crs(self) -> bool:
        """Whether the object is a coordinate reference system."""
        return self.table_name in CRS_OBJECT_TABLES

    @property
    def is_operation(self) -> bool:
        """Whether the object is a coordinate operation."""
        return self.table_name in OPERATION_OBJECT_TABLES

    def pretty(self, *, indent: int = 2, wkt: bool = False) -> str:
        """The reference laid out for reading.

        Args:
            indent: Spaces per level of nesting.
            wkt: Also lay the embedded ESRI WKT out over indented lines. The
                result is then for reading only and is not JSON.

        Returns:
            See :func:`pretty_persistable_reference`.
        """
        return pretty_persistable_reference(self.payload, indent=indent, wkt=wkt)

    def parse(self) -> AnyReference:
        """Read the reference, to build the CRS or operation it states.

        Returns:
            The reference, as
            :func:`~geodetic_engine.persistablereference.parse_persistable_reference`
            reads it.
        """
        return parse_persistable_reference(self.payload)

    def as_dict(self, *, pretty: bool = False) -> dict[str, Any]:
        """The reference and what is recorded about it, as an export entry.

        Args:
            pretty: Give the reference as its decoded JSON object rather than
                the string, so a JSON document embedding it reads naturally.

        Returns:
            A JSON-serialisable mapping with the keys of a CSV export.
        """
        return {
            "identifier": self.identifier,
            "table_name": self.table_name,
            "auth_name": self.auth_name,
            "code": self.code,
            "deprecated": self.deprecated,
            "kind": self.kind.value,
            "name": self.name,
            "stated_authority": (
                None if self.stated_authority is None else str(self.stated_authority)
            ),
            "version": self.version,
            "sha256": self.sha256,
            "source": self.source,
            "persistable_reference": (
                json_object(self.payload) if pretty else self.payload
            ),
        }


def pretty_persistable_reference(
    payload: str, *, indent: int = 2, wkt: bool = False
) -> str:
    """Lay a persistableReference out for reading.

    By default the result is the reference's JSON, indented, with its members
    in the order the payload states them and each WKT a single string, so it
    is still JSON. With ``wkt`` each embedded ESRI WKT is laid out over
    indented lines beneath its key as well. Only whitespace changes either way,
    but the second is no longer JSON: it is for reading, and the verbatim
    payload is what to store or send.

    Args:
        payload: The reference, plain or URL-encoded JSON.
        indent: Spaces per level of nesting, of the JSON and the WKT alike.
        wkt: Lay the embedded WKT out over indented lines. A WKT that does not
            parse is left as the string it is.

    Returns:
        The laid-out text.

    Raises:
        MalformedReferenceError: If the payload is not a JSON object.

    Example:
        >>> print(pretty_persistable_reference(
        ...     '{"type":"LBC","wkt":"GEOGCS[\\\\"a\\\\",DATUM[\\\\"b\\\\",'
        ...     'SPHEROID[\\\\"c\\\\",1.0,2.0]]]"}',
        ...     wkt=True,
        ... ))
        {
          "type": "LBC",
          "wkt":
            GEOGCS["a",
              DATUM["b",
                SPHEROID["c",1.0,2.0]]]
        }
    """
    data = json_object(payload)
    return _display(data, indent) if wkt else _json(data, indent)


def persistable_reference_from_catalog(
    value: str | CoordinateReferenceSystem | CRS,
    *,
    entity: Entity | None = None,
    database: Path | None = None,
) -> CatalogPersistableReference | None:
    """The persistableReference recorded for one object.

    Args:
        value: An OSDU record id, with or without its data partition or a
            version separator; ``"AUTH:CODE"``; or a CRS, looked up by the id
            or code it was resolved from. A record's bare ``data.ID``, such as
            ``"Projected:EPSG::23031"``, is taken when ``entity`` says what
            kind of record it is.
        entity: Look only among CRSs or only among operations, for a code
            that names one of each.
        database: The proj.db to read; the first on PROJ's data path by
            default.

    Returns:
        The reference, or None when none is recorded; see
        :func:`missing_reference_reason`.

    Raises:
        AmbiguousCatalogReferenceError: If ``"AUTH:CODE"`` names an object more
            than one active catalogue record publishes, with different
            references. Name the record by its OSDU id instead.
        ValueError: If ``value`` is neither an OSDU id nor ``"AUTH:CODE"``, or
            ``entity`` is not ``"crs"`` or ``"operation"``.

    Example:
        >>> persistable_reference_from_catalog(
        ...     "opendes:reference-data--CoordinateReferenceSystem:"
        ...     "BoundProjected:EPSG::23031_EPSG::1612"
        ... ).kind  # doctest: +SKIP
        <Kind.EARLY_BOUND_CRS: 'EBC'>
    """
    target = _target(value, entity)
    if target is None:
        return None
    if target.osdu is not None:
        if not _entity_matches(target.osdu, target.tables):
            return None
        for identifier in target.osdu.candidates():
            rows = stored_reference_rows(
                SCHEME, identifiers=(identifier,), database=database
            )
            if rows:
                return _reference(rows[0])
        return None
    rows = stored_reference_rows(
        SCHEME,
        table_names=target.tables,
        auth_name=target.auth_name,
        code=target.code,
        database=database,
    )
    return _single(rows, target.described)


def persistable_references_from_catalog(
    *,
    entity: Entity | None = None,
    kinds: Iterable[Kind | str] | None = None,
    authorities: Iterable[str] | None = None,
    include_deprecated: bool = True,
    database: Path | None = None,
) -> tuple[CatalogPersistableReference, ...]:
    """Every persistableReference recorded, optionally filtered.

    Args:
        entity: Only those for CRSs, or only those for operations.
        kinds: Only those stating these kinds, for example ``[Kind.EARLY_BOUND_CRS]``
            or ``["EBC"]``.
        authorities: Only those for objects of these authorities.
        include_deprecated: Include records the catalogue marks inactive.
        database: The proj.db to read; the first on PROJ's data path by
            default.

    Returns:
        The references, ordered by record id. Empty when the database records
        none.

    Raises:
        ValueError: If a kind or ``entity`` is not one there is.
    """
    rows = stored_reference_rows(
        SCHEME,
        table_names=_tables(entity),
        kinds=None
        if kinds is None
        else frozenset(Kind(str(kind).upper()).value for kind in kinds),
        authorities=None if authorities is None else frozenset(authorities),
        include_deprecated=include_deprecated,
        database=database,
    )
    return tuple(_reference(row) for row in rows)


def missing_reference_reason(
    value: str | CoordinateReferenceSystem | CRS,
    *,
    entity: Entity | None = None,
    database: Path | None = None,
) -> str:
    """Why no persistableReference is recorded for an object, in actionable words.

    Args:
        value: What :func:`persistable_reference_from_catalog` was asked for.
        entity: As given to :func:`persistable_reference_from_catalog`.
        database: As given to :func:`persistable_reference_from_catalog`.

    Returns:
        A sentence for a message.

    Raises:
        ValueError: As :func:`persistable_reference_from_catalog` does.
    """
    where = "the active proj.db" if database is None else str(database)
    target = _target(value, entity)
    if target is None:
        return (
            f"{_shown(value)} was not resolved from an identifier or an authority "
            "code, so it names no catalogue record"
        )
    if database_file(database) is None:
        return (
            "no proj.db is on PROJ's data path"
            if database is None
            else f"{where} does not exist"
        )
    if not persistable_references_present(database):
        return (
            f"{where} records no persistable references: build it from an OSDU "
            "catalogue with geodetic-osdudb, with osdu_persistable_references "
            "on"
            + (", and put it first on PROJ's data path" if database is None else "")
        )
    if target.osdu is not None:
        osdu = target.osdu
        if not _entity_matches(osdu, target.tables):
            return (
                f"{_shown(value)} is the OSDU id of a {osdu.described}, not of a "
                f"{_described_entity(entity)}"
            )
        recorded = recorded_identifiers(
            SCHEME, identifiers=osdu.candidates(), database=database
        )
        if not recorded:
            if database is None:
                return unresolved_reason(osdu)
            return (
                f"{where} records no OSDU id {osdu.identifier!r}, so the "
                "catalogue it was built from does not publish that record, or "
                "the build left it out"
            )
        identifiers = tuple(item for item in osdu.candidates() if item in recorded)[:1]
    else:
        identifiers = recorded_identifiers(
            SCHEME,
            table_names=target.tables,
            auth_name=target.auth_name,
            code=target.code,
            database=database,
        )
        if not identifiers:
            return (
                f"no record of the catalogue {where} was built from names "
                f"{target.described}; only an object a catalogue record names has "
                "a persistable reference"
            )
    if stored_reference_rows(SCHEME, identifiers=identifiers, database=database):
        return f"a persistable reference is recorded for {target.described}"
    # Only once no reference is recorded: the history keeps what every build
    # left out, including a reference a later build then recorded.
    gaps = unrecorded_reference_reasons(database)
    if reasons := [gaps[item] for item in identifiers if item in gaps]:
        return "; ".join(reasons)
    return "no persistable reference is recorded for " + ", ".join(
        repr(item) for item in identifiers
    )


def export_persistable_references_from_catalog(
    destination: Path | IO[str],
    *,
    format: ExportFormat = "json",
    pretty: bool = False,
    wkt: bool = False,
    values: Sequence[str] | None = None,
    entity: Entity | None = None,
    kinds: Iterable[Kind | str] | None = None,
    authorities: Iterable[str] | None = None,
    include_deprecated: bool = True,
    database: Path | None = None,
) -> int:
    """Write recorded persistableReferences to a file.

    Each entry carries the reference and what is recorded about it: the record
    id, the object, the kind, the stated authority code, the version, the
    digest and the source. The reference is the verbatim string unless
    ``pretty`` is given.

    With ``values`` the named objects are written, in that order, each entry
    under a ``query`` key as well. An object with no reference still gets an
    entry, with ``persistable_reference`` null and a ``reason``, so a missing
    reference shows up in the file rather than as a gap.

    Args:
        destination: A path, or a text stream to write to.
        format: ``"json"``, a document holding a list of entries;
            ``"jsonl"``, one compact entry per line; or ``"csv"``.
        pretty: JSON only: give each reference as its decoded object, so the
            document reads naturally.
        wkt: JSON only: also lay each embedded ESRI WKT out over indented
            lines. The file is then for reading and is not JSON.
        values: Export these objects, named as
            :func:`persistable_reference_from_catalog` takes them, rather than
            every reference recorded.
        entity: Only CRSs, or only operations.
        kinds: Only references stating these kinds. Not with ``values``.
        authorities: Only objects of these authorities. Not with ``values``.
        include_deprecated: Include inactive records. Not with ``values``.
        database: The proj.db to read; the first on PROJ's data path by
            default.

    Returns:
        How many references were written, not counting entries for objects
        that have none.

    Raises:
        ValueError: If ``pretty`` or ``wkt`` is given for JSONL or CSV, a
            filter is given with ``values``, or a value is neither an OSDU id
            nor ``"AUTH:CODE"``.
    """
    if format not in ("json", "jsonl", "csv"):
        raise ValueError(f"{format!r} is not an export format: json, jsonl or csv")
    if (pretty or wkt) and format != "json":
        raise ValueError("pretty output is only written as json")
    if values is not None and (
        kinds is not None or authorities is not None or not include_deprecated
    ):
        raise ValueError(
            "named values are exported as they are; kinds, authorities and "
            "include_deprecated only filter a full export"
        )
    pretty = pretty or wkt
    entries: list[dict[str, Any]] = []
    found = 0
    if values is None:
        for reference in persistable_references_from_catalog(
            entity=entity,
            kinds=kinds,
            authorities=authorities,
            include_deprecated=include_deprecated,
            database=database,
        ):
            entries.append(reference.as_dict(pretty=pretty))
            found += 1
    else:
        for value in values:
            try:
                named = persistable_reference_from_catalog(
                    value, entity=entity, database=database
                )
            except AmbiguousCatalogReferenceError as error:
                entries.append(_unfound(value, str(error)))
                continue
            if named is None:
                reason = missing_reference_reason(
                    value, entity=entity, database=database
                )
                entries.append(_unfound(value, reason))
                continue
            entries.append({"query": value, **named.as_dict(pretty=pretty)})
            found += 1

    if isinstance(destination, Path):
        with destination.open("w", encoding="utf-8", newline="") as stream:
            _write(stream, entries, format, pretty=pretty, wkt=wkt, named=values)
    else:
        _write(destination, entries, format, pretty=pretty, wkt=wkt, named=values)
    return found


@dataclass(frozen=True, slots=True)
class _Target:
    """What a lookup is for: a record id, or an object's authority and code."""

    described: str
    tables: frozenset[str] | None
    osdu: OsduId | None = None
    auth_name: str | None = None
    code: str | None = None


def _target(value: object, entity: Entity | None) -> _Target | None:
    """Read what a caller named, or None for a CRS that names no record."""
    from geodetic_engine.geodesy.crs import CoordinateReferenceSystem

    tables = _tables(entity)
    if isinstance(value, CoordinateReferenceSystem | CRS):
        if entity == "operation":
            raise ValueError("a CRS cannot be looked up among operations")
        return _crs_target(value)
    if not isinstance(value, str):
        raise ValueError(
            f"cannot look a persistable reference up from {type(value).__name__}; "
            "give an OSDU record id, AUTH:CODE, or a CRS"
        )
    text = value.strip()
    if (osdu := parse_osdu_id(text)) is not None:
        return _Target(osdu.identifier, tables, osdu=osdu)
    if (match := _AUTHORITY_CODE.fullmatch(text)) is not None:
        return _Target(
            f"{match['auth']}:{match['code']}",
            tables,
            auth_name=match["auth"],
            code=match["code"],
        )
    if entity is not None:
        expected = CRS_ENTITY if entity == "crs" else TRANSFORMATION_ENTITY
        if (osdu := parse_osdu_id(text, entity=expected)) is not None:
            return _Target(osdu.identifier, tables, osdu=osdu)
    raise ValueError(
        f"{value!r} is neither an OSDU record id nor an AUTH:CODE authority code"
    )


def _crs_target(crs: CoordinateReferenceSystem | CRS) -> _Target | None:
    """The record id or code a CRS was resolved from.

    A bound CRS PROJ read from the database reports no authority code of its
    own, and the name of the CRS it wraps, so the definition it was resolved
    from comes first. A CRS defined by its WKT is identified only when PROJ is
    certain which object it is.
    """
    from geodetic_engine.geodesy.crs import CoordinateReferenceSystem

    tables = CRS_OBJECT_TABLES
    if isinstance(crs, CoordinateReferenceSystem):
        definition = crs.definition.strip()
        if (osdu := parse_osdu_id(definition)) is not None:
            return _Target(osdu.identifier, tables, osdu=osdu)
        if (match := _AUTHORITY_CODE.fullmatch(definition)) is not None:
            return _Target(
                f"{match['auth']}:{match['code']}",
                tables,
                auth_name=match["auth"],
                code=match["code"],
            )
        crs = crs.crs
    authority = crs.to_authority(min_confidence=100)
    if authority is None:
        return None
    auth_name, code = authority
    return _Target(f"{auth_name}:{code}", tables, auth_name=auth_name, code=code)


def _tables(entity: Entity | None) -> frozenset[str] | None:
    """The object tables an entity is looked for in; all of them for None."""
    if entity is None:
        return None
    if entity not in _ENTITY_TABLES:
        raise ValueError(f"{entity!r} is not an entity: crs or operation")
    return _ENTITY_TABLES[entity]


def _entity_matches(osdu: OsduId, tables: frozenset[str] | None) -> bool:
    """Whether an OSDU id names the kind of record a lookup was limited to."""
    if tables is None:
        return True
    return _ENTITY_TABLES[_ENTITY_OF_ID[osdu.entity]] == tables


def _described_entity(entity: Entity | None) -> str:
    """An entity, in words."""
    return "coordinate operation" if entity == "operation" else "CRS"


def _single(
    rows: Sequence[StoredReferenceRow], described: str
) -> CatalogPersistableReference | None:
    """The one reference several rows agree on, preferring active records."""
    if not rows:
        return None
    active = [row for row in rows if not row.deprecated] or list(rows)
    if len({row.sha256 for row in active}) > 1:
        records = ", ".join(repr(row.identifier) for row in active)
        raise AmbiguousCatalogReferenceError(
            f"{described} has {len(active)} different persistable references, "
            f"published by {records}; name the record by its OSDU id"
        )
    return _reference(active[0])


def _reference(row: StoredReferenceRow) -> CatalogPersistableReference:
    """A stored row as the public type."""
    stated = (
        AuthorityCode(row.stated_auth_name, row.stated_code)
        if row.stated_auth_name and row.stated_code
        else None
    )
    return CatalogPersistableReference(
        identifier=row.identifier,
        table_name=row.table_name,
        auth_name=row.auth_name,
        code=row.code,
        deprecated=row.deprecated,
        kind=Kind(row.kind),
        name=row.name,
        stated_authority=stated,
        version=row.version,
        sha256=row.sha256,
        source=row.source,
        payload=row.payload,
    )


def _unfound(value: str, reason: str) -> dict[str, Any]:
    """The entry of an object with no reference to export."""
    return {"query": value, "persistable_reference": None, "reason": reason}


def _shown(value: object) -> str:
    """A looked-up value, in a message."""
    return repr(value) if isinstance(value, str) else f"the {type(value).__name__}"


def _write(
    stream: IO[str],
    entries: list[dict[str, Any]],
    format: ExportFormat,
    *,
    pretty: bool,
    wkt: bool,
    named: Sequence[str] | None,
) -> None:
    """Write export entries to a stream in one format."""
    if format == "json":
        stream.write(_display(entries, 2) if wkt else _json(entries, 2))
        stream.write("\n")
    elif format == "jsonl":
        for entry in entries:
            stream.write(json.dumps(entry, ensure_ascii=False, separators=(",", ":")))
            stream.write("\n")
    else:
        columns = (("query",) if named is not None else ()) + _FIELDS
        if named is not None:
            columns += ("reason",)
        writer = csv.DictWriter(stream, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        for entry in entries:
            writer.writerow({column: entry.get(column) for column in columns})


def _json(data: Any, indent: int) -> str:
    """Indented JSON keeping non-ASCII text readable."""
    return json.dumps(data, indent=indent, ensure_ascii=False)


def _display(data: Any, indent: int) -> str:
    """Indented JSON with every ESRI WKT laid out beneath its key.

    Each WKT is swapped for a placeholder no JSON string can otherwise contain,
    the JSON is written, and each placeholder is then replaced by the WKT laid
    out over lines indented one level deeper than its key.
    """
    blocks: dict[str, str] = {}

    def swapped(value: Any) -> Any:
        if isinstance(value, dict):
            result: dict[str, Any] = {}
            for key, member in value.items():
                if str(key).casefold() == "wkt" and isinstance(member, str):
                    try:
                        laid_out = esriwkt.pretty(member, indent=indent)
                    except MalformedReferenceError:
                        result[key] = member
                        continue
                    token = f"\x00{len(blocks)}\x00"
                    blocks[json.dumps(token)] = laid_out
                    result[key] = token
                else:
                    result[key] = swapped(member)
            return result
        if isinstance(value, list):
            return [swapped(member) for member in value]
        return value

    text = json.dumps(swapped(data), indent=indent, ensure_ascii=False)
    if not blocks:
        return text
    lines: list[str] = []
    for line in text.split("\n"):
        token = next((token for token in blocks if token in line), None)
        if token is None:
            lines.append(line)
            continue
        before, after = line.split(token, 1)
        margin = " " * (len(line) - len(line.lstrip()) + indent)
        lines.append(before.rstrip())
        laid_out = blocks[token].split("\n")
        lines.extend(margin + part for part in laid_out[:-1])
        lines.append(margin + laid_out[-1] + after)
    return "\n".join(lines)
