"""Coordinates carry their CRS and epoch, and one point is a batch of one."""

from __future__ import annotations

import numpy as np
import pytest

from geodetic_engine.geodesy import (
    CoordinateReferenceSystem,
    Position,
    PositionSet,
    Transformation,
    transform,
)

OSLO_XY = (10.7522, 59.9139)
BERGEN_XY = (5.3221, 60.3913)


def test_single_point_and_batch_agree() -> None:
    """One point through the batch path gives the same answer as in a batch."""
    single = transform("EPSG:4326", "EPSG:3395", [OSLO_XY])
    batch = transform("EPSG:4326", "EPSG:3395", [OSLO_XY, BERGEN_XY])
    assert single.coordinates[0] == batch.coordinates[0]
    assert batch.count == 2


def test_position_wraps_into_a_one_point_set() -> None:
    """Position delegates to PositionSet rather than duplicating the logic."""
    crs = CoordinateReferenceSystem.from_user_input("EPSG:4326")
    point = Position(crs, OSLO_XY)
    assert point.as_set().count == 1
    assert point.as_set().rows == (OSLO_XY,)


def test_transform_accepts_a_position_set() -> None:
    """A prepared batch can be handed straight to a transformation."""
    crs = CoordinateReferenceSystem.from_user_input("EPSG:4326")
    points = PositionSet.from_rows(crs, [OSLO_XY, BERGEN_XY])
    result = Transformation("EPSG:4326", "EPSG:3395").transform(points)
    assert result.count == 2


def test_transform_accepts_a_numpy_array() -> None:
    """A 2D numpy array of shape (n_points, n_axes) works, one row per point."""
    rows = np.array([OSLO_XY, BERGEN_XY])
    expected = transform("EPSG:4326", "EPSG:3395", [OSLO_XY, BERGEN_XY])

    result = transform("EPSG:4326", "EPSG:3395", rows)

    assert result.coordinates == expected.coordinates


def test_from_rows_accepts_a_numpy_array() -> None:
    """PositionSet.from_rows reads a numpy array the same as nested tuples."""
    crs = CoordinateReferenceSystem.from_user_input("EPSG:4326")
    rows = np.array([OSLO_XY, BERGEN_XY])

    points = PositionSet.from_rows(crs, rows)

    assert points.rows == (OSLO_XY, BERGEN_XY)
    assert all(
        isinstance(value, float) for column in points.columns for value in column
    )


def test_columns_are_stored_per_axis() -> None:
    """Values are held one list per axis, which is the shape PROJ wants."""
    crs = CoordinateReferenceSystem.from_user_input("EPSG:4326")
    points = PositionSet.from_rows(crs, [OSLO_XY, BERGEN_XY])
    assert points.columns == ((10.7522, 5.3221), (59.9139, 60.3913))
    assert points.rows == (OSLO_XY, BERGEN_XY)


def test_wrong_number_of_values_is_rejected() -> None:
    """A point must carry one value per axis the CRS declares, or one more."""
    crs = CoordinateReferenceSystem.from_user_input("EPSG:4326")
    with pytest.raises(ValueError, match="declares 2 axes"):
        PositionSet.from_rows(crs, [(1.0,)])
    with pytest.raises(ValueError, match="declares 2 axes"):
        PositionSet.from_rows(crs, [(1.0, 2.0, 3.0, 4.0)])


def test_one_extra_value_passes_through_unchanged() -> None:
    """A height alongside a 2D horizontal CRS is carried through, not dropped.

    Matches pyproj's own convention: ``Transformer.transform(xx, yy, zz)``
    accepts and returns the height regardless of what the CRS pair declares.
    """
    result = transform("EPSG:4326", "EPSG:3395", [(10.0, 60.0, 100.0)])
    assert result.coordinates[0][2] == 100.0
    assert result.target_axes == ("E", "N")


def test_points_from_another_crs_are_rejected() -> None:
    """A batch cannot be fed to a transformation that starts elsewhere."""
    other = CoordinateReferenceSystem.from_user_input("EPSG:4258")
    points = PositionSet.from_rows(other, [OSLO_XY])
    with pytest.raises(ValueError, match="starts from"):
        Transformation("EPSG:4326", "EPSG:3395").transform(points)


def test_epoch_travels_with_the_points() -> None:
    """An epoch set on the batch is used without being passed again."""
    crs = CoordinateReferenceSystem.from_user_input("EPSG:4896")
    points = PositionSet.from_rows(
        crs, [(-2593197.524, 5656917.6189, -1394397.8828)], coordinate_epoch=2005.0
    )
    result = Transformation("EPSG:4896", "EPSG:4938", operation="EPSG:6277").transform(
        points
    )
    assert result.coordinate_epoch == 2005.0


def test_result_carries_its_provenance() -> None:
    """A result answers what produced it, not just what the numbers are."""
    result = transform(
        "EPSG:4979", "EPSG:3855", [(-144.0, 72.0, 548.4082)], operation="EPSG:3858"
    )
    assert result.operation.requested == "EPSG:3858"
    assert result.operation.authority_code == "EPSG:3858"
    assert [grid.name for grid in result.grids] == ["us_nga_egm08_25.tif"]
    assert result.coordinate_order == "xy"
    assert result.pipeline is not None

    rendered = result.as_dict()
    assert rendered["operation"]["applied"] == "EPSG:3858"
    assert rendered["target_axes"] == ["H"]
    assert rendered["target_units"] == ["metre"]


def test_vertical_target_returns_one_value_per_point() -> None:
    """The result matches the target CRS's declared axis count, not PROJ's output."""
    result = transform(
        "EPSG:4979", "EPSG:3855", [(-144.0, 72.0, 548.4082)], operation="EPSG:3858"
    )
    assert result.target_crs.dimension == 1
    assert len(result.coordinates[0]) == 1
