"""Coordinate conversions and transformations.

Georepository states an operation's parameters as ``ParameterValues``; this
module turns those into :class:`~geodetic_engine.projdb.parameters.Parameter`
records and leaves the decision of which proj.db table they imply, and which
columns they fill, to :mod:`geodetic_engine.projdb.parameters`.
"""

from __future__ import annotations

import logging
from collections.abc import Iterator
from typing import Any

from geodetic_engine.projdb import parameters as pm
from geodetic_engine.projdb import translate as tr
from geodetic_engine.projdb.context import BuildContext
from geodetic_engine.projdb.errors import MissingReferencedObjectError

logger = logging.getLogger(__name__)

CRS_TABLES = (
    "geodetic_crs",
    "projected_crs",
    "vertical_crs",
    "compound_crs",
    "engineering_crs",
)


def collect_conversions(context: BuildContext) -> None:
    """Import map projection conversions used by projected CRSs."""
    rows: list[dict[str, Any]] = []
    parameters: dict[tuple[str, str], dict[str, Any]] = {}
    for obj, auth, code in _candidates(context, "Conversion", "conversion_table"):
        method = obj.get("Method") or {}
        method_code = tr.link_code(method)
        if _is_unsupported(context, method_code):
            context.skip(
                "conversion_table",
                auth,
                code,
                obj,
                f"operation method {method_code} is not supported by this PROJ build",
            )
            continue
        row = {
            "auth_name": auth,
            "code": code,
            "name": tr.text(obj, "Name") or "unknown",
            "description": tr.text(obj, "Remark", "Description"),
            "method_auth_name": "EPSG",
            "method_code": method_code,
            "deprecated": tr.deprecated_flag(obj),
        }
        # conversion_table has seven parameter slots and no per-parameter name.
        parsed = _parameters(obj)
        row |= pm.conversion_columns(parsed)
        for param in parsed[:7]:
            _record_parameter(context, parameters, param)
        rows.append(row)
        _finalise(context, "conversion_table", obj, auth, code)
    # conversion_param supplies the parameter names the conversion view reads;
    # it must exist before the conversions that reference it.
    context.writer.insert("conversion_param", list(parameters.values()))
    context.writer.insert("conversion_table", rows)
    logger.info(
        "conversions: %d imported, %d parameter names added",
        len(rows),
        len(parameters),
    )


def _record_parameter(
    context: BuildContext,
    parameters: dict[tuple[str, str], dict[str, Any]],
    param: pm.Parameter,
) -> None:
    """Note a conversion parameter's name, unless proj.db already defines it."""
    if not param.code or not param.name:
        return
    if not context.is_new("conversion_param", param.auth_name, param.code):
        return
    parameters.setdefault(
        (param.auth_name, param.code),
        {"auth_name": param.auth_name, "code": param.code, "name": param.name},
    )


def collect_transformations(context: BuildContext) -> None:
    """Import transformations, routed by parameter shape to the right table."""
    by_table: dict[str, list[dict[str, Any]]] = {
        pm.HELMERT_TABLE: [],
        pm.GRID_TABLE: [],
        pm.OTHER_TABLE: [],
    }

    for summary in context.client.iter_collection(
        "Transformation", authorities=context.config.authorities
    ):
        obj = context.client.detail(summary)
        auth, code = tr.auth_name(obj), tr.code(obj)
        if code is None:
            continue
        parameters = _parameters(obj)
        table = pm.classify(parameters)
        if not context.should_import(table, auth, code):
            continue

        method = obj.get("Method") or {}
        method_code = tr.link_code(method)
        if _is_unsupported(context, method_code):
            context.skip(
                table,
                auth,
                code,
                obj,
                f"operation method {method_code} is not supported by this PROJ build",
            )
            continue

        try:
            source = _resolve_crs(context, obj.get("SourceCrs"), auth, code, "source")
            target = _resolve_crs(context, obj.get("TargetCrs"), auth, code, "target")
        except MissingReferencedObjectError as exc:
            context.skip(table, auth, code, obj, str(exc))
            continue

        common = {
            "auth_name": auth,
            "code": code,
            "name": tr.text(obj, "Name") or "unknown",
            "description": tr.text(obj, "Remark", "Description"),
            "method_auth_name": "EPSG",
            "method_code": method_code,
            "source_crs_auth_name": source[0],
            "source_crs_code": source[1],
            "target_crs_auth_name": target[0],
            "target_crs_code": target[1],
            "accuracy": tr.number(obj, "Accuracy"),
            "operation_version": tr.text(obj, "CoordTfmVersion"),
            "deprecated": tr.deprecated_flag(obj),
        }
        if table != pm.HELMERT_TABLE:
            common["method_name"] = str(method.get("Name") or "")

        if table == pm.HELMERT_TABLE:
            row = common | pm.helmert_columns(parameters)
        elif table == pm.GRID_TABLE:
            row = common | pm.grid_columns(parameters)
        else:
            row = common | pm.other_columns(parameters)

        by_table[table].append(row)
        _finalise(context, table, obj, auth, code)

    for table, rows in by_table.items():
        context.writer.insert(table, rows)
        logger.info("%s: %d imported", table, len(rows))


