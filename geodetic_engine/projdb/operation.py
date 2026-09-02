"""Coordinate conversions and transformations.

Which proj.db table a coordinate operation belongs in is decided from its
parameters rather than from a hand-maintained list of method codes: an
operation carrying a grid file reference is a grid transformation, one carrying
the Helmert translation parameters is a Helmert transformation, and anything
else is an other_transformation. Getting this wrong would hide an operation
from PROJ entirely.

Parameter values are written in the units the authority states, with the unit
recorded alongside. Nothing is converted here; PROJ applies the unit when it
builds the pipeline.
"""

from __future__ import annotations

import logging
from collections.abc import Iterator
from typing import Any

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

# EPSG parameter codes for the Helmert family, mapped to their proj.db columns.
_HELMERT_PARAMS: dict[str, tuple[str, str]] = {
    "8605": ("tx", "translation"),
    "8606": ("ty", "translation"),
    "8607": ("tz", "translation"),
    "8608": ("rx", "rotation"),
    "8609": ("ry", "rotation"),
    "8610": ("rz", "rotation"),
    "8611": ("scale_difference", "scale_difference"),
    "1040": ("rate_tx", "rate_translation"),
    "1041": ("rate_ty", "rate_translation"),
    "1042": ("rate_tz", "rate_translation"),
    "1043": ("rate_rx", "rate_rotation"),
    "1044": ("rate_ry", "rate_rotation"),
    "1045": ("rate_rz", "rate_rotation"),
    "1046": ("rate_scale_difference", "rate_scale_difference"),
    "1047": ("epoch", "epoch"),
    "8617": ("px", "pivot"),
    "8618": ("py", "pivot"),
    "8667": ("pz", "pivot"),
}
_TRANSLATION_CODES = frozenset({"8605", "8606", "8607"})


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
        for index, param in enumerate(_parameters(obj)[:7], start=1):
            row |= _numbered_param(param, index, with_name=False)
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
    param: dict[str, Any],
) -> None:
    """Note a conversion parameter's name, unless proj.db already defines it."""
    code = str(param.get("ParameterCode") or "").strip()
    name = tr.text(param, "Name")
    if not code or not name or not context.is_new("conversion_param", "EPSG", code):
        return
    parameters.setdefault(
        ("EPSG", code), {"auth_name": "EPSG", "code": code, "name": name}
    )


def collect_transformations(context: BuildContext) -> None:
    """Import transformations, routed by parameter shape to the right table."""
    by_table: dict[str, list[dict[str, Any]]] = {
        "helmert_transformation_table": [],
        "grid_transformation": [],
        "other_transformation": [],
    }

    for summary in context.client.iter_collection(
        "Transformation", authorities=context.config.authorities
    ):
        obj = context.client.detail(summary)
        auth, code = tr.auth_name(obj), tr.code(obj)
        if code is None:
            continue
        parameters = _parameters(obj)
        table = _classify(parameters)
        if not context.is_new(table, auth, code):
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
        if table != "helmert_transformation_table":
            common["method_name"] = str(method.get("Name") or "")

        if table == "helmert_transformation_table":
            row = common | _helmert_columns(parameters)
        elif table == "grid_transformation":
            row = common | _grid_columns(parameters)
        else:
            row = common | _other_columns(parameters)

        by_table[table].append(row)
        _finalise(context, table, obj, auth, code)

    for table, rows in by_table.items():
        context.writer.insert(table, rows)
        logger.info("%s: %d imported", table, len(rows))


def collect_concatenated(context: BuildContext) -> None:
    """Import concatenated operations and their ordered steps.

    A concatenated operation without its steps is not a transformation, so an
    operation whose steps cannot all be resolved is skipped rather than written
    with a partial chain.
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


def _classify(parameters: list[dict[str, Any]]) -> str:
    """Choose the proj.db table for a transformation from its parameters."""
    if any(_grid_file(param) for param in parameters):
        return "grid_transformation"
    codes = {str(param.get("ParameterCode")) for param in parameters}
    if codes >= _TRANSLATION_CODES:
        return "helmert_transformation_table"
    return "other_transformation"


def _helmert_columns(parameters: list[dict[str, Any]]) -> dict[str, Any]:
    row: dict[str, Any] = {}
    units: dict[str, dict[str, Any]] = {}
    for param in parameters:
        mapping = _HELMERT_PARAMS.get(str(param.get("ParameterCode")))
        if mapping is None:
            continue
        column, unit_group = mapping
        row[column] = tr.number(param, "ParameterValue")
        units.setdefault(unit_group, param.get("Unit") or {})
    for unit_group, unit in units.items():
        row[f"{unit_group}_uom_auth_name"] = tr.auth_name(unit) or "EPSG"
        row[f"{unit_group}_uom_code"] = tr.link_code(unit)
    return row


def _grid_columns(parameters: list[dict[str, Any]]) -> dict[str, Any]:
    row: dict[str, Any] = {}
    grids = [param for param in parameters if _grid_file(param)]
    others = [param for param in parameters if not _grid_file(param)]

    for prefix, param in zip(("grid", "grid2"), grids[:2], strict=False):
        row[f"{prefix}_param_auth_name"] = "EPSG"
        row[f"{prefix}_param_code"] = str(param.get("ParameterCode"))
        row[f"{prefix}_param_name"] = tr.text(param, "Name") or ""
        row[f"{prefix}_name"] = _grid_file(param)

    for index, param in enumerate(others[:2], start=1):
        row |= _numbered_param(param, index, with_name=True)
    return row


def _other_columns(parameters: list[dict[str, Any]]) -> dict[str, Any]:
    row: dict[str, Any] = {}
    for index, param in enumerate(parameters[:9], start=1):
        row |= _numbered_param(param, index, with_name=True)
    return row


def _numbered_param(
    param: dict[str, Any], index: int, *, with_name: bool
) -> dict[str, Any]:
    unit = param.get("Unit") or {}
    row = {
        f"param{index}_auth_name": "EPSG",
        f"param{index}_code": str(param.get("ParameterCode")),
        f"param{index}_value": tr.number(param, "ParameterValue"),
        f"param{index}_uom_auth_name": tr.auth_name(unit) or "EPSG",
        f"param{index}_uom_code": tr.link_code(unit),
    }
    if with_name:
        row[f"param{index}_name"] = tr.text(param, "Name") or ""
    return row


def _grid_file(param: dict[str, Any]) -> str | None:
    """Return the grid file a parameter refers to, if it refers to one."""
    return tr.text(param, "ParamValueFileRef")


def _parameters(obj: dict[str, Any]) -> list[dict[str, Any]]:
    return sorted(
        obj.get("ParameterValues") or [],
        key=lambda param: int(param.get("SortOrder") or 0),
    )


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
        if code is None or not context.is_new(table, auth, code):
            continue
        yield obj, auth, code


def _finalise(
    context: BuildContext, table: str, obj: dict[str, Any], auth: str, code: str
) -> None:
    context.annotate(context.record(table, auth, code), obj)
