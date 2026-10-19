"""Reading the one thing PROJ's database knows but will not hand back.

proj.db has no bound CRS table. A bound CRS is stored as an ordinary
``geodetic_crs`` or ``projected_crs`` row whose ``text_definition`` holds the
whole ``BOUNDCRS`` WKT. PROJ honours that when it selects an operation --
a bound ED50 CRS fixes the datum shift where plain ED50 leaves a choice --
but the object it hands back has been unwrapped: ``is_bound`` is False,
``source_crs`` is None, and the PROJJSON carries no ``transformation`` node.

So a caller naming such a CRS by its code gets a CRS that cannot say which
operation it carries, and this package would then refuse the datum change as
ambiguous even though the CRS itself settles it. Reading the stored WKT back
out restores the object the database actually describes.

Only the ``BOUNDCRS`` definitions are read. Every other row is left to PROJ,
which is the authority on its own database.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import sqlite3
from collections.abc import Mapping
from contextlib import closing
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path, PurePosixPath
from threading import local

from pyproj import datadir

logger = logging.getLogger(__name__)

# CRS tables that carry a text_definition column. Checked against the database
# rather than assumed, since the layout differs between PROJ releases.
_CRS_TABLES = (
    "geodetic_crs",
    "projected_crs",
    "vertical_crs",
    "compound_crs",
    "engineering_crs",
)

_DATABASE_NAME = "proj.db"
_BOUND_KEYWORD = "BOUNDCRS"

# Extensions a grid file is published under, by any of the authorities whose
# grids PROJ references. A name ending in anything else is a name, not a file.
_GRID_SUFFIXES = frozenset(
    {
        ".asc",
        ".bin",
        ".byn",
        ".dat",
        ".grd",
        ".gsa",
        ".gsb",
        ".gtx",
        ".las",
        ".los",
        ".tif",
        ".txt",
    }
)

# Where a build of this package records what it wrote and what it left out.
# Spelled here rather than imported from geodetic_engine.projdb, which depends
# on this package and must not be depended on in return.
_BUILD_HISTORY_TABLE = "geodetic_engine_build_history"

# Where a build records OSDU record identifiers for objects in this database.
# Spelled here for the same reason as the history table: this package depends
# on geodesy, so geodesy must not import projdb in return.
_IDENTIFIER_TABLE = "geodetic_engine_osdu_object_identifier"
_LEGACY_IDENTIFIER_TABLE = "geodetic_engine_object_identifier"

# Where a build records the OSDU persistableReference each record publishes,
# against the record's id. Spelled here for the same reason again.
_PERSISTABLE_REFERENCE_TABLE = "geodetic_engine_osdu_persistable_reference"

# The object_view vocabulary an identifier can point into, split by what kind
# of object a caller asked for.
CRS_OBJECT_TABLES = frozenset(
    {
        "geodetic_crs",
        "projected_crs",
        "vertical_crs",
        "compound_crs",
        "engineering_crs",
        "derived_projected_crs",
    }
)
OPERATION_OBJECT_TABLES = frozenset(
    {
        "conversion",
        "helmert_transformation",
        "grid_transformation",
        "other_transformation",
        "concatenated_operation",
    }
)

type DatabaseIdentity = tuple[tuple[str, int, int, int, int, int], ...]
_context = local()


def database_identity() -> DatabaseIdentity:
    """Identify the active search path and on-disk database generations."""
    entries = []
    for directory in datadir.get_data_dir().split(os.pathsep):
        path = (Path(directory) / _DATABASE_NAME).resolve()
        try:
            stat = path.stat()
        except OSError:
            entries.append((str(path), 0, 0, 0, 0, 0))
        else:
            entries.append(
                (
                    str(path),
                    stat.st_dev,
                    stat.st_ino,
                    stat.st_size,
                    stat.st_mtime_ns,
                    stat.st_ctime_ns,
                )
            )
    identity = tuple(entries)
    if getattr(_context, "identity", None) != identity:
        datadir.set_data_dir(datadir.get_data_dir())
        _context.identity = identity
    return identity


@lru_cache(maxsize=32)
def database_fingerprints(identity: DatabaseIdentity) -> tuple[tuple[str, str], ...]:
    """Return SHA-256 fingerprints of the databases in one resolution context."""
    found = []
    for path, _, _, size, _, _ in identity:
        if size:
            with Path(path).open("rb") as stream:
                found.append((path, hashlib.file_digest(stream, "sha256").hexdigest()))
    return tuple(found)


def bound_definition(auth_name: str, code: str) -> str | None:
    """The stored ``BOUNDCRS`` WKT for an authority code, if there is one.

    Args:
        auth_name: Authority of the CRS, for example ``"Example"``.
        code: Its code.

    Returns:
        The WKT, or None when the code names no CRS, names one that is not
        bound, or no readable database defines it.

    Example:
        >>> bound_definition("Example", "1100001")  # doctest: +SKIP
        'BOUNDCRS[SOURCECRS[GEOGCRS["ED50",...'
    """
    return _definitions(database_identity()).by_code.get(
        (auth_name.casefold(), str(code))
    )


def bound_definition_by_name(name: str) -> str | None:
    """The stored ``BOUNDCRS`` WKT going by a CRS's name.

    Needed because PROJ answers a bound CRS's own code with the *base* CRS,
    which reports the base's authority rather than the custom authority, so
    its code is no way back to the definition. The name survives that unwrapping.

    A name defined by more than one bound CRS is not resolved, since there
    would be no way to tell which was meant.

    Args:
        name: The CRS name, compared case insensitively.

    Returns:
        The WKT, or None when no single bound CRS goes by that name.
    """
    return _definitions(database_identity()).by_name.get(name.strip().casefold())


@dataclass(frozen=True, slots=True)
class _BoundDefinitions:
    """Bound CRS definitions, reachable by code and by name."""

    by_code: Mapping[tuple[str, str], str]
    by_name: Mapping[str, str]


@lru_cache(maxsize=8)
def _definitions(identity: DatabaseIdentity) -> _BoundDefinitions:
    """Every bound CRS definition in the databases PROJ is currently reading.

    Read once per data directory and kept, because the alternative is a query
    for every CRS resolved. Directories are searched in PROJ's own order, so
    the first database defining a code wins, exactly as PROJ resolves it.
    """
    found: dict[tuple[str, str], str] = {}
    names: dict[str, str] = {}
    for path, *_ in identity:
        database = Path(path)
        if not database.is_file():
            continue
        try:
            _read_into(found, names, database)
        except sqlite3.Error as error:
            # A database that cannot be read tells us nothing about bound CRSs;
            # it must not stop an ordinary CRS from resolving.
            logger.debug("could not read %s: %s", database, error)
        break
    if found:
        logger.debug("%d bound CRS definitions available", len(found))
    return _BoundDefinitions(by_code=found, by_name=names)


def skip_reason(definition: str) -> str | None:
    """Why the build left this CRS out, if that is why it cannot be resolved.

    An object the register defines but PROJ cannot represent is skipped when the
    database is built, with the reason recorded in the build history. Without
    this, naming such a CRS gives only PROJ's "unknown name", which is true but
    says nothing about a definition that was deliberately not written.

    Args:
        definition: What the caller named the CRS by: its name, ``"AUTH:CODE"``,
            or an identifier another scheme gives it, such as an OSDU record
            id without its partition.

    Returns:
        A sentence naming the object and the reason, or None when the build
        recorded no such skip.
    """
    return _skips(database_identity()).get(definition.strip().casefold())


@lru_cache(maxsize=8)
def _skips(identity: DatabaseIdentity) -> Mapping[str, str]:
    """Skipped objects of the databases PROJ is reading, by name and by code.

    Every build appends its own report, so a database written by more than one
    source carries more than one; later reports win, being the later word on
    what the database now holds.
    """
    found: dict[str, str] = {}
    for path, *_ in identity:
        database = Path(path)
        if not database.is_file():
            continue
        try:
            _read_skips_into(found, database)
        except (sqlite3.Error, ValueError) as error:
            # No build history, or an unreadable one, only means this database
            # cannot explain itself; it must not stop a CRS from resolving.
            logger.debug("could not read build history of %s: %s", database, error)
        break
    return found


def _read_skips_into(found: dict[str, str], database: Path) -> None:
    """Collect the skipped objects recorded in one database's build history."""
    with closing(sqlite3.connect(f"file:{database}?mode=ro", uri=True)) as connection:
        if not _has_table(connection, _BUILD_HISTORY_TABLE):
            return
        reports = connection.execute(
            f"SELECT report FROM {_BUILD_HISTORY_TABLE} ORDER BY sequence"
        )
        for (report,) in reports:
            for entry in json.loads(report).get("skipped") or ():
                if not isinstance(entry, dict) or not entry.get("reason"):
                    continue
                identifier = f"{entry.get('auth_name')}:{entry.get('code')}"
                explanation = (
                    f"the build that wrote this database left out {identifier} "
                    f"because {entry['reason']}"
                )
                for key in (entry.get("name"), identifier, entry.get("identifier")):
                    if key:
                        found[str(key).strip().casefold()] = explanation


