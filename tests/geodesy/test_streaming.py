"""Numeric buffers and lazy batches retain the ordinary geodetic guarantees."""

import pickle
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch

import numpy as np
import pytest
from numpy.typing import ArrayLike, NDArray
from pyproj import Transformer

from geodetic_engine.geodesy import (
    ArrayTransformationResult,
    CoordinateOutOfRangeError,
    MissingCoordinateEpochError,
    Transformation,
    TransformationFailedError,
)
from geodetic_engine.geodesy.transformation import _apply


@pytest.fixture
def projection() -> Transformation:
    return Transformation("EPSG:4326", "EPSG:32632")


def test_array_axes_match_pyproj_without_modifying_inputs(
    projection: Transformation,
) -> None:
    x = np.linspace(9, 11, 100)
    y = np.linspace(59, 61, 100)
    original = (x.copy(), y.copy())
    raw = Transformer.from_crs(4326, 32632, always_xy=True)

    result = projection.transform_arrays(x, y)

    assert isinstance(result, ArrayTransformationResult)
    assert result.count == 100
    for column, expected, supplied in zip(
        result.columns, raw.transform(x, y, errcheck=True), (x, y), strict=True
    ):
        np.testing.assert_array_equal(column, expected)
        assert column.dtype == np.float64
        assert column.flags.c_contiguous
        assert column.flags.writeable
        assert not np.shares_memory(column, supplied)
    np.testing.assert_array_equal(x, original[0])
    np.testing.assert_array_equal(y, original[1])


@pytest.mark.parametrize(
    "layout", ["rows", "strided", "readonly", "float32", "integer"]
)
def test_numeric_input_layouts(projection: Transformation, layout: str) -> None:
    rows = np.array([[10, 60], [11, 61]], dtype=np.float64)
    if layout == "strided":
        rows = np.repeat(rows, 2, axis=0)[::2]
    elif layout == "readonly":
        rows.flags.writeable = False
    elif layout == "float32":
        rows = rows.astype(np.float32)
    elif layout == "integer":
        rows = rows.astype(np.int64)
    before = rows.copy()

    result = projection.transform_arrays(rows)
    expected = projection.transform(before.tolist())

    np.testing.assert_array_equal(result.to_numpy(), expected.coordinates.to_numpy())
    np.testing.assert_array_equal(rows, before)
    assert result.to_result().to_json_dict() == expected.to_json_dict()
    metadata = expected.to_json_dict()
    del metadata["coordinates"]
    assert result.metadata_json_dict() == metadata
    assert type(result.to_result().coordinates[0][0]) is float


def test_broadcast_axes_and_flat_point(projection: Transformation) -> None:
    result = projection.transform_arrays([10.0, 11.0], 60.0, 100.0)
    expected = projection.transform([[10.0, 60.0, 100.0], [11.0, 60.0, 100.0]])
    np.testing.assert_array_equal(result.to_numpy(), expected.coordinates.to_numpy())
    single = projection.transform_arrays([10.0, 60.0])
    np.testing.assert_array_equal(single.to_numpy(), result.to_numpy()[:1, :2])


def test_aliased_input_axes_are_copied_independently(
    projection: Transformation,
) -> None:
    axis = np.array([10.0, 11.0])
    result = projection.transform_arrays(axis, axis)
    expected = projection.transform([[10.0, 10.0], [11.0, 11.0]])
    np.testing.assert_array_equal(result.to_numpy(), expected.coordinates.to_numpy())
    assert not np.shares_memory(result.columns[0], result.columns[1])
    np.testing.assert_array_equal(axis, [10.0, 11.0])


