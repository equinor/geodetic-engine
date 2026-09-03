"""Coordinate operation parameters, and the proj.db table their shape implies.

Which proj.db table a coordinate operation belongs in is decided from its
parameters rather than from a hand-maintained list of method codes: an
operation carrying a grid file reference is a grid transformation, one carrying
the Helmert translation parameters is a Helmert transformation, and anything
else is an ``other_transformation``. Getting this wrong would hide an operation
from PROJ entirely, so the rule lives in one place and every source uses it.

Parameter values are written in the units the authority states, with the unit
recorded alongside. Nothing is converted here; PROJ applies the unit when it
builds the pipeline.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

HELMERT_TABLE = "helmert_transformation_table"
GRID_TABLE = "grid_transformation"
OTHER_TABLE = "other_transformation"

# Parameter codes are EPSG's regardless of who defines the operation using them.
PARAMETER_AUTHORITY = "EPSG"

# EPSG parameter codes for the Helmert family, mapped to their proj.db columns
# and to the column group that records the unit shared by that family.
HELMERT_PARAMS: dict[str, tuple[str, str]] = {
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

# The three translations every Helmert variant carries.
TRANSLATION_CODES = frozenset({"8605", "8606", "8607"})


@dataclass(frozen=True, slots=True)
class Parameter:
    """One coordinate operation parameter as the defining authority states it.

    Attributes:
        code: EPSG parameter code.
        name: Parameter name as the authority states it.
        value: Numeric value in ``uom_code``'s unit, or None for a file
            parameter.
        file: Grid file this parameter names, or None for a numeric parameter.
            A parameter has one or the other, never both.
        uom_auth_name: Authority of the unit of measure.
        uom_code: Code of the unit of measure.
        auth_name: Authority that defines the parameter itself, EPSG in
            practice.
    """

    code: str
    name: str
    value: float | None = None
    file: str | None = None
    uom_auth_name: str | None = None
    uom_code: str | None = None
    auth_name: str = PARAMETER_AUTHORITY


def classify(parameters: Sequence[Parameter]) -> str:
    """Choose the proj.db table for a transformation from its parameters.

    Args:
        parameters: The operation's parameters, in the authority's order.

    Returns:
        One of :data:`GRID_TABLE`, :data:`HELMERT_TABLE` or
        :data:`OTHER_TABLE`.

    Example:
        >>> classify([Parameter("8656", "Latitude difference file", file="x.las")])
        'grid_transformation'
        >>> shifts = [Parameter(c, "translation", 1.0) for c in TRANSLATION_CODES]
        >>> classify(shifts)
        'helmert_transformation_table'
        >>> classify([Parameter("8601", "Longitude offset", 2.0)])
        'other_transformation'
    """
    if any(param.file for param in parameters):
        return GRID_TABLE
    if {param.code for param in parameters} >= TRANSLATION_CODES:
        return HELMERT_TABLE
    return OTHER_TABLE


def helmert_columns(parameters: Sequence[Parameter]) -> dict[str, Any]:
    """Map Helmert parameters onto their named proj.db columns.

    Each family of parameters (translations, rotations, their rates, the pivot)
    shares one unit column pair, taken from the first parameter of that family.
    """
    row: dict[str, Any] = {}
    units: dict[str, Parameter] = {}
    for param in parameters:
        mapping = HELMERT_PARAMS.get(param.code)
        if mapping is None:
            continue
        column, unit_group = mapping
        row[column] = param.value
        units.setdefault(unit_group, param)
    for unit_group, param in units.items():
        row[f"{unit_group}_uom_auth_name"] = param.uom_auth_name or PARAMETER_AUTHORITY
        row[f"{unit_group}_uom_code"] = param.uom_code
    return row


def grid_columns(parameters: Sequence[Parameter]) -> dict[str, Any]:
    """Map a grid transformation's parameters onto its proj.db columns.

    grid_transformation has two grid slots and two general parameter slots.
    """
    row: dict[str, Any] = {}
    grids = [param for param in parameters if param.file]
    others = [param for param in parameters if not param.file]

    for prefix, param in zip(("grid", "grid2"), grids[:2], strict=False):
        row[f"{prefix}_param_auth_name"] = param.auth_name
        row[f"{prefix}_param_code"] = param.code
        row[f"{prefix}_param_name"] = param.name
        row[f"{prefix}_name"] = param.file

    for index, param in enumerate(others[:2], start=1):
        row |= numbered_param(param, index, with_name=True)
    return row


def other_columns(parameters: Sequence[Parameter]) -> dict[str, Any]:
    """Map any other transformation's parameters onto its nine proj.db slots."""
    row: dict[str, Any] = {}
    for index, param in enumerate(parameters[:9], start=1):
        row |= numbered_param(param, index, with_name=True)
    return row


def conversion_columns(parameters: Sequence[Parameter]) -> dict[str, Any]:
    """Map a conversion's parameters onto its seven proj.db slots.

    conversion_table has no per-parameter name column; the names live in
    ``conversion_param``.
    """
    row: dict[str, Any] = {}
    for index, param in enumerate(parameters[:7], start=1):
        row |= numbered_param(param, index, with_name=False)
    return row


def numbered_param(param: Parameter, index: int, *, with_name: bool) -> dict[str, Any]:
    """Render one parameter into the ``paramN_*`` columns of a table."""
    row: dict[str, Any] = {
        f"param{index}_auth_name": param.auth_name,
        f"param{index}_code": param.code,
        f"param{index}_value": param.value,
        f"param{index}_uom_auth_name": param.uom_auth_name or PARAMETER_AUTHORITY,
        f"param{index}_uom_code": param.uom_code,
    }
    if with_name:
        row[f"param{index}_name"] = param.name
    return row
