"""Regression tests for strict operation and epoch authorization."""

import math

import pytest
from pyproj import CRS, Transformer
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


def test_a_dynamic_frame_alone_does_not_require_an_epoch() -> None:
    """ITRF2014 to ITRF2014: the frame is dynamic, the arithmetic is not."""
    transformation = Transformation("EPSG:7912", "EPSG:7789")
    assert not transformation.requires_epoch
    assert transformation.transform((10, 60, 0)).coordinates
    assert (
        transformation.transform((10, 60, 0), coordinate_epoch=2010).coordinate_epoch
        == 2010
    )


def test_a_static_helmert_out_of_a_dynamic_frame_needs_no_epoch() -> None:
    """EPSG declares WGS 72 dynamic, but EPSG:1237 is a plain seven parameter shift.

    PROJ returns identical coordinates at every epoch, so demanding one would
    refuse valid work without preventing any error.
    """
    transformation = Transformation("EPSG:4326", "EPSG:32232", operation="EPSG:1237")
    assert not transformation.requires_epoch
    unstated = transformation.transform((7.0, 52.0)).coordinates
    assert (
        unstated
        == transformation.transform((7.0, 52.0), coordinate_epoch=2026.0).coordinates
    )


def test_a_time_dependent_operation_requires_an_epoch() -> None:
    """EPSG:8366 carries rates of change, so the epoch enters the arithmetic."""
    transformation = Transformation("EPSG:7789", "EPSG:8401", operation="EPSG:8366")
    assert transformation.requires_epoch
    with pytest.raises(MissingCoordinateEpochError):
        transformation.transform((10, 60, 0))
    early = transformation.transform((10, 60, 0), coordinate_epoch=2000.0).coordinates
    late = transformation.transform((10, 60, 0), coordinate_epoch=2020.0).coordinates
    assert early != late


@pytest.mark.parametrize("epoch", [math.nan, math.inf, -math.inf])
def test_nonfinite_epoch_is_refused(epoch: float) -> None:
    with pytest.raises(MissingCoordinateEpochError):
        Transformation("EPSG:4326", "EPSG:3857").transform(
            (10, 60), coordinate_epoch=epoch
        )


@pytest.mark.parametrize("version", ["WKT1_ESRI", "WKT1_GDAL"])
@pytest.mark.parametrize("source_code, target_code", [(4326, 3857), (4258, 25832)])
@pytest.mark.parametrize("inverse", [False, True])
def test_wkt1_representation_does_not_require_a_datum_operation(
    version: str, source_code: int, target_code: int, inverse: bool
) -> None:
    geographic = CRS.from_wkt(CRS(source_code).to_wkt(version))
    projected = CRS(target_code)
    source, target = (projected, geographic) if inverse else (geographic, projected)
    point = (500000.0, 6650000.0) if inverse else (10.0, 60.0)
    expected = Transformer.from_crs(
        source, target, always_xy=True, allow_ballpark=False
    ).transform(*point)

    transformation = Transformation(source, target)
    result = transformation.transform(point)

    assert result.coordinates[0] == pytest.approx(expected, abs=1e-9)
    assert transformation.operation.requested is None
    assert not transformation.operation.ballpark
    assert result.pipeline is not None
    assert Transformer.from_pipeline(result.pipeline).transform(
        *point
    ) == pytest.approx(expected, abs=1e-9)


@pytest.mark.parametrize("code", [4326, 4258])
def test_wkt1_identity_conversion_is_allowed(code: int) -> None:
    source = CRS.from_wkt(CRS(code).to_wkt("WKT1_ESRI"))
    result = Transformation(source, code).transform((10.0, 60.0))
    assert result.coordinates[0] == pytest.approx((10.0, 60.0))


@pytest.mark.parametrize("allow_any_operation", [False, True])
@pytest.mark.parametrize("source_code, target_code", [(4230, 4326), (4326, 25832)])
def test_wkt1_datum_change_still_requires_an_operation(
    source_code: int, target_code: int, allow_any_operation: bool
) -> None:
    source = CRS.from_wkt(CRS(source_code).to_wkt("WKT1_ESRI"))
    with pytest.raises(AmbiguousOperationError):
        Transformation(source, target_code, allow_any_operation=allow_any_operation)


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