@dataclass(frozen=True, slots=True)
class IdentifiedObject:
    """The object an identifier from another scheme names in proj.db.

    Attributes:
        table_name: The object's table, in the vocabulary of PROJ's
            ``object_view``, for example ``"projected_crs"`` or
            ``"helmert_transformation"``.
        auth_name: The object's authority, for example ``"EPSG"``.
        code: The object's code.
        deprecated: Whether the identifying scheme marks it inactive. This is
            the scheme's own flag, independent of whether the authority has
            deprecated the object.
    """

    table_name: str
    auth_name: str
    code: str
    deprecated: bool

    @property
    def authority_code(self) -> str:
        """``"AUTH:CODE"``, as PROJ resolves it."""
        return f"{self.auth_name}:{self.code}"

    @property
    def is_crs(self) -> bool:
        """Whether the object is a coordinate reference system."""
        return self.table_name in CRS_OBJECT_TABLES

    @property
    def is_operation(self) -> bool:
        """Whether the object is a coordinate operation."""
        return self.table_name in OPERATION_OBJECT_TABLES


def identified_object(scheme: str, identifier: str) -> IdentifiedObject | None:
    """The object an identifier names in the active database, if it names one.

    The identifier is matched exactly. Nothing is normalized here beyond what
    the caller has already done, and nothing is matched approximately: an
    identifier one character away from another names a different object, or
    none.

    Args:
        scheme: The identifying scheme, for example ``"OSDU"``.
        identifier: The identifier, as the build recorded it.

    Returns:
        The object, or None when the active database records no such
        identifier, or records none at all.

    Example:
        >>> identified_object(
        ...     "OSDU",
        ...     "reference-data--CoordinateReferenceSystem:Projected:EPSG::23031",
        ... )  # doctest: +SKIP
        IdentifiedObject(table_name='projected_crs', auth_name='EPSG', ...)
    """
    return _identified(database_identity(), scheme, identifier)


