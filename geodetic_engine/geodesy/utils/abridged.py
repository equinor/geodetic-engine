"""Stating a coordinate operation in the units an abridged transformation needs.

A bound CRS carries its transformation as an ``ABRIDGEDTRANSFORMATION``. That
form drops units entirely: every parameter has one fixed convention, so
translations are metres, rotations are arc-seconds, and a scale difference is
written as the factor ``1 + s`` rather than as a deviation.

PROJ applies those conversions when it exports a ``BoundCRS``, but only from the
units it expects to find. A scale difference stated in parts per billion, which
EPSG uses for most recent ITRF and ETRF realisations, is written through
unchanged, so ``0.33`` ppb is exported as the literal ``0.33`` and read back as
a scale factor -- a scale of ``-670000`` ppm, and a position wrong by kilometres.

Restating such a parameter before the operation is embedded is enough to avoid
this, and it is not specific to how the operation was arrived at: an operation
read straight from a register needs it just as much as one composed by
:mod:`geodetic_engine.geodesy.utils.helmert`.
"""

from __future__ import annotations

from typing import Any

from pyproj.crs import CoordinateOperation
from pyproj.exceptions import CRSError

from geodetic_engine.geodesy.errors import UnembeddableOperationError

# The scale unit PROJ converts from when exporting an abridged transformation.
_PARTS_PER_MILLION = 1e-06

# EPSG's scale difference. Deliberately the only parameter this module touches:
# a rate of change of scale (1046) is also a scale unit, but PROJ states its
# factor per second, so the same arithmetic would be meaningless there.
_SCALE_DIFFERENCE_CODE = "8611"


def scale_in_parts_per_million(
    operation: CoordinateOperation,
) -> CoordinateOperation:
    """Restate an operation's scale difference in parts per million.

    Only the scale difference is touched, and it is converted with its own
    stated factor, so the operation keeps its name, method, accuracy, area of
    use and authority code, and describes exactly the same transformation.

    Args:
        operation: Any coordinate operation, concatenated or single step.

    Returns:
        The operation itself when the scale difference is already in parts per
        million, or it states none, otherwise an equivalent operation that is.

    Raises:
        UnembeddableOperationError: If the restated operation cannot be read
            back, which would leave nothing to embed.

    Example:
        >>> operation = CoordinateOperation.from_authority("EPSG", 10586)
        >>> next(p.unit_name for p in operation.params if p.code == "8611")
        'parts per billion'
        >>> restated = scale_in_parts_per_million(operation)
        >>> next(p.unit_name for p in restated.params if p.code == "8611")
        'parts per million'
    """
    definition = operation.to_json_dict()
    restated = False
    for parameter in definition.get("parameters", ()):
        unit = parameter.get("unit")
        if (
            not _is_scale_difference(parameter)
            or not isinstance(unit, dict)
            or unit.get("type") != "ScaleUnit"
            or unit.get("conversion_factor") in (None, _PARTS_PER_MILLION)
        ):
            continue
        parameter["value"] = (
            parameter["value"] * unit["conversion_factor"] / _PARTS_PER_MILLION
        )
        parameter["unit"] = {
            "type": "ScaleUnit",
            "name": "parts per million",
            "conversion_factor": _PARTS_PER_MILLION,
        }
        restated = True

    if not restated:
        return operation
    try:
        return CoordinateOperation.from_json_dict(definition)
    except CRSError as error:
        raise UnembeddableOperationError(
            f"the scale of {_label(operation)} could not be restated in parts "
            f"per million: {error}"
        ) from error


def _is_scale_difference(parameter: dict[str, Any]) -> bool:
    """Whether a PROJJSON parameter is EPSG's scale difference."""
    identifier = parameter.get("id")
    if not isinstance(identifier, dict):
        return False
    return (
        identifier.get("authority") == "EPSG"
        and str(identifier.get("code")) == _SCALE_DIFFERENCE_CODE
    )


def _label(operation: CoordinateOperation) -> str:
    """Name an operation for an error message."""
    identifier = operation.to_json_dict().get("id")
    if isinstance(identifier, dict):
        authority, code = identifier.get("authority"), identifier.get("code")
        if authority is not None and code is not None:
            return f"{authority}:{code}"
    return f"{operation.name!r}"
