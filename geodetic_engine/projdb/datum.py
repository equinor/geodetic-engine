"""Units, ellipsoids, prime meridians and datums.

Datum epochs are first class here. A dynamic datum's frame reference epoch, an
engineering datum's anchor epoch and a datum ensemble's accuracy are all carried
through to proj.db; dropping any of them turns a time-dependent datum into a
static one that silently answers with the wrong coordinates.
"""

from __future__ import annotations

import logging
from typing import Any

from geodetic_engine.projdb import translate as tr
from geodetic_engine.projdb.context import BuildContext
from geodetic_engine.projdb.errors import MissingReferencedObjectError

logger = logging.getLogger(__name__)

UNIT_TABLE = "unit_of_measure"
ELLIPSOID_TABLE = "ellipsoid"
PRIME_MERIDIAN_TABLE = "prime_meridian"

# Georepository datum Type values mapped onto the proj.db table that stores them.
_DATUM_TABLE_BY_TYPE = {
    "geodetic": "geodetic_datum",
    "vertical": "vertical_datum",
    "engineering": "engineering_datum",
    "ensemble": "geodetic_datum",
}


def collect_units(context: BuildContext) -> None:
    """Import units of measure defined by the configured authorities."""
    rows: list[dict[str, Any]] = []
    for summary in context.client.iter_collection(
        "Unit", authorities=context.config.authorities
    ):
        obj = context.client.detail(summary)
        auth, code = tr.auth_name(obj), tr.code(obj)
        if not context.is_new(UNIT_TABLE, auth, code):
            continue
        assert code is not None
        factor_b = tr.number(obj, "FactorB")
        factor_c = tr.number(obj, "FactorC")
        rows.append(
            {
                "auth_name": auth,
                "code": code,
                "name": tr.text(obj, "Name") or "unknown",
                "type": _unit_type(obj),
                # PROJ stores a single conversion factor to the base unit.
                "conv_factor": (
                    factor_b / factor_c
                    if factor_b is not None and factor_c
                    else factor_b
                ),
                "proj_short_name": None,
                "deprecated": tr.deprecated_flag(obj),
            }
        )
        context.record(UNIT_TABLE, auth, code)
    context.writer.insert(UNIT_TABLE, rows)
    logger.info("units: %d imported", len(rows))


def collect_ellipsoids(context: BuildContext) -> None:
    """Import ellipsoids, keeping semi-minor axis and inverse flattening apart.

    An ellipsoid is defined by either the semi-minor axis or the inverse
    flattening, not both. Whichever the authority states is stored; the other is
    left NULL rather than derived, so PROJ applies its own conversion at the
    precision it needs.
    """
    rows: list[dict[str, Any]] = []
    for summary in context.client.iter_collection(
        "Ellipsoid", authorities=context.config.authorities
    ):
        obj = context.client.detail(summary)
        auth, code = tr.auth_name(obj), tr.code(obj)
        if not context.is_new(ELLIPSOID_TABLE, auth, code):
            continue
        assert code is not None

        semi_major = tr.number(obj, "SemiMajorAxis")
        if semi_major is None or semi_major <= 0:
            context.skip(
                ELLIPSOID_TABLE,
                auth,
                code,
                obj,
                "missing or non-positive semi-major axis",
            )
            continue
        inv_flattening = tr.number(obj, "InverseFlattening")
        semi_minor = tr.number(obj, "SemiMinorAxis")
        unit_auth, unit_code = context.resolve_link(
            obj.get("Unit"),
            tables="unit_of_measure",
            referenced_by=f"ellipsoid {auth}:{code}",
        )
        rows.append(
            {
                "auth_name": auth,
                "code": code,
                "name": tr.text(obj, "Name") or "unknown",
                "description": tr.text(obj, "Remark", "Description"),
                "celestial_body_auth_name": "PROJ",
                "celestial_body_code": "EARTH",
                "semi_major_axis": semi_major,
                "uom_auth_name": unit_auth,
                "uom_code": unit_code,
                "inv_flattening": inv_flattening,
                "semi_minor_axis": None if inv_flattening else semi_minor,
                "deprecated": tr.deprecated_flag(obj),
            }
        )
        context.record(ELLIPSOID_TABLE, auth, code)
    context.writer.insert(ELLIPSOID_TABLE, rows)
    logger.info("ellipsoids: %d imported", len(rows))


