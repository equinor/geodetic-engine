"""Finding the positions a transformation fails for, with only the public API."""

from __future__ import annotations

import pickle
from typing import Any

import numpy as np
import pytest

from geodetic_engine.geodesy import (
    CoordinateOutOfRangeError,
    MissingCoordinateEpochError,
    Transformation,
    TransformationFailedError,
)
from geodetic_engine.geojson._failures import (
    TooManyFailures,
    first_failure,
    isolate,
    probe,
    relocated,
)

# Longitude 93 at the equator is outside UTM zone 31's projection domain, which
# PROJ refuses without naming the point. Latitude 95 is refused, by index,
# before PROJ is called.
OUTSIDE_DOMAIN = (93.0, 0.0)
OUT_OF_RANGE = (3.0, 95.0)


@pytest.fixture(scope="module")
def to_utm() -> Transformation:
    return Transformation("EPSG:4326", "EPSG:32631")


def _rows(count: int, failing: dict[int, tuple[float, float]]) -> np.ndarray:
    generator = np.random.default_rng(31)
    rows = np.column_stack(
        [generator.uniform(0.0, 6.0, count), generator.uniform(55.0, 72.0, count)]
    )
    for index, position in failing.items():
        rows[index] = position
    return rows


class _Counting:
    """A transformation that counts its calls, to check the work done."""

    def __init__(self, transformation: Transformation) -> None:
        self.transformation = transformation
        self.sizes: list[int] = []

    def transform_arrays(self, rows: Any, **options: Any) -> Any:
        self.sizes.append(len(rows))
        return self.transformation.transform_arrays(rows, **options)


def test_the_probe_raises_what_no_point_is_to_blame_for(to_utm: Transformation) -> None:
    with pytest.raises(TransformationFailedError, match="declares 3 axes"):
        probe(Transformation("EPSG:4326", "EPSG:4979"), 2, None)
    with pytest.raises(MissingCoordinateEpochError):
        probe(Transformation("EPSG:4896", "EPSG:4938", operation="EPSG:6277"), 3, None)
    with pytest.raises(ValueError, match="4 values"):
        probe(to_utm, 4, None)
    result = probe(to_utm, 3, None)
    assert (result.count, len(result.columns)) == (0, 3)
    assert result.operation == to_utm.operation


@pytest.mark.parametrize(
    ("failing", "first", "error"),
    [
        ({137: OUTSIDE_DOMAIN, 600: OUTSIDE_DOMAIN}, 137, TransformationFailedError),
        ({0: OUT_OF_RANGE}, 0, CoordinateOutOfRangeError),
        ({999: OUT_OF_RANGE}, 999, CoordinateOutOfRangeError),
        ({512: OUT_OF_RANGE, 300: OUTSIDE_DOMAIN}, 300, TransformationFailedError),
    ],
)
def test_the_first_failing_row_is_found(
    to_utm: Transformation,
    failing: dict[int, tuple[float, float]],
    first: int,
    error: type[Exception],
) -> None:
    counting = _Counting(to_utm)
    found = first_failure(counting, _rows(1000, failing), None)  # type: ignore[arg-type]
    assert found is not None
    assert found.index == first
    assert type(found.error) is error
    # One whole batch, ten halvings of 1000 rows, and the row on its own.
    assert len(counting.sizes) <= 12
    assert sum(counting.sizes) <= 2 * 1000 + 1


def test_no_failing_row_is_one_call(to_utm: Transformation) -> None:
    counting = _Counting(to_utm)
    assert first_failure(counting, _rows(1000, {}), None) is None  # type: ignore[arg-type]
    assert counting.sizes == [1000]