def identifier_scheme_present(scheme: str) -> bool:
    """Whether the active database records any identifier of a scheme.

    Tells "this database was never built to answer such identifiers" apart
    from "it was, and this one is not among them".
    """
    return scheme in _schemes(database_identity())


def object_identifiers(
    table_name: str, auth_name: str, code: str, *, scheme: str
) -> tuple[str, ...]:
    """Every identifier of a scheme recorded for one object.

    Args:
        table_name: The object's table, in ``object_view`` vocabulary.
        auth_name: The object's authority.
        code: The object's code.
        scheme: The identifying scheme, for example ``"OSDU"``.

    Returns:
        The identifiers, sorted; empty when there are none.
    """
    return _identifiers_of(
        database_identity(), scheme, table_name, auth_name, str(code)
    )


@lru_cache(maxsize=1024)
def _identified(
    identity: DatabaseIdentity, scheme: str, identifier: str
) -> IdentifiedObject | None:
    """Look one identifier up in the active database, once per database."""
    rows = _query_identifiers(
        identity,
        "SELECT table_name, auth_name, code, deprecated FROM {table} "
        "WHERE scheme = ? AND identifier = ?",
        (scheme, identifier),
    )
    if not rows:
        return None
    table_name, auth_name, code, deprecated = rows[0]
    return IdentifiedObject(
        table_name=str(table_name),
        auth_name=str(auth_name),
        code=str(code),
        deprecated=bool(deprecated),
    )


