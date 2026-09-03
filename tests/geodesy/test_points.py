"""Points are plain lists, tuples or numpy arrays; one point is a batch of one."""

from __future__ import annotations

import json

import numpy as np
import pytest

from geodetic_engine.geodesy import CoordinateReferenceSystem, Transformation, transform
from geodetic_engine.geodesy.transformation import _columns

OSLO_XY = (10.7522, 59.9139)
BERGEN_XY = (5.3221, 60.3913)


def test_single_point_and_batch_agree() -> None:
    """One point through the batch path gives the same answer as in a batch."""
    single = transform("EPSG:4326", "EPSG:3395", [OSLO_XY])
    batch = transform("EPSG:4326", "EPSG:3395", [OSLO_XY, BERGEN_XY])
    assert single.coordinates[0] == batch.coordinates[0]
    assert batch.count == 2


def test_a_single_point_can_be_given_flat() -> None:
    """(lon, lat) works directly, without wrapping it in an outer list."""
    wrapped = transform("EPSG:4326", "EPSG:3395", [OSLO_XY])
    flat_tuple = transform("EPSG:4326", "EPSG:3395", OSLO_XY)
    flat_list = transform("EPSG:4326", "EPSG:3395", list(OSLO_XY))
    flat_array = transform("EPSG:4326", "EPSG:3395", np.array(OSLO_XY))

    assert flat_tuple.coordinates == wrapped.coordinates
    assert flat_list.coordinates == wrapped.coordinates
    assert flat_array.coordinates == wrapped.coordinates


def test_x_y_z_can_be_given_as_separate_axes() -> None:
    """Matches pyproj.Transformer.transform(xx, yy, zz): axes, not rows."""
    expected = transform("EPSG:4326", "EPSG:3395", [OSLO_XY, BERGEN_XY])
    lons = [OSLO_XY[0], BERGEN_XY[0]]
    lats = [OSLO_XY[1], BERGEN_XY[1]]

    by_axes = transform("EPSG:4326", "EPSG:3395", lons, lats)
    by_keyword = transform("EPSG:4326", "EPSG:3395", x=lons, y=lats)
    by_arrays = transform("EPSG:4326", "EPSG:3395", np.array(lons), np.array(lats))

    assert by_axes.coordinates == expected.coordinates
    assert by_keyword.coordinates == expected.coordinates
    assert by_arrays.coordinates == expected.coordinates


def test_a_single_point_can_be_given_as_scalar_axes() -> None:
    """x, y as bare scalars is one point, matching flat and wrapped forms."""
    wrapped = transform("EPSG:4326", "EPSG:3395", [OSLO_XY])
    by_axes = transform("EPSG:4326", "EPSG:3395", *OSLO_XY)
    assert by_axes.coordinates == wrapped.coordinates


def test_a_scalar_z_is_broadcast_across_a_batch() -> None:
    """One height applies to every point rather than being repeated by hand."""
    lons = [OSLO_XY[0], BERGEN_XY[0]]
    lats = [OSLO_XY[1], BERGEN_XY[1]]

    broadcast = transform("EPSG:4326", "EPSG:3395", lons, lats, 100.0)
    repeated = transform("EPSG:4326", "EPSG:3395", lons, lats, [100.0, 100.0])

    assert broadcast.coordinates == repeated.coordinates


def test_mismatched_axis_batch_sizes_are_rejected() -> None:
    """x and y must hold the same number of points."""
    with pytest.raises(ValueError, match="differing batch sizes"):
        transform("EPSG:4326", "EPSG:3395", [10.0, 11.0], [60.0, 61.0, 62.0])


def test_z_without_y_is_rejected() -> None:
    """z alone is ambiguous: it cannot be told apart from a lone points batch."""
    tfm = Transformation("EPSG:4979", "EPSG:3855", operation="EPSG:3858")
    with pytest.raises(TypeError, match="z was given without y"):
        tfm.transform(-144.0, None, 548.4082)


def test_transform_accepts_a_numpy_array() -> None:
    """A 2D numpy array of shape (n_points, n_axes) works, one row per point."""
    rows = np.array([OSLO_XY, BERGEN_XY])
    expected = transform("EPSG:4326", "EPSG:3395", [OSLO_XY, BERGEN_XY])
    result = transform("EPSG:4326", "EPSG:3395", rows)

    assert result.coordinates == expected.coordinates


def test_points_are_stored_per_axis() -> None:
    """Values are reshaped one list per axis, which is the shape PROJ wants."""
    crs = CoordinateReferenceSystem.from_user_input("EPSG:4326")
    columns = _columns(crs, [OSLO_XY, BERGEN_XY])
    assert columns == ((10.7522, 5.3221), (59.9139, 60.3913))


def test_wrong_number_of_values_is_rejected() -> None:
    """A point must carry one value per axis the CRS declares, or one more."""
    tfm = Transformation("EPSG:4326", "EPSG:3395")
    with pytest.raises(ValueError, match="declares 2 axes"):
        tfm.transform([(1.0,)])
    with pytest.raises(ValueError, match="declares 2 axes"):
        tfm.transform([(1.0, 2.0, 3.0, 4.0)])


def test_differing_value_counts_are_rejected() -> None:
    """Every point in a batch must carry the same number of values."""
    tfm = Transformation("EPSG:4326", "EPSG:3395")
    with pytest.raises(ValueError, match="differing numbers of values"):
        tfm.transform([OSLO_XY, (5.3221, 60.3913, 10.0)])


def test_one_extra_value_passes_through_unchanged() -> None:
    """A height alongside a 2D horizontal CRS is carried through, not dropped.

    Matches pyproj's own convention: ``Transformer.transform(xx, yy, zz)``
    accepts and returns the height regardless of what the CRS pair declares.
    """
    result = transform("EPSG:4326", "EPSG:3395", [(10.0, 60.0, 100.0)])
    assert result.coordinates[0][2] == 100.0
    assert result.target_axes == ("E", "N")


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

    rendered = result.to_json_dict()
    assert rendered["operation"]["applied"] == "EPSG:3858"
    assert rendered["target_axes"] == ["H"]
    assert rendered["target_units"] == ["metre"]


def test_to_json_matches_to_json_dict_and_can_be_compact() -> None:
    """to_json() serialises the same fields as to_json_dict(), pretty by default."""
    result = transform("EPSG:4326", "EPSG:3395", [OSLO_XY])

    assert json.loads(result.to_json()) == result.to_json_dict()
    assert "\n" in result.to_json()
    assert "\n" not in result.to_json(pretty=False)


def test_vertical_target_returns_one_value_per_point() -> None:
    """The result matches the target CRS's declared axis count, not PROJ's output."""
    result = transform(
        "EPSG:4979", "EPSG:3855", [(-144.0, 72.0, 548.4082)], operation="EPSG:3858"
    )
    assert result.target_crs.dimension == 1
    assert len(result.coordinates[0]) == 1
