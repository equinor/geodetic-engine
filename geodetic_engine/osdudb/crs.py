"""Coordinate reference systems from an OSDU catalogue.

Each CRS record names its coordinate system, datum and projection by authority
code but defines none of them, so importing one CRS means producing everything
it references out of its own WKT as well. A record is imported whole or not at
all: a CRS whose datum, ellipsoid or axis unit cannot be produced faithfully is
skipped and reported, never written with a dangling reference that PROJ would
later fail on or, worse, silently resolve to a different object.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from pyproj import CRS

from geodetic_engine.osdudb import definition as df
from geodetic_engine.osdudb import translate as tr
from geodetic_engine.osdudb.catalog import (
    COMPOUND_CRS,
    ENGINEERING_CRS,
    GEODETIC_CRS,
    PROJECTED_CRS,
    VERTICAL_CRS,
    Record,
)
from geodetic_engine.osdudb.context import OsduBuildContext, Staged
from geodetic_engine.osdudb.errors import (
    MissingReferencedObjectError,
    ProjDbBuildError,
    UnreadableDefinitionError,
)
from geodetic_engine.projdb import parameters as pm
from geodetic_engine.projdb.records import ObjectKey

logger = logging.getLogger(__name__)

# OSDU Kind values mapped onto the proj.db geodetic_crs type vocabulary. A
# dynamic CRS differs from a static one by its datum's reference epoch, which
# the datum row carries; proj.db has no separate CRS type for it.
GEODETIC_KINDS: dict[str, str] = {
    "geocentric": "geocentric",
    "dynamic geocentric": "geocentric",
    "geographic 2d": "geographic 2D",
    "dynamic geographic 2d": "geographic 2D",
    "geographic 3d": "geographic 3D",
    "dynamic geographic 3d": "geographic 3D",
}


@dataclass(slots=True)
class _Blocks:
    """What a CRS is built from, and the rows needed to define it.

    The identifiers are the ones the CRS row must reference. They are returned
    rather than looked up again, because an object this build has only staged
    is not yet in the database and would not be found.
    """

    coordinate_system: df.Identifier
    datum: df.Identifier
    rows: list[Staged] = field(default_factory=list)

    @property
    def system_columns(self) -> dict[str, Any]:
        """The coordinate system columns every CRS table carries."""
        return {
            "coordinate_system_auth_name": self.coordinate_system.auth_name,
            "coordinate_system_code": self.coordinate_system.code,
        }

    @property
    def datum_columns(self) -> dict[str, Any]:
        """The datum columns the CRS tables that reference one carry."""
        return {
            "datum_auth_name": self.datum.auth_name,
            "datum_code": self.datum.code,
        }


def collect_geodetic(context: OsduBuildContext) -> None:
    """Import geographic and geocentric CRSs."""
    count = 0
    for record in _candidates(context, GEODETIC_CRS, "geodetic_crs"):
        try:
            kind = GEODETIC_KINDS.get(str(record.data.get("Kind") or "").casefold())
            if kind is None:
                raise UnreadableDefinitionError(
                    f"unsupported geodetic CRS kind {record.data.get('Kind')!r}"
                )
            crs = df.parse_crs(tr.wkt(record.data), record.described)
            blocks = _building_blocks(context, record, crs, "geodetic_datum")
            staged = blocks.rows
            staged.append(
                (
                    "geodetic_crs",
                    _common(record)
                    | blocks.system_columns
                    | blocks.datum_columns
                    | {"type": kind, "text_definition": None},
                )
            )
        except ProjDbBuildError as exc:
            context.skip("geodetic_crs", record, str(exc))
            continue
        _import(context, "geodetic_crs", record, staged)
        count += 1
    logger.info("geodetic CRS: %d read", count)


def collect_projected(context: OsduBuildContext) -> None:
    """Import projected CRSs together with the conversions they apply.

    A conversion is defined only inside the WKT of the CRSs that use it, so it
    is produced here rather than by a pass of its own. Where several CRSs name
    the same conversion, the first to produce it defines it and the rest
    reference it by code.
    """
    count = 0
    for record in _candidates(context, PROJECTED_CRS, "projected_crs"):
        try:
            crs = df.parse_crs(tr.wkt(record.data), record.described)
            blocks = _building_blocks(context, record, crs, "geodetic_datum")
            staged = blocks.rows
            conversion = _conversion(context, record, crs, staged)
            base = _reference(context, record, "BaseCRS", "geodetic_crs")
            staged.append(
                (
                    "projected_crs",
                    _common(record)
                    | blocks.system_columns
                    | {
                        "geodetic_crs_auth_name": base[0],
                        "geodetic_crs_code": base[1],
                        "conversion_auth_name": conversion.auth_name,
                        "conversion_code": conversion.code,
                        "text_definition": None,
                    },
                )
            )
        except ProjDbBuildError as exc:
            context.skip("projected_crs", record, str(exc))
            continue
        _import(context, "projected_crs", record, staged)
        count += 1
    logger.info("projected CRS: %d read", count)


def collect_vertical(context: OsduBuildContext) -> None:
    """Import vertical CRSs.

    A vertical CRS derived from another one by a conversion states no datum of
    its own. proj.db has no derived CRS table, and flattening such a CRS onto
    its base datum would drop the conversion, so it is skipped and reported.
    """
    count = 0
    for record in _candidates(context, VERTICAL_CRS, "vertical_crs"):
        try:
            crs = df.parse_crs(tr.wkt(record.data), record.described)
            blocks = _building_blocks(context, record, crs, "vertical_datum")
            staged = blocks.rows
            staged.append(
                (
                    "vertical_crs",
                    _common(record) | blocks.system_columns | blocks.datum_columns,
                )
            )
        except ProjDbBuildError as exc:
            context.skip("vertical_crs", record, str(exc))
            continue
        _import(context, "vertical_crs", record, staged)
        count += 1
    logger.info("vertical CRS: %d read", count)


def collect_engineering(context: OsduBuildContext) -> None:
    """Import engineering CRSs."""
    count = 0
    for record in _candidates(context, ENGINEERING_CRS, "engineering_crs"):
        try:
            crs = df.parse_crs(tr.wkt(record.data), record.described)
            blocks = _building_blocks(context, record, crs, "engineering_datum")
            staged = blocks.rows
            staged.append(
                (
                    "engineering_crs",
                    _common(record) | blocks.system_columns | blocks.datum_columns,
                )
            )
        except ProjDbBuildError as exc:
            context.skip("engineering_crs", record, str(exc))
            continue
        _import(context, "engineering_crs", record, staged)
        count += 1
    logger.info("engineering CRS: %d read", count)


def collect_compound(context: OsduBuildContext) -> None:
    """Import compound CRSs from their horizontal and vertical components.

    Both components must already exist; a compound CRS that lost its vertical
    component would silently become a 2D CRS.
    """
    count = 0
    for record in _candidates(context, COMPOUND_CRS, "compound_crs"):
        try:
            horizontal = _horizontal_reference(context, record)
            vertical = _reference(context, record, "VerticalCRS", "vertical_crs")
        except ProjDbBuildError as exc:
            context.skip("compound_crs", record, str(exc))
            continue
        staged: list[Staged] = [
            (
                "compound_crs",
                _common(record)
                | {
                    "horiz_crs_auth_name": horizontal[0],
                    "horiz_crs_code": horizontal[1],
                    "vertical_crs_auth_name": vertical[0],
                    "vertical_crs_code": vertical[1],
                },
            )
        ]
        _import(context, "compound_crs", record, staged)
        count += 1
    logger.info("compound CRS: %d read", count)


def _candidates(context: OsduBuildContext, crs_type: str, table: str) -> list[Record]:
    """The records of one CRS type the database does not already define."""
    return [
        record
        for record in context.candidates(crs_type)
        if context.is_new(table, record.auth_name, record.code)
    ]


def _common(record: Record) -> dict[str, Any]:
    """The columns every CRS table shares."""
    return {
        "auth_name": record.auth_name,
        "code": record.code,
        "name": record.name,
        "description": tr.text(record.data, "Description"),
        "deprecated": tr.deprecated_flag(record.data),
    }


def _import(
    context: OsduBuildContext, table: str, record: Record, staged: list[Staged]
) -> None:
    """Stage a record's rows and attach its usages and aliases."""
    context.stage(staged)
    context.annotate(
        ObjectKey(table=table, auth_name=record.auth_name, code=record.code), record
    )