@lru_cache(maxsize=8)
def _schemes(identity: DatabaseIdentity) -> frozenset[str]:
    """The identifier schemes the active database records."""
    rows = _query_identifiers(identity, "SELECT DISTINCT scheme FROM {table}", ())
    return frozenset(str(scheme) for (scheme,) in rows)


@lru_cache(maxsize=1024)
def _identifiers_of(
    identity: DatabaseIdentity,
    scheme: str,
    table_name: str,
    auth_name: str,
    code: str,
) -> tuple[str, ...]:
    """The identifiers recorded for one object, through the reverse index."""
    rows = _query_identifiers(
        identity,
        "SELECT identifier FROM {table} WHERE table_name = ? AND auth_name = ? "
        "AND code = ? AND scheme = ? ORDER BY identifier",
        (table_name, auth_name, code, scheme),
    )
    return tuple(str(identifier) for (identifier,) in rows)


def _query_identifiers(
    identity: DatabaseIdentity, statement: str, parameters: tuple[str, ...]
) -> list[tuple[object, ...]]:
    """Run a query against the identifier table of the first database PROJ reads.

    A database without either table, which is every database not built by this
    package and every one built before identifiers were recorded, answers with
    nothing rather than failing. The former table name remains readable until
    an appended build migrates it.
    """
    for path, *_ in identity:
        database = Path(path)
        if not database.is_file():
            continue
        try:
            with closing(
                sqlite3.connect(f"file:{database}?mode=ro", uri=True)
            ) as connection:
                table = next(
                    (
                        candidate
                        for candidate in (
                            _IDENTIFIER_TABLE,
                            _LEGACY_IDENTIFIER_TABLE,
                        )
                        if _has_table(connection, candidate)
                    ),
                    None,
                )
                if table is None:
                    return []
                return list(
                    connection.execute(statement.format(table=table), parameters)
                )
        except sqlite3.Error as error:
            logger.debug("could not read identifiers of %s: %s", database, error)
            return []
    return []


def _has_table(connection: sqlite3.Connection, table: str) -> bool:
    """Whether a table exists in this database."""
    return bool(
        connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
        ).fetchone()
    )


# A database file as read at one moment: its resolved path, device, inode,
# size and modification time. Results are cached against this, so a database
# rebuilt in place is read afresh.
type DatabaseFile = tuple[str, int, int, int, int]


@dataclass(frozen=True, slots=True)
class StoredReferenceRow:
    """One persistableReference a build recorded, with the object it is for.

    Attributes:
        identifier: The id of the record that publishes it, without the data
            partition.
        table_name: The table of the object the id names, in the vocabulary of
            PROJ's ``object_view``.
        auth_name: The object's authority.
        code: The object's code.
        deprecated: Whether the identifying scheme marks the record inactive.
        kind: The kind of definition stated, for example ``"EBC"``.
        name: The name the reference states, if any.
        stated_auth_name: The authority of the ``authCode`` the reference
            states, which is provenance and may disagree with ``auth_name``.
        stated_code: The code of that ``authCode``.
        version: The producer version the reference states.
        sha256: Hex SHA-256 of ``payload`` encoded as UTF-8.
        payload: The reference, exactly as the catalogue published it.
        source: What the build read it from.
    """

    identifier: str
    table_name: str
    auth_name: str
    code: str
    deprecated: bool
    kind: str
    name: str | None
    stated_auth_name: str | None
    stated_code: str | None
    version: str | None
    sha256: str
    payload: str
    source: str | None


