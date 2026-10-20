"""OSDU persistableReference strings, recorded against the records publishing them.

Every CRS and transformation record in an OSDU catalogue states its definition
twice: as WKT2, which the build reads objects from, and as a
``PersistableReference``, the ESRI WKT envelope OSDU data carries wherever it
names a CRS or a transformation. A caller writing OSDU data needs the latter,
exactly as the catalogue publishes it, for EPSG objects the base proj.db
already defines as much as for the objects this build adds.

So the reference of every record whose id was recorded is stored verbatim
against that id. The id already names one object in the database; the
reference reaches it through the id rather than naming it again, and the
table's foreign key refuses a reference for an id that is not recorded.

A catalogue is curated by hand and not every record carries a usable
reference. None of this fails a build:

* A record that states none is listed as ``missing``. ESRI has no geocentric
  CRS, so OSDU publishes no reference for one, and such records are expected.
* One that cannot be read, or states a transformation for a CRS or the
  reverse, is not recorded and is listed as ``invalid`` with the reason.
* One that is readable but disagrees with its record -- an ``authCode`` naming
  a different object, or an early bound reference on a CRS that is not bound --
  is recorded as published and listed under ``warnings``. The authority code in
  a reference is provenance, not identity, and the string is the catalogue's
  word.

This runs after the identifiers have been recorded, because of that foreign
key.
"""

from __future__ import annotations

import hashlib
import logging
from collections import defaultdict
from collections.abc import Iterator
from typing import Any

from geodetic_engine.geodesy.osdu_id import SCHEME
from geodetic_engine.osdudb import translate as tr
from geodetic_engine.osdudb.catalog import (
    BOUND_CRS,
    CONCATENATED_OPERATION,
    TRANSFORMATION,
    Record,
)
from geodetic_engine.osdudb.context import OsduBuildContext
from geodetic_engine.osdudb.identifier import catalogue_source
from geodetic_engine.persistablereference import esriwkt
from geodetic_engine.persistablereference.envelope import (
    Envelope,
    Kind,
    decode,
    json_object,
)
from geodetic_engine.persistablereference.errors import (
    MalformedReferenceError,
    PersistableReferenceError,
)
from geodetic_engine.persistablereference.reference import (
    parse_persistable_reference,
)

logger = logging.getLogger(__name__)

# The field of a record's data the reference is published under.
FIELD = "PersistableReference"

_CRS_KINDS = frozenset({Kind.LATE_BOUND_CRS, Kind.EARLY_BOUND_CRS})
_OPERATION_KINDS = frozenset({Kind.TRANSFORMATION, Kind.CONCATENATED_TRANSFORMATION})

# Members holding a nested reference, which may be published as a JSON string.
_NESTED_MEMBERS = frozenset({"lateboundcrs", "singlect", "compoundct", "cts"})

# The kind a record of each type is expected to state. A CRS type absent here
# expects a late bound CRS.
_EXPECTED_KIND: dict[str, Kind] = {
    BOUND_CRS: Kind.EARLY_BOUND_CRS,
    TRANSFORMATION: Kind.TRANSFORMATION,
    CONCATENATED_OPERATION: Kind.CONCATENATED_TRANSFORMATION,
}


class _Unusable(Exception):
    """A reference that cannot be recorded, and why."""