def _reference(
    context: OsduBuildContext, record: Record, field: str, table: str
) -> tuple[str, str]:
    """Resolve one of a record's ``AuthorityCode`` cross references."""
    auth, code = tr.authority_code(record.data.get(field))
    return context.require_reference(
        table=table, auth=auth, code=code, referenced_by=record.described
    )


def _horizontal_reference(context: OsduBuildContext, record: Record) -> tuple[str, str]:
    """Resolve a compound CRS's horizontal half, geodetic or projected."""
    auth, code = tr.authority_code(record.data.get("HorizontalCRS"))
    for table in ("geodetic_crs", "projected_crs"):
        if auth and code is not None and not context.is_new(table, auth, code):
            return auth, str(code)
    raise MissingReferencedObjectError(
        f"{record.described} references horizontal CRS {auth}:{code}, which is "
        "in neither the base proj.db nor this catalogue"
    )


def _identifier(record: Record, field: str) -> df.Identifier | None:
    """The identifier a record gives one of the objects it references."""
    auth, code = tr.authority_code(record.data.get(field))
    return df.Identifier(auth, code) if auth and code is not None else None


def _building_blocks(
    context: OsduBuildContext, record: Record, crs: CRS, datum_table: str
) -> _Blocks:
    """Produce the objects a CRS references that the database does not have.

    The coordinate system's identity comes from the OSDU record, because PROJ's
    PROJJSON export does not carry a coordinate system's code; its axes come
    from the WKT, which is the only place they are stated.
    """
    datum, rows = _datum_blocks(context, record, crs, datum_table)

    identifier = _identifier(record, "CoordinateSystem")
    if identifier is None:
        raise MissingReferencedObjectError(
            f"{record.described} states no coordinate system, so its axis order "
            "and units cannot be recorded"
        )
    if context.new_dependency("coordinate_system", identifier, record.described):
        system, axes = df.coordinate_system_rows(crs, identifier, context.units)
        rows.append(("coordinate_system", system))
        rows.extend(("axis", axis) for axis in axes)
    return _Blocks(coordinate_system=identifier, datum=datum, rows=rows)