@pytest.mark.parametrize(
    "source,target,operation,point,epoch,width",
    [
        ("EPSG:4230", "EPSG:32632", "EPSG:8047", (10.0, 60.0, 100.0), None, 3),
        ("EPSG:4979", "EPSG:4978", None, (10.0, 60.0, 100.0), None, 3),
        ("EPSG:4979", "EPSG:3855", "EPSG:3858", (-144.0, 72.0, 548.4082), None, 1),
        ("EPSG:3855", "EPSG:4979", "EPSG:3858", (-144.0, 72.0, 556.3882), None, 3),
        (
            "EPSG:4896",
            "EPSG:4938",
            "EPSG:6277",
            (1137080.2487, -214618.1963, 6252133.9585),
            1993.0,
            3,
        ),
    ],
)
def test_array_operations_replay_the_reported_pipeline(
    source: str,
    target: str,
    operation: str | None,
    point: tuple[float, ...],
    epoch: float | None,
    width: int,
) -> None:
    prepared = Transformation(source, target, operation=operation)
    rows = np.tile(point, (3, 1))
    result = prepared.transform_arrays(rows, coordinate_epoch=epoch)
    assert result.pipeline is not None
    args = tuple(rows[:, axis].copy() for axis in range(rows.shape[1]))
    raw = Transformer.from_pipeline(result.pipeline)
    produced = raw.transform(
        args[0],
        args[1],
        args[2] if len(args) > 2 else None,
        np.full(3, epoch) if epoch is not None else None,
        errcheck=True,
    )
    expected = (produced[2],) if width == 1 else produced[:width]
    np.testing.assert_allclose(result.to_numpy(), np.column_stack(expected), atol=1e-8)
    assert result.to_numpy().shape == (3, width)
    assert result.operation is prepared.operation
    assert result.grids == prepared.grids


@pytest.mark.parametrize("rows", [[], np.empty((0, 2))])
def test_empty_batches(projection: Transformation, rows: ArrayLike) -> None:
    result = projection.transform_arrays(rows)
    assert result.count == 0
    assert result.to_numpy().shape == (0, 2)
    assert result.to_result().coordinates == ()
    axes = projection.transform_arrays([], [], 100)
    assert axes.to_numpy().shape == (0, 3)


@pytest.mark.parametrize(
    "args,error,match",
    [
        ((1.0, None, None), TypeError, "single number"),
        (([1.0, 2.0], None, [3.0]), TypeError, "without y"),
        (([[1.0]], None, None), ValueError, "values"),
        ((np.zeros((2, 2, 2)), None, None), ValueError, "two-dimensional"),
        (([1.0], [2.0, 3.0], None), ValueError, "differing batch sizes"),
        (([[1.0]], [2.0], None), ValueError, "one-dimensional"),
        (([[1.0, 2.0], [3.0]], None, None), ValueError, "inhomogeneous"),
    ],
)
def test_invalid_numeric_shapes(
    projection: Transformation,
    args: tuple[ArrayLike, ArrayLike | None, ArrayLike | None],
    error: type[Exception],
    match: str,
) -> None:
    with pytest.raises(error, match=match):
        projection.transform_arrays(*args)


def test_epoch_rules_are_retained() -> None:
    prepared = Transformation("EPSG:4896", "EPSG:4938", operation="EPSG:6277")
    for epoch in (None, float("nan"), float("inf")):
        with pytest.raises(MissingCoordinateEpochError):
            prepared.transform_arrays([[1.0, 2.0, 3.0]], coordinate_epoch=epoch)


def test_complex_and_masked_values_are_not_silently_discarded(
    projection: Transformation,
) -> None:
    complex_rows = np.array([[10.0 + 1.0j, 60.0]])
    with pytest.raises(TypeError, match="real numbers"):
        projection.transform_arrays(complex_rows)
    with pytest.raises(TypeError, match="real numbers"):
        projection.transform_arrays(complex_rows[:, 0], [60.0])
    masked = np.ma.array([[10.0, 60.0]], mask=[[False, True]])
    with pytest.raises(TypeError, match="masked coordinates"):
        projection.transform_arrays(masked)


def test_input_is_unchanged_after_proj_failure(projection: Transformation) -> None:
    # UTM is undefined at the opposite meridian's equatorial singularity.
    rows = np.array([[10.0, 60.0], [-81.0, 0.0]])
    before = rows.copy()
    with pytest.raises(TransformationFailedError):
        projection.transform_arrays(rows)
    np.testing.assert_array_equal(rows, before)