def collect_persistable_references(context: OsduBuildContext) -> dict[str, Any]:
    """Record the persistableReference of every record whose id was recorded.

    Inactive records follow ``include_deprecated``, as their ids do.

    Returns:
        The report's account of it: whether recording was enabled, how many
        references are recorded and how many of those this build wrote, how
        many records published one under an id that names nothing here, and
        every record whose reference is missing, could not be used, or was
        recorded despite a disagreement.
    """
    if not context.config.osdu_persistable_references:
        logger.info("persistable references not recorded: disabled")
        return {"enabled": False}

    source = catalogue_source(context)
    missing: list[dict[str, str]] = []
    invalid: list[dict[str, str]] = []
    warnings: list[dict[str, str]] = []
    rows: list[dict[str, Any]] = []
    unlinked = 0
    published: dict[str, list[Record]] = defaultdict(list)

    for record in context.catalog.records():
        if tr.is_deprecated(record.data) and not context.config.include_deprecated:
            continue
        if record.osdu_id is None or record.osdu_id not in context.recorded_identifiers:
            if record.data.get(FIELD) not in (None, ""):
                unlinked += 1
            continue
        published[record.osdu_id].append(record)

    for identifier, records in published.items():
        record = context.recorded_identifiers[identifier]
        stated = {
            raw
            for item in records
            if isinstance(raw := item.data.get(FIELD), str) and raw.strip()
        }
        try:
            if len(stated) > 1:
                raise _Unusable(
                    f"the catalogue publishes {len(stated)} different "
                    f"persistableReferences under {identifier!r}, so none is "
                    "recorded"
                )
            raw = next(iter(stated)) if stated else _payload(record)
            if raw is None:
                missing.append(_entry(record, identifier, _absence(record)))
                continue
            envelope = _envelope(record, raw)
        except _Unusable as unusable:
            invalid.append(_entry(record, identifier, str(unusable)))
            continue
        warnings.extend(
            {"identifier": identifier, "warning": warning}
            for warning in _disagreements(record, envelope)
        )
        rows.append(_row(identifier, raw, envelope, source))

    missing, invalid, kept, removed = _reconcile(context, missing, invalid, warnings)
    written, unchanged = context.writer.write_persistable_references(rows)
    logger.info(
        "persistable references: %d recorded (%d written by this build, %d "
        "unchanged), %d missing, %d not usable, %d with a warning, %d kept from "
        "an earlier build, %d removed",
        len(rows),
        written,
        unchanged,
        len(missing),
        len(invalid),
        len(warnings),
        kept,
        removed,
    )
    for item in missing:
        logger.debug("no persistable reference for %s", item["identifier"])
    for item in invalid:
        logger.debug(
            "persistable reference of %s not recorded: %s",
            item["identifier"],
            item["reason"],
        )
    return {
        "enabled": True,
        "recorded": len(rows),
        "written": written,
        "unchanged": unchanged,
        "kept": kept,
        "removed": removed,
        "unlinked": unlinked,
        "missing": missing,
        "invalid": invalid,
        "warnings": warnings,
    }


def _reconcile(
    context: OsduBuildContext,
    missing: list[dict[str, str]],
    invalid: list[dict[str, str]],
    warnings: list[dict[str, str]],
) -> tuple[list[dict[str, str]], list[dict[str, str]], int, int]:
    """Settle the references an earlier build recorded that this one would not.

    Appending a catalogue that no longer states a usable reference for a
    record would otherwise leave the earlier one in place while the report
    said none was recorded. With ``overwrite_rows`` the newer catalogue wins
    and the earlier reference is removed, as a changed one would be replaced.
    Without it the earlier reference is kept, as a changed one would be, and
    the record is reported among the warnings rather than as having none.

    Returns:
        The records still without a reference, missing and invalid; how many
        earlier references were kept; and how many were removed.
    """
    overwrite = context.config.overwrite_rows
    stale: list[tuple[str, str]] = []
    kept = 0
    settled: list[list[dict[str, str]]] = []
    for entries, described in (
        (missing, "states no persistableReference for it"),
        (invalid, "states one this build cannot use"),
    ):
        remaining: list[dict[str, str]] = []
        for item in entries:
            identifier = item["identifier"]
            if not context.writer.persistable_reference_recorded(SCHEME, identifier):
                remaining.append(item)
            elif overwrite:
                stale.append((SCHEME, identifier))
                remaining.append(
                    item
                    | {
                        "reason": f"{item['reason']}; the persistableReference "
                        "an earlier build recorded for it was removed"
                    }
                )
            else:
                kept += 1
                warnings.append(
                    {
                        "identifier": identifier,
                        "warning": (
                            f"the catalogue {described} ({item['reason']}), so "
                            "the persistableReference an earlier build recorded "
                            "is kept; --overwrite-rows removes it"
                        ),
                    }
                )
        settled.append(remaining)
    removed = context.writer.remove_persistable_references(stale)
    return settled[0], settled[1], kept, removed


def _payload(record: Record) -> str | None:
    """The reference a record states, or None when it states none."""
    raw = record.data.get(FIELD)
    if raw is None or (isinstance(raw, str) and not raw.strip()):
        return None
    if not isinstance(raw, str):
        raise _Unusable(
            f"{FIELD} is a JSON {type(raw).__name__} rather than the string a "
            "persistableReference is published as"
        )
    return raw


