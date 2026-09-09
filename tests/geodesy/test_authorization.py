"""Regression tests for strict operation and epoch authorization."""

import math

import pytest
from pyproj.crs import CoordinateOperation

from geodetic_engine.geodesy import (
    AmbiguousOperationError,
    MissingCoordinateEpochError,
    OperationNotAvailableError,
    OperationRequest,
    Transformation,
)
from geodetic_engine.geodesy.operation import fully_requested


def test_conflicting_id_cannot_be_satisfied_by_name() -> None:
    node = CoordinateOperation.from_authority("EPSG", 1133).to_json_dict()
    node["id"] = {"authority": "CUSTOM", "code": 999}
    node["parameters"][0]["value"] = 10000
    assert not OperationRequest.parse("EPSG:1133").is_satisfied_by(node)


@pytest.mark.parametrize("operations", ["EPSG:11028", "EPSG:9484"])
def test_partial_compound_request_is_refused(operations: str) -> None:
    with pytest.raises(OperationNotAvailableError):
        Transformation("EPSG:4979", "EPSG:6172", operation=operations)


def test_projection_request_does_not_authorize_a_datum_shift() -> None:
    with pytest.raises(OperationNotAvailableError):
        Transformation("EPSG:4326", "EPSG:25832", operation="EPSG:16032")


def test_dynamic_crs_requires_epoch_even_without_rates() -> None:
    transformation = Transformation("EPSG:7912", "EPSG:7789")
    with pytest.raises(MissingCoordinateEpochError):
        transformation.transform((10, 60, 0))
    assert (
        transformation.transform((10, 60, 0), coordinate_epoch=2010).coordinate_epoch
        == 2010
    )


@pytest.mark.parametrize("epoch", [math.nan, math.inf, -math.inf])
def test_nonfinite_epoch_is_refused(epoch: float) -> None:
    with pytest.raises(MissingCoordinateEpochError):
        Transformation("EPSG:4326", "EPSG:3857").transform(
            (10, 60), coordinate_epoch=epoch
        )


def test_automatic_datum_selection_is_refused() -> None:
    with pytest.raises(AmbiguousOperationError):
        Transformation("EPSG:4230", "EPSG:4326", allow_any_operation=True)


def test_pyproj_group_index_error_is_wrapped() -> None:
    with pytest.raises(OperationNotAvailableError, match="PROJ cannot build"):
        Transformation("EPSG:2985", "EPSG:3031", operation="EPSG:1921")


def test_modified_fused_pipeline_is_not_authorized() -> None:
    transformation = Transformation("EPSG:4937", "EPSG:6172", operation="EPSG:9484")
    node = __import__("json").loads(transformation.operation.projjson)
    node["method"]["name"] = node["method"]["name"].replace(
        "multiplier=1", "multiplier=2"
    )
    assert not fully_requested(node, (OperationRequest.parse("EPSG:9484"),))