def collect_prime_meridians(context: BuildContext) -> None:
    """Import prime meridians, keeping the longitude in its declared unit."""
    rows: list[dict[str, Any]] = []
    for summary in context.client.iter_collection(
        "PrimeMeridian", authorities=context.config.authorities
    ):
        obj = context.client.detail(summary)
        auth, code = tr.auth_name(obj), tr.code(obj)
        if not context.is_new(PRIME_MERIDIAN_TABLE, auth, code):
            continue
        assert code is not None
        unit_auth, unit_code = context.resolve_link(
            obj.get("Unit"),
            tables="unit_of_measure",
            referenced_by=f"prime meridian {auth}:{code}",
        )
        rows.append(
            {
                "auth_name": auth,
                "code": code,
                "name": tr.text(obj, "Name") or "unknown",
                "longitude": tr.number(obj, "GreenwichLongitude"),
                "uom_auth_name": unit_auth,
                "uom_code": unit_code,
                "deprecated": tr.deprecated_flag(obj),
            }
        )
        context.record(PRIME_MERIDIAN_TABLE, auth, code)
    context.writer.insert(PRIME_MERIDIAN_TABLE, rows)
    logger.info("prime meridians: %d imported", len(rows))


def collect_datums(context: BuildContext) -> None:
    """Import geodetic, vertical and engineering datums, including ensembles.

    Datum ensembles are fetched from a separate endpoint but stored in
    ``geodetic_datum`` with an ``ensemble_accuracy``, which is how PROJ models
    them.
    """
    by_table: dict[str, list[dict[str, Any]]] = {
        "geodetic_datum": [],
        "vertical_datum": [],
        "engineering_datum": [],
    }

    for endpoint, forced_type in (("Datum", None), ("DatumEnsemble", "ensemble")):
        for summary in context.client.iter_collection(
            endpoint, authorities=context.config.authorities
        ):
            obj = context.client.detail(summary)
            auth, code = tr.auth_name(obj), tr.code(obj)
            if code is None:
                continue
            datum_type = forced_type or str(obj.get("Type") or "").strip().casefold()
            table = _DATUM_TABLE_BY_TYPE.get(datum_type)
            if table is None:
                context.skip(
                    "geodetic_datum",
                    auth,
                    code,
                    obj,
                    f"unsupported datum type {datum_type!r}",
                )
                continue
            if not context.is_new(table, auth, code):
                continue

            row = _datum_row(context, obj, auth, code, table, datum_type)
            if row is None:
                continue
            by_table[table].append(row)
            key = context.record(table, auth, code)
            context.annotate(key, obj)

    for table, rows in by_table.items():
        context.writer.insert(table, rows)
        logger.info("%s: %d imported", table, len(rows))


def _datum_row(
    context: BuildContext,
    obj: dict[str, Any],
    auth: str,
    code: str,
    table: str,
    datum_type: str,
) -> dict[str, Any] | None:
    common: dict[str, Any] = {
        "auth_name": auth,
        "code": code,
        "name": tr.text(obj, "Name") or "unknown",
        "publication_date": tr.text(obj, "PublicationDate"),
        "anchor": tr.text(obj, "Origin"),
        "anchor_epoch": tr.epoch(obj, "AnchorEpoch"),
        "deprecated": tr.deprecated_flag(obj),
    }

    if table == "engineering_datum":
        # engineering_datum has no description column.
        return common

    common |= {
        "description": tr.text(obj, "Remark", "Description"),
        "frame_reference_epoch": tr.epoch(obj, "FrameReferenceEpoch"),
        "ensemble_accuracy": (
            tr.number(obj, "Accuracy") if datum_type == "ensemble" else None
        ),
    }

    if table == "vertical_datum":
        return common

    ellipsoid = obj.get("Ellipsoid") or {}
    prime_meridian = obj.get("PrimeMeridian") or {}
    described = f"geodetic datum {auth}:{code}"
    try:
        ellipsoid_ref = context.resolve_link(
            ellipsoid, tables="ellipsoid", referenced_by=described
        )
        meridian_ref = context.resolve_link(
            prime_meridian, tables="prime_meridian", referenced_by=described
        )
    except MissingReferencedObjectError as exc:
        context.skip(table, auth, code, obj, str(exc))
        return None

    return common | {
        "ellipsoid_auth_name": ellipsoid_ref[0],
        "ellipsoid_code": ellipsoid_ref[1],
        "prime_meridian_auth_name": meridian_ref[0],
        "prime_meridian_code": meridian_ref[1],
    }


def _unit_type(obj: dict[str, Any]) -> str:
    raw = str(obj.get("Type") or "").strip().casefold()
    return {
        "length": "length",
        "angle": "angle",
        "scale": "scale",
        "time": "time",
    }.get(raw, "unknown")