def collect_concatenated(context: BuildContext) -> None:
    """Import concatenated operations and their ordered steps.

    A concatenated operation without its steps is not a transformation, so an
    operation whose steps cannot all be resolved is skipped rather than written
    with a partial chain. So is one with a step that proj.db runs between other
    CRSs than the register declares for it: it fails validation, because the
    chain the register defines is not the one proj.db would build.
    """
    rows: list[dict[str, Any]] = []
    step_rows: list[dict[str, Any]] = []

    for obj, auth, code in _candidates(
        context, "ConcatenatedOperation", "concatenated_operation"
    ):
        try:
            source = _resolve_crs(context, obj.get("SourceCrs"), auth, code, "source")
            target = _resolve_crs(context, obj.get("TargetCrs"), auth, code, "target")
        except MissingReferencedObjectError as exc:
            context.skip("concatenated_operation", auth, code, obj, str(exc))
            continue

        steps = obj.get("CoordOperations") or []
        resolved_steps = _resolve_steps(context, steps, auth, code)
        if resolved_steps is None:
            continue
        if conflict := _step_endpoint_conflict(context, steps, resolved_steps):
            context.skip("concatenated_operation", auth, code, obj, conflict)
            continue

        rows.append(
            {
                "auth_name": auth,
                "code": code,
                "name": tr.text(obj, "Name") or "unknown",
                "description": tr.text(obj, "Remark", "Description"),
                "source_crs_auth_name": source[0],
                "source_crs_code": source[1],
                "target_crs_auth_name": target[0],
                "target_crs_code": target[1],
                "accuracy": tr.number(obj, "Accuracy"),
                "operation_version": tr.text(obj, "CoordTfmVersion"),
                "deprecated": tr.deprecated_flag(obj),
            }
        )
        step_rows.extend(resolved_steps)
        _finalise(context, "concatenated_operation", obj, auth, code)

    context.writer.insert("concatenated_operation", rows)
    context.writer.insert("concatenated_operation_step", step_rows)
    logger.info(
        "concatenated operations: %d imported with %d steps", len(rows), len(step_rows)
    )


def _resolve_steps(
    context: BuildContext, steps: list[dict[str, Any]], auth: str, code: str
) -> list[dict[str, Any]] | None:
    operation_tables = (
        "helmert_transformation_table",
        "grid_transformation",
        "other_transformation",
        "conversion_table",
        "concatenated_operation",
    )
    resolved: list[dict[str, Any]] = []
    for number, step in enumerate(steps, start=1):
        step_auth: str | None
        step_code: str | None
        try:
            step_auth, step_code = context.resolve_link(
                step,
                tables=operation_tables,
                referenced_by=f"concatenated operation {auth}:{code} step {number}",
            )
        except MissingReferencedObjectError:
            step_auth, step_code = tr.auth_name(step), tr.link_code(step)
            found = False
        else:
            found = True
        if not found:
            context.skip(
                "concatenated_operation",
                auth,
                code,
                None,
                f"step {number} references operation {step_auth}:{step_code}, "
                "which is not available",
            )
            return None
        resolved.append(
            {
                "operation_auth_name": auth,
                "operation_code": code,
                "step_number": number,
                "step_auth_name": step_auth,
                "step_code": step_code,
                "step_direction": None,
            }
        )
    if not resolved:
        context.skip(
            "concatenated_operation", auth, code, None, "operation has no steps"
        )
        return None
    return resolved