@pytest.mark.parametrize("container", ["list", "tuple", "object-array"])
@pytest.mark.parametrize("streaming", [False, True])
def test_nested_masked_rows_are_rejected(
    projection: Transformation, container: str, streaming: bool
) -> None:
    row = np.ma.array([10.0, 60.0], mask=[False, True])
    batch: ArrayLike
    if container == "object-array":
        rows = np.empty(1, dtype=object)
        rows[0] = row
        batch = rows
    elif container == "tuple":
        batch = (row,)
    else:
        batch = [row]
    with pytest.raises(TypeError, match="masked coordinates"):
        if streaming:
            next(projection.transform_batches([batch]))
        else:
            projection.transform_arrays(batch)


def test_nested_masked_axis_scalars_are_rejected(
    projection: Transformation,
) -> None:
    with pytest.raises(TypeError, match="masked coordinates"):
        projection.transform_arrays([10.0], [np.ma.masked])


def test_batch_consumption_is_lazy_and_results_are_independent(
    projection: Transformation,
) -> None:
    reads: list[int] = []
    shared = np.array([[10.0, 60.0]])

    def source() -> Iterator[NDArray[np.float64]]:
        for i in range(3):
            reads.append(i)
            shared[0, 0] = 10.0 + i
            yield shared

    batches = projection.transform_batches(source())
    assert reads == []
    first = next(batches)
    saved = first.to_numpy()
    assert reads == [0]
    second = next(batches)
    assert reads == [0, 1]
    np.testing.assert_array_equal(first.to_numpy(), saved)
    assert not np.shares_memory(first.columns[0], second.columns[0])
    batches.close()
    assert reads == [0, 1]


def test_stream_global_range_error_stops_consumption(
    projection: Transformation,
) -> None:
    batches = projection.transform_batches(
        iter([[[10.0, 60.0]], [], [[10.0, 60.0], [10.0, 100.0]], [[10.0, 60.0]]])
    )
    assert next(batches).count == 1
    assert next(batches).count == 0
    with pytest.raises(CoordinateOutOfRangeError, match=r"^point 2 has ") as raised:
        next(batches)
    assert raised.value.point == 2
    assert str(raised.value) == f"point 2 {raised.value.detail}"
    with pytest.raises(StopIteration):
        next(batches)


def test_stream_global_nonfinite_error() -> None:
    identity = Transformation("EPSG:4326", "EPSG:4326")
    batches = identity.transform_batches(
        [[[10.0, 60.0], [11.0, 61.0]], [[float("nan"), 60.0]]]
    )
    next(batches)
    with pytest.raises(TransformationFailedError, match=r"^point 2 has ") as raised:
        next(batches)
    error = raised.value
    assert (error.point, error.detail.startswith("has no finite")) == (2, True)
    restored = pickle.loads(pickle.dumps(error))
    assert (type(restored), str(restored)) == (type(error), str(error))
    assert (restored.point, restored.detail) == (error.point, error.detail)


def test_array_execution_does_not_materialise_legacy_coordinates(
    projection: Transformation,
) -> None:
    with patch(
        "geodetic_engine.geodesy.result.Coordinates",
        side_effect=AssertionError("array path must not construct coordinate tuples"),
    ):
        result = projection.transform_arrays(np.ones((100, 2)))
        assert result.count == 100
        assert "coordinates" not in result.metadata_json_dict()


def test_pipeline_steps_reuse_the_same_owned_buffers() -> None:
    chained = Transformation("EPSG:4230", "EPSG:32632", operation="EPSG:8047")
    rows = np.array([[10.0, 60.0, 100.0]])
    with patch("geodetic_engine.geodesy.transformation._apply", wraps=_apply) as apply:
        result = chained.transform_arrays(rows)
    assert apply.call_count > 1
    for call in apply.call_args_list:
        for working, output in zip(call.args[1], result.columns, strict=True):
            assert working is output
            assert not np.shares_memory(working, rows)


def test_parallel_batches_have_independent_buffers(
    projection: Transformation,
) -> None:
    rows = np.array([[10.0, 60.0], [11.0, 61.0]])
    expected = projection.transform_arrays(rows).to_numpy()
    with ThreadPoolExecutor(max_workers=4) as executor:
        results = list(executor.map(projection.transform_arrays, [rows] * 16))
    for result in results:
        np.testing.assert_array_equal(result.to_numpy(), expected)
    results[0].columns[0].fill(0.0)
    np.testing.assert_array_equal(results[1].to_numpy(), expected)