def database_file(database: Path | None = None) -> DatabaseFile | None:
    """Identify a database file, or the one PROJ reads first.

    Args:
        database: A proj.db to read. When omitted, the first proj.db on PROJ's
            search path, the one PROJ itself resolves names against.

    Returns:
        The file's identity, or None when there is no such file.
    """
    if database is None:
        for entry, device, inode, size, modified, _ in database_identity():
            if Path(entry).is_file():
                return (entry, device, inode, size, modified)
        return None
    path = Path(database)
    try:
        stat = path.stat()
    except OSError:
        return None
    if not path.is_file():
        return None
    return (
        str(path.resolve()),
        stat.st_dev,
        stat.st_ino,
        stat.st_size,
        stat.st_mtime_ns,
    )


def persistable_references_present(database: Path | None = None) -> bool:
    """Whether a database records any persistable references at all.

    Tells "this database was never built to answer such lookups" apart from
    "it was, and this object has none".

    Args:
        database: The proj.db to read; PROJ's first by default.
    """
    found = database_file(database)
    return found is not None and _references_recorded(found)


def stored_reference_rows(
    scheme: str,
    *,
    identifiers: tuple[str, ...] | None = None,
    table_names: frozenset[str] | None = None,
    auth_name: str | None = None,
    code: str | None = None,
    kinds: frozenset[str] | None = None,
    authorities: frozenset[str] | None = None,
    include_deprecated: bool = True,
    database: Path | None = None,
) -> tuple[StoredReferenceRow, ...]:
    """The persistable references a database records, filtered.

    Every filter given must hold. A database that records no persistable
    references, or cannot be read, answers with nothing rather than failing.

    Args:
        scheme: The identifying scheme, for example ``"OSDU"``.
        identifiers: Only references recorded against these ids.
        table_names: Only those for objects in these ``object_view`` tables.
        auth_name: Only those for objects of this authority, matched without
            case.
        code: Only those for objects with this code.
        kinds: Only those stating these kinds, for example ``{"EBC"}``.
        authorities: Only those for objects of these authorities, matched
            without case.
        include_deprecated: Include records the scheme marks inactive.
        database: The proj.db to read; PROJ's first by default.

    Returns:
        The references, ordered by identifier.
    """
    found = database_file(database)
    if found is None:
        return ()
    clauses = ["i.scheme = ?"]
    parameters: list[str] = [scheme]
    for column, values in (
        ("i.identifier", identifiers),
        ("i.table_name", table_names),
        ("r.kind", kinds),
    ):
        if values is not None:
            ordered = sorted(values)
            clauses.append(f"{column} IN ({', '.join('?' * len(ordered))})")
            parameters.extend(ordered)
    if authorities is not None:
        folded = sorted({authority.casefold() for authority in authorities})
        clauses.append(f"lower(i.auth_name) IN ({', '.join('?' * len(folded))})")
        parameters.extend(folded)
    if auth_name is not None:
        clauses.append("i.auth_name = ? COLLATE NOCASE")
        parameters.append(auth_name)
    if code is not None:
        clauses.append("i.code = ?")
        parameters.append(str(code))
    if not include_deprecated:
        clauses.append("i.deprecated = 0")
    return _stored_rows(found, " AND ".join(clauses), tuple(parameters))


def recorded_identifiers(
    scheme: str,
    *,
    identifiers: tuple[str, ...] | None = None,
    table_names: frozenset[str] | None = None,
    auth_name: str | None = None,
    code: str | None = None,
    database: Path | None = None,
) -> tuple[str, ...]:
    """The ids of a scheme a database records, filtered like the references.

    Args:
        scheme: The identifying scheme, for example ``"OSDU"``.
        identifiers: Only these ids.
        table_names: Only ids naming objects in these ``object_view`` tables.
        auth_name: Only ids naming objects of this authority, without case.
        code: Only ids naming objects with this code.
        database: The proj.db to read; PROJ's first by default.

    Returns:
        The ids, sorted; empty when the database records none.
    """
    found = database_file(database)
    if found is None:
        return ()
    clauses = ["scheme = ?"]
    parameters: list[str] = [scheme]
    for column, values in (("identifier", identifiers), ("table_name", table_names)):
        if values is not None:
            ordered = sorted(values)
            clauses.append(f"{column} IN ({', '.join('?' * len(ordered))})")
            parameters.extend(ordered)
    if auth_name is not None:
        clauses.append("auth_name = ? COLLATE NOCASE")
        parameters.append(auth_name)
    if code is not None:
        clauses.append("code = ?")
        parameters.append(str(code))
    return _recorded_ids(found, " AND ".join(clauses), tuple(parameters))


