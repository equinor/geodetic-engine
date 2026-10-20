"""OSDU record ids, recorded against the objects they name.

A caller holding OSDU data names a CRS by the id of the record that publishes
it, ``opendes:reference-data--CoordinateReferenceSystem:Projected:EPSG::23031``,
not by ``EPSG:23031``. So every record in the catalogue has its id recorded
against the object it names. That includes the EPSG records the base proj.db
already defines and this build leaves alone: those are most of the ids a
caller will use.

The id is recorded without its data partition, which differs between operators
while the rest of the id does not. It is not decoded from its spelling. The
record's own ``CodeSpace`` and ``Code`` decide what it names, since OSDU's
convention for spelling ids is good practice, not a guarantee.

An id that names nothing in the database is reported, with the reason, rather
than recorded against a guess. An id the catalogue publishes for two different
objects is reported and recorded for neither.

This runs after every object has been written, because the identifier table,
like PROJ's ``alias_name``, refuses a row for an object that does not exist.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from pathlib import Path
from typing import Any

from geodetic_engine.geodesy.osdu_id import SCHEME
from geodetic_engine.osdudb import translate as tr
from geodetic_engine.osdudb.catalog import (
    BOUND_CRS,
    COMPOUND_CRS,
    ENGINEERING_CRS,
    GEODETIC_CRS,
    PROJECTED_CRS,
    VERTICAL_CRS,
    Record,
)
from geodetic_engine.osdudb.context import OsduBuildContext
from geodetic_engine.projdb.schema import OBJECT_TABLE_NAME

logger = logging.getLogger(__name__)

# The tables a CRS record's object can be in, by CoordinateReferenceSystemType.
_CRS_TABLES: dict[str, tuple[str, ...]] = {
    GEODETIC_CRS: ("geodetic_crs",),
    PROJECTED_CRS: ("projected_crs",),
    VERTICAL_CRS: ("vertical_crs",),
    COMPOUND_CRS: ("compound_crs",),
    ENGINEERING_CRS: ("engineering_crs",),
    # proj.db stores a bound CRS on the table of the CRS it wraps.
    BOUND_CRS: ("geodetic_crs", "projected_crs"),
}

_OPERATION_TABLES: tuple[str, ...] = (
    "helmert_transformation_table",
    "grid_transformation",
    "other_transformation",
    "concatenated_operation",
)


def collect_identifiers(context: OsduBuildContext) -> dict[str, Any]:
    """Record the id of every catalogue record that names an object here.

    Inactive records follow ``include_deprecated``, as they do for import,
    except that an id an earlier build recorded as active is marked inactive
    when its record now is. The configured authorities do not limit this: an
    EPSG record names an EPSG object whether or not EPSG objects are being
    imported. An object published under one id both active and inactive is
    recorded once, from its active record.

    Returns:
        The report's account of it: how many ids were recorded, how many of
        those this build wrote, how many aliases, every id that names nothing
        and why, and where the catalogue and proj.db disagree about an object
        an id names.
    """
    skipped = {(item.auth_name, item.code): item.reason for item in context.skipped}
    source = catalogue_source(context)
    unmapped: list[dict[str, str]] = []
    warnings: list[dict[str, str]] = []
    by_identifier: dict[str, list[Record]] = defaultdict(list)
    excluded: dict[str, list[Record]] = defaultdict(list)

    for record in context.catalog.records():
        if tr.is_deprecated(record.data) and not context.config.include_deprecated:
            if record.osdu_id is not None:
                excluded[record.osdu_id].append(record)
            continue
        if record.osdu_id is None:
            unmapped.append(_entry(record, "the record states no id"))
            continue
        by_identifier[record.osdu_id].append(record)

    rows: list[dict[str, Any]] = []
    ambiguous: list[tuple[str, str, str]] = []
    for identifier, records in by_identifier.items():
        # Before asking whether the object is here: an id published for two
        # objects is ambiguous even when only one of them was imported. The
        # inactive flag is not part of what an id names, so one object
        # published both active and inactive is not ambiguous.
        targets = {(record.auth_name, record.code) for record in records}
        if len(targets) > 1:
            named = ", ".join(sorted(f"{auth}:{code}" for auth, code in targets))
            for record in records:
                unmapped.append(
                    _entry(
                        record,
                        f"the catalogue publishes {identifier!r} for more than "
                        f"one object ({named}), so it is recorded for none",
                    )
                )
            ambiguous.append(
                (
                    SCHEME,
                    identifier,
                    f"the catalogue publishes it for more than one object ({named})",
                )
            )
            continue
        # An active record speaks for the object over an inactive one.
        record = next(
            (item for item in records if not tr.is_deprecated(item.data)), records[0]
        )
        table = _table(context, record)
        if table is None:
            unmapped.append(_entry(record, _missing(context, record, skipped)))
            continue
        rows.append(
            {
                "scheme": SCHEME,
                "identifier": identifier,
                "table_name": OBJECT_TABLE_NAME[table],
                "auth_name": record.auth_name,
                "code": record.code,
                "deprecated": tr.deprecated_flag(record.data),
                "source": source,
            }
        )
        context.recorded_identifiers[identifier] = record
        warnings.extend(_drift(context, record, table))

    retired = _retired(context, excluded, by_identifier, source)
    rows.extend(retired)

    # An earlier build may have recorded an id this catalogue makes ambiguous;
    # left alone, it would go on resolving while the report says it does not.
    context.writer.remove_identifiers(ambiguous)
    written, aliased = context.writer.write_identifiers(
        rows, aliases=context.config.osdu_id_aliases
    )
    logger.info(
        "%s ids: %d recorded (%d written by this build, %d aliases), %d naming "
        "nothing in this database",
        SCHEME,
        len(rows),
        written,
        aliased,
        len(unmapped),
    )
    for item in unmapped:
        logger.debug(
            "%s id %s not recorded: %s", SCHEME, item["identifier"], item["reason"]
        )
    return {
        "scheme": SCHEME,
        "mapped": len(rows),
        "written": written,
        "aliases_written": aliased,
        "marked_inactive": len(retired),
        "unmapped": unmapped,
        "warnings": warnings,
    }


def _retired(
    context: OsduBuildContext,
    excluded: dict[str, list[Record]],
    included: dict[str, list[Record]],
    source: str,
) -> list[dict[str, Any]]:
    """Ids recorded as active whose records the catalogue now marks inactive.

    With ``include_deprecated`` off an inactive record is left out, but an id
    an earlier build recorded for it would otherwise go on resolving as
    active. Only the flag is brought up to date, which like any change to a
    recorded id needs ``overwrite_rows``: the id keeps naming the object it
    did, and one whose inactive records name another object is left alone.
    """
    rows: list[dict[str, Any]] = []
    for identifier, records in excluded.items():
        if identifier in included:
            continue
        recorded = context.writer.recorded_identifier(SCHEME, identifier)
        if recorded is None or recorded[3]:
            continue
        table_name, auth_name, code, _ = recorded
        if any(
            (record.auth_name, record.code) != (auth_name, code) for record in records
        ):
            continue
        rows.append(
            {
                "scheme": SCHEME,
                "identifier": identifier,
                "table_name": table_name,
                "auth_name": auth_name,
                "code": code,
                "deprecated": 1,
                "source": source,
            }
        )
    return rows


def _table(context: OsduBuildContext, record: Record) -> str | None:
    """The table the object a record names is in, or None when it is absent."""
    tables = (
        _OPERATION_TABLES if record.is_operation else _CRS_TABLES.get(record.type, ())
    )
    for table in tables:
        if not context.is_new(table, record.auth_name, record.code):
            return table
    return None


def _missing(
    context: OsduBuildContext,
    record: Record,
    skipped: dict[tuple[str, str], str],
) -> str:
    """Why the object a record names is not in the database."""
    if (reason := skipped.get((record.auth_name, record.code))) is not None:
        return f"this build left {record.auth_name}:{record.code} out: {reason}"
    allowed = {name.casefold() for name in context.config.authorities}
    if record.auth_name.casefold() not in allowed:
        return (
            f"{record.auth_name}:{record.code} is not in the base proj.db, and "
            f"{record.auth_name} is not among the authorities this build imports "
            f"{sorted(context.config.authorities)}"
        )
    if record.type not in _CRS_TABLES and not record.is_operation:
        return f"a {record.type} record names no kind of object proj.db stores"
    return f"{record.auth_name}:{record.code} was not imported"


def _drift(
    context: OsduBuildContext, record: Record, table: str
) -> list[dict[str, str]]:
    """Where the catalogue and proj.db disagree about the object an id names.

    Reported, never acted on: the object stays as proj.db defines it, and the
    id names it all the same. A catalogue and the EPSG dataset PROJ ships are
    rarely at the same version, so an EPSG rename is the usual cause.
    """
    found = context.writer.connection.execute(
        f"SELECT name, deprecated FROM {table} WHERE auth_name = ? AND code = ?",
        (record.auth_name, record.code),
    ).fetchone()
    if found is None:
        return []
    name, deprecated = found
    warnings: list[dict[str, str]] = []
    if record.name != "unknown" and str(name) != record.name:
        warnings.append(
            {
                "identifier": str(record.osdu_id),
                "warning": (
                    f"{record.auth_name}:{record.code} is named {name!r} in "
                    f"proj.db but {record.name!r} in the catalogue"
                ),
            }
        )
    if deprecated and not tr.is_deprecated(record.data):
        warnings.append(
            {
                "identifier": str(record.osdu_id),
                "warning": (
                    f"{record.auth_name}:{record.code} is deprecated in proj.db "
                    "but active in the catalogue"
                ),
            }
        )
    return warnings


def _entry(record: Record, reason: str) -> dict[str, str]:
    """One id the report lists as naming nothing."""
    return {
        "identifier": record.osdu_id or "",
        "auth_name": record.auth_name,
        "code": record.code,
        "name": record.name,
        "reason": reason,
    }


def catalogue_source(context: OsduBuildContext) -> str:
    """What the identifiers were read from, recorded on every row."""
    path = Path(context.catalog.path or context.config.catalog)
    version = context.config.catalog_version
    return f"{path.name} ({version})" if version else path.name
