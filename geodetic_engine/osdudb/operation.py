"""Coordinate transformations from an OSDU catalogue.

An OSDU record names the operation's method, its source CRS and its target CRS,
but states the parameter values only inside the record's WKT. Which proj.db
table the operation belongs in follows from those parameters and not from the
method code, so the decision is left to
:mod:`geodetic_engine.projdb.parameters`, which the Georepository workflow uses
for the same purpose.
"""

from __future__ import annotations

import logging
from typing import Any

from geodetic_engine.osdudb import definition as df
from geodetic_engine.osdudb import translate as tr
from geodetic_engine.osdudb.catalog import (
    CONCATENATED_OPERATION,
    TRANSFORMATION,
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

# Tables a transformation's source or target CRS may live in.
CRS_TABLES = (
    "geodetic_crs",
    "projected_crs",
    "vertical_crs",
    "compound_crs",
    "engineering_crs",
)

# Tables a step of a concatenated operation may live in.
OPERATION_TABLES = (
    "helmert_transformation_table",
    "grid_transformation",
    "other_transformation",
    "conversion_table",
    "concatenated_operation",
)


def collect_transformations(context: OsduBuildContext) -> None:
    """Import transformations, routed by parameter shape to the right table."""
    counts: dict[str, int] = {}
    for record in context.candidates(TRANSFORMATION):
        try:
            operation = df.parse_operation(tr.wkt(record.data), record.described)
            described = f"transformation {record.auth_name}:{record.code}"
            parameters = df.parameters_of(operation, context.units, described)
            table = pm.classify(parameters)
            if not context.should_import(table, record.auth_name, record.code):
                continue

            method = _method(context, record, operation)
            source = _crs_reference(context, record, "SourceCRS", operation=operation)
            target = _crs_reference(context, record, "TargetCRS", operation=operation)
        except ProjDbBuildError as exc:
            context.skip(pm.OTHER_TABLE, record, str(exc))
            continue

        row = _common(record) | {
            "method_auth_name": method[0],
            "method_code": method[1],
            "source_crs_auth_name": source[0],
            "source_crs_code": source[1],
            "target_crs_auth_name": target[0],
            "target_crs_code": target[1],
            "accuracy": tr.number(record.data, "Accuracy"),
            "operation_version": tr.text(record.data, "CoordTfmVersion"),
        }
        if table == pm.HELMERT_TABLE:
            # helmert_transformation_table has no method_name column.
            row |= pm.helmert_columns(parameters)
        else:
            row["method_name"] = tr.text(record.data.get("Method") or {}, "Name") or ""
            row |= (
                pm.grid_columns(parameters)
                if table == pm.GRID_TABLE
                else pm.other_columns(parameters)
            )

        interpolation = df.identifier_of(
            operation.to_json_dict().get("interpolation_crs")
        )
        declared_interpolation = tr.authority_code(record.data.get("InterpolationCRS"))
        if declared_interpolation != (None, None) and (
            interpolation is None
            or declared_interpolation != (interpolation.auth_name, interpolation.code)
        ):
            context.skip(table, record, "interpolation CRS contradicts the WKT")
            continue
        if interpolation is not None:
            if table == pm.HELMERT_TABLE:
                context.skip(
                    table, record, "Helmert table cannot preserve an interpolation CRS"
                )
                continue
            row |= {
                "interpolation_crs_auth_name": interpolation.auth_name,
                "interpolation_crs_code": interpolation.code,
            }

        context.stage([(table, row)])
        context.annotate(
            ObjectKey(table=table, auth_name=record.auth_name, code=record.code),
            record,
        )
        counts[table] = counts.get(table, 0) + 1

    for table in (pm.HELMERT_TABLE, pm.GRID_TABLE, pm.OTHER_TABLE):
        logger.info("%s: %d read", table, counts.get(table, 0))


def collect_concatenated(context: OsduBuildContext) -> None:
    """Import concatenated operations and their ordered steps.

    A concatenated operation without its steps is not a transformation, so an
    operation whose steps cannot all be resolved is skipped rather than written
    with a partial chain.
    """
    count = 0
    table = "concatenated_operation"
    for record in context.candidates(CONCATENATED_OPERATION):
        if not context.should_import(table, record.auth_name, record.code):
            continue
        try:
            source = _crs_reference(context, record, "SourceCRS")
            target = _crs_reference(context, record, "TargetCRS")
            steps = _steps(context, record)
        except ProjDbBuildError as exc:
            context.skip(table, record, str(exc))
            continue

        staged: list[Staged] = [
            (
                table,
                _common(record)
                | {
                    "source_crs_auth_name": source[0],
                    "source_crs_code": source[1],
                    "target_crs_auth_name": target[0],
                    "target_crs_code": target[1],
                    "accuracy": tr.number(record.data, "Accuracy"),
                    "operation_version": tr.text(record.data, "CoordTfmVersion"),
                },
            )
        ]
        staged.extend(("concatenated_operation_step", step) for step in steps)
        context.stage(staged)
        context.annotate(
            ObjectKey(table=table, auth_name=record.auth_name, code=record.code),
            record,
        )
        count += 1
    logger.info("concatenated operations: %d read", count)


def _common(record: Record) -> dict[str, Any]:
    """The columns every operation table shares."""
    return {
        "auth_name": record.auth_name,
        "code": record.code,
        "name": record.name,
        "description": tr.text(record.data, "Description"),
        "deprecated": tr.deprecated_flag(record.data),
    }


def _method(
    context: OsduBuildContext, record: Record, operation: Any
) -> tuple[str, str]:
    """Return the operation's EPSG method, preferring what the record states.

    Raises:
        UnreadableDefinitionError: If no method is stated, or the method is one
            this PROJ build cannot evaluate.
    """
    auth, code = tr.authority_code(record.data.get("Method"))
    from_wkt = df.method_of(operation)
    if auth and code is not None and from_wkt != df.Identifier(auth, code):
        raise UnreadableDefinitionError(
            f"{record.described} method contradicts the WKT"
        )
    if not auth or code is None:
        if from_wkt is None:
            raise UnreadableDefinitionError(f"{record.described} states no method")
        auth, code = from_wkt.auth_name, from_wkt.code
    try:
        unsupported = int(code) in context.config.unsupported_method_codes
    except ValueError:
        unsupported = False
    if unsupported:
        raise UnreadableDefinitionError(
            f"operation method {code} is not supported by this PROJ build"
        )
    return auth, str(code)


def _crs_reference(
    context: OsduBuildContext, record: Record, field: str, *, operation: Any = None
) -> tuple[str, str]:
    """Resolve a transformation's source or target CRS across the CRS tables."""
    auth, code = tr.authority_code(record.data.get(field))
    if operation is not None:
        key = "source_crs" if field == "SourceCRS" else "target_crs"
        identifier = df.identifier_of(operation.to_json_dict().get(key))
        if identifier is None or (identifier.auth_name, identifier.code) != (
            auth,
            code,
        ):
            raise UnreadableDefinitionError(
                f"{record.described} {field} contradicts or is not "
                "identified by the WKT"
            )
    for table in CRS_TABLES:
        if auth and code is not None and not context.is_new(table, auth, code):
            return auth, str(code)
    raise MissingReferencedObjectError(
        f"{record.described} references {field} {auth}:{code}, which is in "
        "neither the base proj.db nor this catalogue"
    )


def _steps(context: OsduBuildContext, record: Record) -> list[dict[str, Any]]:
    """Resolve the ordered steps of a concatenated operation.

    Raises:
        MissingReferencedObjectError: If the operation has no steps, or a step
            names an operation that is not available.
    """
    listed = record.data.get("ConcatenatedTransformations") or []
    if not listed:
        raise MissingReferencedObjectError(f"{record.described} has no steps")

    steps = []
    for number, step in enumerate(listed, start=1):
        auth, code = tr.authority_code(step)
        table = next(
            (
                table
                for table in OPERATION_TABLES
                if auth and code is not None and not context.is_new(table, auth, code)
            ),
            None,
        )
        if table is None:
            raise MissingReferencedObjectError(
                f"{record.described} step {number} references operation "
                f"{auth}:{code}, which is not available"
            )
        steps.append(
            {
                "operation_auth_name": record.auth_name,
                "operation_code": record.code,
                "step_number": number,
                "step_auth_name": auth,
                "step_code": str(code),
                "step_direction": None,
            }
        )
    return steps