def _datum_blocks(
    context: OsduBuildContext, record: Record, crs: CRS, expected_table: str
) -> tuple[df.Identifier, list[Staged]]:
    """Produce a CRS's datum and, for a geodetic datum, what it is built from."""
    datum = crs.datum
    if datum is None:
        raise UnreadableDefinitionError(
            f"{record.described} states no datum, so it is derived from another "
            "CRS. proj.db has no derived CRS table, and storing it on the base "
            "datum would drop the deriving conversion and shift every "
            "coordinate it produces."
        )
    table = df.datum_table(datum)
    if table != expected_table:
        raise UnreadableDefinitionError(
            f"{record.described} has a datum of type "
            f"{datum.to_json_dict().get('type')!r}, which belongs in "
            f"{table or 'no proj.db table'} rather than {expected_table}"
        )

    identifier = _agreed(record, "Datum", df.identifier_of(datum), "datum")
    if not context.new_dependency(table, identifier, record.described):
        return identifier, []

    rows: list[Staged] = []
    ellipsoid: df.Identifier | None = None
    meridian: df.Identifier | None = None
    if table == "geodetic_datum":
        ellipsoid = _stage_ellipsoid(context, record, crs, rows)
        meridian = _stage_prime_meridian(context, record, crs, rows)

    built = df.datum_row(datum, table, ellipsoid=ellipsoid, prime_meridian=meridian)
    assert built is not None  # new_dependency has already required an identifier
    rows.append((table, built[1]))
    return identifier, rows