def unrecorded_reference_reasons(database: Path | None = None) -> Mapping[str, str]:
    """Why a build recorded no persistable reference for a record, by its id.

    Read from the build history, where each build's report lists the records
    that stated none and those whose reference could not be used. Later
    reports win, being the later word on what the database holds.

    Args:
        database: The proj.db to read; PROJ's first by default.

    Returns:
        A sentence for each id, empty when the history records none.
    """
    found = database_file(database)
    return {} if found is None else _reference_gaps(found)


_STORED_COLUMNS = (
    "i.identifier, i.table_name, i.auth_name, i.code, i.deprecated, r.kind, "
    "r.name, r.stated_auth_name, r.stated_code, r.version, r.sha256, "
    "r.persistable_reference, r.source"
)


@lru_cache(maxsize=8)
def _references_recorded(found: DatabaseFile) -> bool:
    """Whether one database file has the persistable reference table."""
    try:
        with closing(_read_only(found)) as connection:
            return _has_table(connection, _PERSISTABLE_REFERENCE_TABLE)
    except sqlite3.Error as error:
        logger.debug("could not read %s: %s", found[0], error)
        return False


@lru_cache(maxsize=256)
def _stored_rows(
    found: DatabaseFile, where: str, parameters: tuple[str, ...]
) -> tuple[StoredReferenceRow, ...]:
    """Run one filtered query for persistable references, once per database."""
    if not _references_recorded(found):
        return ()
    statement = (
        f"SELECT {_STORED_COLUMNS} FROM {_IDENTIFIER_TABLE} i "
        f"JOIN {_PERSISTABLE_REFERENCE_TABLE} r "
        "ON r.scheme = i.scheme AND r.identifier = i.identifier "
        f"WHERE {where} ORDER BY i.identifier"
    )
    try:
        with closing(_read_only(found)) as connection:
            rows = connection.execute(statement, parameters).fetchall()
    except sqlite3.Error as error:
        logger.debug("could not read persistable references of %s: %s", found[0], error)
        return ()
    return tuple(
        StoredReferenceRow(
            identifier=str(identifier),
            table_name=str(table_name),
            auth_name=str(auth_name),
            code=str(code),
            deprecated=bool(deprecated),
            kind=str(kind),
            name=_optional(name),
            stated_auth_name=_optional(stated_auth_name),
            stated_code=_optional(stated_code),
            version=_optional(version),
            sha256=str(digest),
            payload=str(payload),
            source=_optional(source),
        )
        for (
            identifier,
            table_name,
            auth_name,
            code,
            deprecated,
            kind,
            name,
            stated_auth_name,
            stated_code,
            version,
            digest,
            payload,
            source,
        ) in rows
    )


@lru_cache(maxsize=256)
def _recorded_ids(
    found: DatabaseFile, where: str, parameters: tuple[str, ...]
) -> tuple[str, ...]:
    """Run one filtered query against the identifier table, once per database."""
    try:
        with closing(_read_only(found)) as connection:
            if not _has_table(connection, _IDENTIFIER_TABLE):
                return ()
            rows = connection.execute(
                f"SELECT identifier FROM {_IDENTIFIER_TABLE} WHERE {where} "
                "ORDER BY identifier",
                parameters,
            ).fetchall()
    except sqlite3.Error as error:
        logger.debug("could not read identifiers of %s: %s", found[0], error)
        return ()
    return tuple(str(identifier) for (identifier,) in rows)