def _absence(record: Record) -> str:
    """How a record states no reference."""
    if FIELD not in record.data:
        return f"the record states no {FIELD}"
    return f"the record's {FIELD} is empty"


def _envelope(record: Record, raw: str) -> Envelope:
    """Read a reference far enough to know it is one, and of the right kind.

    A reference this package reads but would not compute with -- a vertical
    transformation, say -- is still the catalogue's reference for the record,
    so only one that is malformed is refused.
    """
    try:
        envelope = decode(raw)
    except PersistableReferenceError as error:
        raise _Unusable(f"it cannot be read: {error}") from error
    try:
        parse_persistable_reference(raw)
    except MalformedReferenceError as error:
        raise _Unusable(f"it is malformed: {error}") from error
    except PersistableReferenceError:
        pass
    try:
        for wkt in _wkt(envelope.data):
            esriwkt.read(wkt)
    except MalformedReferenceError as error:
        raise _Unusable(f"its WKT is malformed: {error}") from error

    expected = _OPERATION_KINDS if record.is_operation else _CRS_KINDS
    if envelope.kind not in expected:
        raise _Unusable(
            f"it states a {_described(envelope.kind)} ({envelope.kind.value}), but "
            f"the record publishes a {record.type}"
        )
    return envelope


def _disagreements(record: Record, envelope: Envelope) -> Iterator[str]:
    """Where a usable reference and its record disagree."""
    expected = _EXPECTED_KIND.get(
        record.type,
        Kind.TRANSFORMATION if record.is_operation else Kind.LATE_BOUND_CRS,
    )
    if envelope.kind is not expected:
        yield (
            f"the record publishes a {record.type}, but its persistableReference "
            f"states a {_described(envelope.kind)} ({envelope.kind.value})"
        )
    stated = envelope.authority_code
    if stated is None:
        yield "its persistableReference states no authCode"
    elif (stated.authority.casefold(), stated.code) != (
        record.auth_name.casefold(),
        record.code,
    ):
        yield (
            f"its persistableReference states authCode {stated}, but the record "
            f"is {record.auth_name}:{record.code}"
        )


def _row(identifier: str, raw: str, envelope: Envelope, source: str) -> dict[str, Any]:
    """The row recording one reference, verbatim."""
    stated = envelope.authority_code
    return {
        "scheme": SCHEME,
        "identifier": identifier,
        "kind": envelope.kind.value,
        "name": envelope.name or None,
        "stated_auth_name": stated.authority if stated else None,
        "stated_code": stated.code if stated else None,
        "version": envelope.version or None,
        "sha256": hashlib.sha256(raw.encode("utf-8")).hexdigest(),
        "persistable_reference": raw,
        "source": source,
    }


def _wkt(data: Any, *, nested: bool = False) -> Iterator[str]:
    """Every ESRI WKT a reference carries, at any depth.

    A nested reference published as a JSON string is decoded and walked too.

    Raises:
        MalformedReferenceError: If such a string is not a readable object.
    """
    if isinstance(data, str):
        if nested:
            yield from _wkt(json_object(data))
    elif isinstance(data, dict):
        for key, value in data.items():
            folded = key.casefold()
            if folded == "wkt" and isinstance(value, str):
                yield value
            else:
                yield from _wkt(value, nested=folded in _NESTED_MEMBERS)
    elif isinstance(data, list):
        for value in data:
            yield from _wkt(value, nested=nested)


def _described(kind: Kind) -> str:
    """A kind of reference, in words."""
    return {
        Kind.LATE_BOUND_CRS: "late bound CRS",
        Kind.EARLY_BOUND_CRS: "early bound CRS",
        Kind.TRANSFORMATION: "single transformation",
        Kind.CONCATENATED_TRANSFORMATION: "concatenated transformation",
        Kind.UNIT_SCALE_OFFSET: "unit",
        Kind.UNIT_ABCD: "unit",
    }[kind]


def _entry(record: Record, identifier: str, reason: str) -> dict[str, str]:
    """One record the report lists as having no recorded reference."""
    return {
        "identifier": identifier,
        "auth_name": record.auth_name,
        "code": record.code,
        "name": record.name,
        "type": record.type,
        "reason": reason,
    }