def _agreed(
    record: Record, field: str, from_wkt: df.Identifier | None, described: str
) -> df.Identifier:
    """Reconcile the identity a record declares with the one its WKT states.

    A record whose declared datum is not the datum its own WKT defines is a
    contradiction, and resolving it either way would attach a CRS to a datum
    somebody did not mean. Both are refused instead.

    Raises:
        MissingReferencedObjectError: If neither source gives an identity, or
            the two disagree.
    """
    declared = _identifier(record, field)
    if from_wkt is None:
        if declared is None:
            raise MissingReferencedObjectError(
                f"{record.described} identifies its {described} neither in the "
                "record nor in its WKT"
            )
        return declared
    if declared is not None and (declared.auth_name, declared.code) != (
        from_wkt.auth_name,
        from_wkt.code,
    ):
        raise MissingReferencedObjectError(
            f"{record.described} declares {described} "
            f"{declared.auth_name}:{declared.code} but its WKT defines "
            f"{from_wkt.auth_name}:{from_wkt.code}"
        )
    return from_wkt


def _stage_ellipsoid(
    context: OsduBuildContext, record: Record, crs: CRS, staged: list[Staged]
) -> df.Identifier:
    """Return a datum's ellipsoid, staging it when the database lacks it."""
    identifier = df.identifier_of(crs.ellipsoid) if crs.ellipsoid else None
    if not context.new_dependency("ellipsoid", identifier, record.described):
        assert identifier is not None
        return identifier
    built = df.ellipsoid_row(crs.ellipsoid, context.units)
    assert built is not None
    staged.append(("ellipsoid", built[1]))
    return built[0]


def _stage_prime_meridian(
    context: OsduBuildContext, record: Record, crs: CRS, staged: list[Staged]
) -> df.Identifier:
    """Return a datum's prime meridian, staging it when the database lacks it."""
    identifier = df.identifier_of(crs.prime_meridian) if crs.prime_meridian else None
    if not context.new_dependency("prime_meridian", identifier, record.described):
        assert identifier is not None
        return identifier
    built = df.prime_meridian_row(crs.prime_meridian, context.units)
    assert built is not None
    staged.append(("prime_meridian", built[1]))
    return built[0]


def _conversion(
    context: OsduBuildContext, record: Record, crs: CRS, staged: list[Staged]
) -> df.Identifier:
    """Return the map projection a projected CRS applies, staging it if new."""
    conversion = crs.coordinate_operation
    if conversion is None:
        raise UnreadableDefinitionError(
            f"{record.described} states no conversion in its WKT"
        )
    identifier = _agreed(
        record, "Projection", df.identifier_of(conversion), "conversion"
    )
    if not context.new_dependency("conversion_table", identifier, record.described):
        return identifier

    method = df.method_of(conversion)
    if method is None:
        raise UnreadableDefinitionError(
            f"{record.described} has a conversion whose method carries no EPSG code"
        )
    if _is_unsupported(context, method.code):
        raise UnreadableDefinitionError(
            f"conversion method {method.code} is not supported by this PROJ build"
        )

    described = f"conversion {identifier.auth_name}:{identifier.code}"
    parameters = df.parameters_of(conversion, context.units, described)
    staged.extend(
        # conversion_param supplies the parameter names the conversion view
        # reads; it must exist before the conversion that references it.
        (
            "conversion_param",
            {"auth_name": param.auth_name, "code": param.code, "name": param.name},
        )
        for param in parameters[:7]
        if context.is_new("conversion_param", param.auth_name, param.code)
    )
    staged.append(
        (
            "conversion_table",
            {
                "auth_name": identifier.auth_name,
                "code": identifier.code,
                "name": tr.text(record.data.get("Projection") or {}, "Name")
                or str(conversion.name or "unknown"),
                "description": None,
                "method_auth_name": method.auth_name,
                "method_code": method.code,
                "deprecated": tr.deprecated_flag(record.data),
            }
            | pm.conversion_columns(parameters),
        )
    )
    return identifier


def _is_unsupported(context: OsduBuildContext, method_code: str) -> bool:
    try:
        return int(method_code) in context.config.unsupported_method_codes
    except ValueError:
        return False