@pytest.mark.parametrize("failures", [0, 1, 7, 64, 1000])
def test_isolated_rows_are_bitwise_those_of_one_batch(
    to_utm: Transformation, failures: int
) -> None:
    generator = np.random.default_rng(failures)
    bad = sorted(generator.choice(1000, failures, replace=False).tolist())
    failing = {
        index: OUT_OF_RANGE if number % 2 else OUTSIDE_DOMAIN
        for number, index in enumerate(bad)
    }
    rows = _rows(1000, failing)
    isolated = isolate(to_utm, rows, None)

    assert [failure.index for failure in isolated.failures] == bad
    assert np.flatnonzero(isolated.failed).tolist() == bad
    good = ~isolated.failed
    if good.any():
        expected = to_utm.transform_arrays(rows[good]).columns
        for column, values in zip(isolated.columns, expected, strict=True):
            assert np.array_equal(column[good].view(np.uint64), values.view(np.uint64))
    for column in isolated.columns:
        assert np.isnan(column[isolated.failed]).all()


def test_each_failing_row_keeps_its_own_reason(to_utm: Transformation) -> None:
    rows = _rows(50, {10: OUT_OF_RANGE, 20: OUTSIDE_DOMAIN})
    reasons = {f.index: type(f.error) for f in isolate(to_utm, rows, None).failures}
    assert reasons == {10: CoordinateOutOfRangeError, 20: TransformationFailedError}


def test_isolation_costs_one_call_when_nothing_fails(to_utm: Transformation) -> None:
    counting = _Counting(to_utm)
    isolate(counting, _rows(1000, {}), None)  # type: ignore[arg-type]
    assert counting.sizes == [0, 1000]


def test_isolation_raises_what_no_row_is_to_blame_for() -> None:
    to_3d = Transformation("EPSG:4326", "EPSG:4979")
    with pytest.raises(TransformationFailedError, match="declares 3 axes"):
        isolate(to_3d, _rows(10, {}), None)


def test_an_empty_batch_is_isolated_without_transforming(
    to_utm: Transformation,
) -> None:
    isolated = isolate(to_utm, np.empty((0, 2)), None)
    assert [len(column) for column in isolated.columns] == [0, 0]
    assert isolated.failures == ()


def test_isolation_stops_at_one_failure_too_many(to_utm: Transformation) -> None:
    """When every row fails, the cost stays that of finding the first few."""
    counting = _Counting(to_utm)
    rows = np.tile(OUT_OF_RANGE, (1024, 1))
    with pytest.raises(TooManyFailures, match="more than 2 rows") as raised:
        isolate(counting, rows, None, max_failures=2)  # type: ignore[arg-type]
    assert [failure.index for failure in raised.value.failures] == [0, 1, 2]
    assert len(counting.sizes) < 20
    restored = pickle.loads(pickle.dumps(raised.value))
    assert [failure.index for failure in restored.failures] == [0, 1, 2]


def test_isolation_within_the_limit_is_complete(to_utm: Transformation) -> None:
    rows = _rows(100, {10: OUT_OF_RANGE, 90: OUTSIDE_DOMAIN})
    isolated = isolate(to_utm, rows, None, max_failures=2)
    assert [failure.index for failure in isolated.failures] == [10, 90]
    with pytest.raises(TooManyFailures):
        isolate(to_utm, rows, None, max_failures=1)
    with pytest.raises(ValueError, match="negative"):
        isolate(to_utm, rows, None, max_failures=-1)


@pytest.mark.parametrize(
    ("position", "error", "start"),
    [
        (OUT_OF_RANGE, CoordinateOutOfRangeError, "$.features[2].geometry.coordinates[1] has geodetic latitude 95.0"),
        (OUTSIDE_DOMAIN, TransformationFailedError, "$.features[2].geometry.coordinates[1]: PROJ could not transform"),
    ],
)  # fmt: skip
def test_a_failure_names_its_position(
    to_utm: Transformation,
    position: tuple[float, float],
    error: type[Exception],
    start: str,
) -> None:
    found = first_failure(to_utm, np.array([position]), None)
    assert found is not None
    moved = relocated(found.error, "$.features[2].geometry.coordinates[1]")
    assert type(moved) is error
    assert str(moved).startswith(start)
    assert "point 0" not in str(moved)