@lru_cache(maxsize=8)
def _reference_gaps(found: DatabaseFile) -> Mapping[str, str]:
    """The records each build of one database recorded no reference for."""
    gaps: dict[str, str] = {}
    try:
        with closing(_read_only(found)) as connection:
            if not _has_table(connection, _BUILD_HISTORY_TABLE):
                return gaps
            reports = connection.execute(
                f"SELECT report FROM {_BUILD_HISTORY_TABLE} ORDER BY sequence"
            ).fetchall()
    except sqlite3.Error as error:
        logger.debug("could not read build history of %s: %s", found[0], error)
        return gaps
    for (report,) in reports:
        try:
            stated = json.loads(report).get("persistable_references") or {}
        except (ValueError, AttributeError):
            continue
        for group, described in (
            ("missing", "the catalogue record {id} states no persistableReference"),
            (
                "invalid",
                "the persistableReference the catalogue record {id} states was "
                "not recorded because {reason}",
            ),
        ):
            for entry in stated.get(group) or ():
                if not isinstance(entry, dict) or not entry.get("identifier"):
                    continue
                identifier = str(entry["identifier"])
                gaps[identifier] = described.format(
                    id=repr(identifier), reason=entry.get("reason") or "it is unusable"
                )
    return gaps


def _read_only(found: DatabaseFile) -> sqlite3.Connection:
    """Open one database file for reading."""
    return sqlite3.connect(f"file:{found[0]}?mode=ro", uri=True)


def _optional(value: object) -> str | None:
    """A nullable column as text."""
    return None if value is None else str(value)


def _read_into(
    found: dict[tuple[str, str], str], names: dict[str, str], database: Path
) -> None:
    """Collect the bound CRS rows of one database into ``found`` and ``names``."""
    ambiguous: set[str] = set()
    with closing(sqlite3.connect(f"file:{database}?mode=ro", uri=True)) as connection:
        for table in _CRS_TABLES:
            if not _has_text_definition(connection, table):
                continue
            rows = connection.execute(
                f"SELECT auth_name, code, name, text_definition FROM {table} "
                "WHERE text_definition IS NOT NULL"
            )
            for auth_name, code, name, text in rows:
                if not str(text).lstrip().upper().startswith(_BOUND_KEYWORD):
                    continue
                found.setdefault((str(auth_name).casefold(), str(code)), str(text))
                if not name:
                    continue
                key = str(name).strip().casefold()
                if key in names and names[key] != str(text):
                    ambiguous.add(key)
                names.setdefault(key, str(text))
    for key in ambiguous:
        del names[key]


def _has_text_definition(connection: sqlite3.Connection, table: str) -> bool:
    """Whether a table exists in this database and carries a text definition."""
    columns = connection.execute(f"PRAGMA table_info({table})").fetchall()
    return any(column[1] == "text_definition" for column in columns)


@dataclass(frozen=True, slots=True)
class GridDataset:
    """A grid transformation identified by the grid files it reads.

    Attributes:
        method_code: EPSG code of the method, for example ``"9615"`` for NTv2.
        method_name: EPSG name of that method.
        files: The grid file parameters, each as an EPSG parameter code, the
            parameter's name, and the file name the database states. NADCON
            states two, one per direction; NTv2 states one.
    """

    method_code: str
    method_name: str
    files: tuple[tuple[str, str, str], ...]


def grid_dataset(name: str) -> GridDataset | None:
    """Find a grid transformation by the bare name of the grid it reads.

    A grid is named differently by every dialect that references it -- EPSG
    states ``A66 National (13.09.01).gsb``, another dialect
    ``A66_National_13_09_01``, PROJ ships it as
    ``au_icsm_A66_National_13_09_01.tif``. Punctuation is normalized, and PROJ's
    grid-alternatives table supplies modern and legacy filename aliases. The
    result retains the original method and file parameters PROJ accepts in a
    definition; provider prefixes are never guessed or stripped.

    Args:
        name: The grid name, with or without an extension. Punctuation, case
            and spacing are ignored; ``A66_National_13_09_01`` and
            ``A66 National (13.09.01)`` are the same grid.

    Returns:
        The transformation, or None when the database references no such grid,
        or references it from transformations that disagree on the method or on
        which files it reads, in which case there is no single answer to give.

    Example:
        >>> grid_dataset("conus").method_name  # doctest: +SKIP
        'NADCON'
    """
    index, reads = _grid_datasets(database_identity())
    found = index.get(_grid_key(name))
    if not found:
        return None
    # Rows that agree on the method and on the files PROJ reads are the same
    # transformation written two ways, which several EPSG entries are: one
    # states "rdtrans2008", another "rdtrans2008.gsb".
    shapes = {
        (
            dataset.method_code,
            tuple(reads.get(k, k) for k in (_grid_key(f) for _, _, f in dataset.files)),
        )
        for dataset in found
    }
    if len(shapes) != 1:
        return None
    # The spelling that carries file extensions is the one PROJ's own grid
    # alternatives are keyed by.
    return max(found, key=_spelling)


