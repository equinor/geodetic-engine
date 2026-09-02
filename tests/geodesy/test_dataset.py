"""The wrapper reproduces the published dataset, within its stated tolerance.

Each record names the operation to apply, so these tests exercise the path that
matters most: a caller asking for one specific EPSG operation and getting that
operation, not whichever one PROJ would have picked.
"""

from __future__ import annotations

from typing import Any

import pytest

from geodetic_engine.geodesy import (
    CoordinateReferenceSystem,
    MissingGridError,
    OperationRoute,
    Transformation,
    TransformationFailedError,
)
from tests.geodesy.conftest import (
    dataset_params,
    ordering_defect,
    residual_metres,
    to_declared,
    to_xy,
)


def check(record: dict[str, Any]) -> None:
    """Transform one dataset record and compare it against the expected values."""
    target = CoordinateReferenceSystem.from_user_input(record["target_crs"])
    source = CoordinateReferenceSystem.from_user_input(record["source_crs"])

    try:
        transformation = Transformation(
            record["source_crs"], record["target_crs"], record["operation"]
        )
    except MissingGridError as error:
        pytest.xfail(f"grid not installed on this machine: {error}")

    try:
        result = transformation.transform(
            [to_xy(source, row) for row in record["source"]],
            coordinate_epoch=record.get("coordinate_epoch"),
        )
    except TransformationFailedError as error:
        # Feeding a transposed record in EPSG order can put the point outside
        # the projection's domain, which PROJ rejects outright.
        _xfail_if_transposed(transformation, source, target, record)
        raise AssertionError(f"PROJ rejected the point: {error}") from error

    if record["operation"] is not None:
        assert result.operation.authority_code == record["operation"]
    else:
        # The conversion file names no operation, so PROJ chooses. That is only
        # allowed because no datum changes; the choice is still recorded.
        assert result.operation.route == OperationRoute.PROJ_DEFAULT
    assert result.source_axes == tuple(record["source_axes"])
    assert result.target_axes == tuple(record["target_axes"])
    assert result.source_units == tuple(record["source_units"])
    assert result.target_units == tuple(record["target_units"])

    tolerance = record["tolerance_m"]
    residuals = [
        residual_metres(target, to_declared(target, produced), expected)
        for produced, expected in zip(
            result.coordinates, record["expected"], strict=True
        )
    ]
    worst = max(residuals)
    if worst > tolerance:
        _xfail_if_transposed(transformation, source, target, record)
        index = residuals.index(worst)
        raise AssertionError(
            f"point {index} is {worst:.6g} m out, tolerance {tolerance} m; "
            f"produced {to_declared(target, result.coordinates[index])}, "
            f"expected {tuple(record['expected'][index])}"
        )


def _xfail_if_transposed(
    transformation: Transformation,
    source: CoordinateReferenceSystem,
    target: CoordinateReferenceSystem,
    record: dict[str, Any],
) -> None:
    """Mark the record xfail if transposing its axes reproduces it exactly."""
    transposed = ordering_defect(transformation, source, target, record)
    if transposed is not None:
        pytest.xfail(
            f"dataset stores {transposed} coordinates against the EPSG "
            f"declared axis order {record['source_axes']} -> "
            f"{record['target_axes']}"
        )


@pytest.mark.parametrize("record", dataset_params("horizontal_conversion.jsonl"))
def test_horizontal_conversion(record: dict[str, Any]) -> None:
    """Same datum, projection and unit arithmetic only."""
    check(record)


@pytest.mark.parametrize("record", dataset_params("horizontal_datum_shift.jsonl"))
def test_horizontal_datum_shift(record: dict[str, Any]) -> None:
    """Datum change by an analytic method."""
    check(record)


@pytest.mark.parametrize("record", dataset_params("horizontal_grid.jsonl"))
def test_horizontal_grid(record: dict[str, Any]) -> None:
    """Datum change that consumes a grid file."""
    check(record)


@pytest.mark.parametrize("record", dataset_params("horizontal_3d.jsonl"))
def test_horizontal_3d(record: dict[str, Any]) -> None:
    """Three-dimensional geographic or geocentric coordinates."""
    check(record)


@pytest.mark.parametrize("record", dataset_params("dynamic.jsonl"))
def test_dynamic(record: dict[str, Any]) -> None:
    """Epoch-dependent transformation involving a dynamic reference frame."""
    check(record)


@pytest.mark.parametrize("record", dataset_params("vertical.jsonl"))
def test_vertical(record: dict[str, Any]) -> None:
    """Vertical or compound target."""
    check(record)