def _step_endpoint_conflict(
    context: BuildContext,
    steps: list[dict[str, Any]],
    resolved: list[dict[str, Any]],
) -> str | None:
    """Say why a step is not the operation the register chains, if it is not.

    A step names its operation by code, and an operation proj.db already
    defines, an EPSG transformation say, is used as proj.db defines it. When
    EPSG restates such an operation from another CRS, the register and proj.db
    can hold different versions of it.

    Returns:
        Why the concatenated operation fails validation, or None when every
        step runs between the CRSs the register declares for it. A conversion
        has no CRSs of its own, in proj.db or the register, so it never
        conflicts.
    """
    for step, row in zip(steps, resolved, strict=True):
        name = f"{row['step_auth_name']}:{row['step_code']}"
        installed = _installed_endpoints(
            context, row["step_auth_name"], row["step_code"]
        )
        if installed is None:
            continue
        stated = context.client.resolve(step)
        referenced_by = f"step {row['step_number']} operation {name}"
        declared = (
            _declared_endpoint(
                context, stated.get("SourceCrs"), f"{referenced_by} source CRS"
            ),
            _declared_endpoint(
                context, stated.get("TargetCrs"), f"{referenced_by} target CRS"
            ),
        )
        if None in declared or declared == installed:
            continue
        return (
            f"fails validation: step {row['step_number']} uses {name}, which runs "
            f"from {installed[0]} to {installed[1]} in proj.db but from "
            f"{declared[0]} to {declared[1]} in the register. The register and "
            "proj.db hold different versions of that operation."
        )
    return None


def _installed_endpoints(
    context: BuildContext, auth: str, code: str
) -> tuple[str, str] | None:
    """The CRSs an operation runs between in proj.db; None for a conversion."""
    row = context.writer.connection.execute(
        "SELECT source_crs_auth_name, source_crs_code, target_crs_auth_name, "
        "target_crs_code FROM coordinate_operation_view "
        "WHERE auth_name = ? AND code = ?",
        (auth, code),
    ).fetchone()
    if row is None:
        return None
    return f"{row[0]}:{row[1]}", f"{row[2]}:{row[3]}"


def _declared_endpoint(
    context: BuildContext, link: dict[str, Any] | None, referenced_by: str
) -> str | None:
    """The CRS the register states at one end of an operation, as AUTH:CODE."""
    code = tr.link_code(link)
    if code is None:
        return None
    try:
        owner, resolved = context.resolve_link(
            link, tables=CRS_TABLES, referenced_by=referenced_by
        )
    except MissingReferencedObjectError:
        # A CRS proj.db does not hold cannot be an end of an operation it holds,
        # so it is only named here.
        stated = tr.auth_name(link or {}) or tr.auth_name(context.client.resolve(link))
        return f"{stated}:{code}" if stated else f"code {code}"
    return f"{owner}:{resolved}"


def _parameters(obj: dict[str, Any]) -> list[pm.Parameter]:
    """Read an operation's parameters in the order the register declares them."""
    ordered = sorted(
        obj.get("ParameterValues") or [],
        key=lambda param: int(param.get("SortOrder") or 0),
    )
    parameters = []
    for param in ordered:
        unit = param.get("Unit") or {}
        parameters.append(
            pm.Parameter(
                code=str(param.get("ParameterCode") or "").strip(),
                name=tr.text(param, "Name") or "",
                value=tr.number(param, "ParameterValue"),
                # A parameter naming a grid file has no numeric value.
                file=tr.text(param, "ParamValueFileRef"),
                uom_auth_name=tr.auth_name(unit) or pm.PARAMETER_AUTHORITY,
                uom_code=tr.link_code(unit),
            )
        )
    return parameters


def _is_unsupported(context: BuildContext, method_code: str | None) -> bool:
    if method_code is None:
        return False
    try:
        return int(method_code) in context.config.unsupported_method_codes
    except ValueError:
        return False


def _resolve_crs(
    context: BuildContext,
    link: dict[str, Any] | None,
    auth: str,
    code: str,
    role: str,
) -> tuple[str, str]:
    return context.resolve_link(
        link,
        tables=CRS_TABLES,
        referenced_by=f"operation {auth}:{code} {role} CRS",
    )


def _candidates(
    context: BuildContext, endpoint: str, table: str
) -> Iterator[tuple[dict[str, Any], str, str]]:
    for summary in context.client.iter_collection(
        endpoint, authorities=context.config.authorities
    ):
        obj = context.client.detail(summary)
        auth, code = tr.auth_name(obj), tr.code(obj)
        if code is None or not context.should_import(table, auth, code):
            continue
        yield obj, auth, code


def _finalise(
    context: BuildContext, table: str, obj: dict[str, Any], auth: str, code: str
) -> None:
    context.annotate(context.record(table, auth, code), obj)