def _spelling(dataset: GridDataset) -> tuple[int, tuple[str, ...]]:
    """Rank one spelling of a grid transformation against another."""
    names = tuple(file for _, _, file in dataset.files)
    return sum("." in name for name in names), names


def _grid_key(name: str) -> str:
    """Normalize punctuation and extensions without guessing filename aliases."""
    text = name.strip()
    # Only a known grid extension is dropped. Taking everything after the last
    # dot would cut "A66 National (13.09.01)" down to "A66 National (13.09".
    if (suffix := PurePosixPath(text).suffix.casefold()) in _GRID_SUFFIXES:
        text = text[: -len(suffix)]
    return "".join(character for character in text if character.isalnum()).casefold()


@lru_cache(maxsize=8)
def _grid_datasets(
    identity: DatabaseIdentity,
) -> tuple[Mapping[str, tuple[GridDataset, ...]], Mapping[str, str]]:
    """Every grid transformation in the active databases, by grid name.

    Also returns the file PROJ reads for each grid name it has an alternative
    for. Read once per data directory, like the bound CRS definitions above,
    and for the same reason.
    """
    found: dict[str, set[GridDataset]] = {}
    reads: dict[str, str] = {}
    for path, *_ in identity:
        database = Path(path)
        if not database.is_file():
            continue
        try:
            _read_grids_into(found, reads, database)
        except sqlite3.Error as error:
            logger.debug(
                "could not read grid transformations of %s: %s", database, error
            )
        break
    return {key: tuple(datasets) for key, datasets in found.items()}, reads


def _read_grids_into(
    found: dict[str, set[GridDataset]], reads: dict[str, str], database: Path
) -> None:
    """Index one database's grid transformations by each grid name it reads."""
    with closing(sqlite3.connect(f"file:{database}?mode=ro", uri=True)) as connection:
        if not _has_table(connection, "grid_transformation"):
            return
        rows = connection.execute(
            "SELECT method_code, method_name, "
            "grid_param_code, grid_param_name, grid_name, "
            "grid2_param_code, grid2_param_name, grid2_name "
            "FROM grid_transformation WHERE deprecated = 0 AND grid_name IS NOT NULL"
        )
        for (
            method_code,
            method_name,
            code,
            parameter,
            grid,
            code2,
            parameter2,
            grid2,
        ) in rows:
            files = [(str(code), str(parameter), str(grid))]
            if grid2:
                files.append((str(code2), str(parameter2), str(grid2)))
            dataset = GridDataset(
                method_code=str(method_code),
                method_name=str(method_name),
                files=tuple(files),
            )
            # Every file of one transformation shares a name in practice, but
            # each is indexed so that a reference to either one resolves.
            for _, _, file_name in files:
                found.setdefault(_grid_key(file_name), set()).add(dataset)

        if not _has_table(connection, "grid_alternatives"):
            return
        aliases: dict[str, set[GridDataset]] = {}
        for original, current, legacy in connection.execute(
            "SELECT original_grid_name, proj_grid_name, old_proj_grid_name "
            "FROM grid_alternatives"
        ):
            datasets = found.get(_grid_key(original), ())
            if current:
                key, read = _grid_key(original), _grid_key(current)
                # Names that normalize alike but read different files stay apart.
                reads[key] = read if reads.get(key, read) == read else key
            for alias in (current, legacy):
                if alias:
                    aliases.setdefault(_grid_key(alias), set()).update(datasets)
        for key, datasets in aliases.items():
            found.setdefault(key, set()).update(datasets)
