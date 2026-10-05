"""P6 parameters and the EPSG 9666 / 1049 transformation they define."""

from __future__ import annotations

import dataclasses
import math
from typing import Any

import numpy as np
import pytest

from geodetic_engine.bingrid import Handedness, InvalidParameterError, P6Parameters
from tests.bingrid import _epsg_reference as epsg
from tests.bingrid.conftest import load, p6, synthetic_grids

EPSG_EXAMPLES = load("epsg_p6_examples.json")["examples"]
GRIDS = synthetic_grids()

NORTH_UP: dict[str, Any] = {
    "origin_i": 1, "origin_j": 1000, "origin_easting": 500000.0,
    "origin_northing": 3000000.0, "bin_width_i": 1000.0, "bin_width_j": 100.0,
    "bearing_j": 0.0, "handedness": "right",
}  # fmt: skip


def test_handedness_states_the_epsg_method() -> None:
    assert Handedness.RIGHT.method_code == 9666
    assert Handedness.LEFT.method_code == 1049
    assert Handedness.RIGHT.sign == 1
    assert Handedness.LEFT.sign == -1
    assert "I=J+90" in Handedness.RIGHT.method_name
    assert "I=J-90" in Handedness.LEFT.method_name
    assert Handedness.from_method_code(9666) is Handedness.RIGHT
    assert Handedness.from_method_code(1049) is Handedness.LEFT


def test_an_unknown_method_code_is_refused() -> None:
    with pytest.raises(InvalidParameterError, match="9621"):
        Handedness.from_method_code(9621)


@pytest.mark.parametrize("example", EPSG_EXAMPLES, ids=lambda e: e["case_id"])
def test_forward_reproduces_the_epsg_example(example: dict[str, Any]) -> None:
    grid = p6(example["parameters"], example["handedness"])

    mapped = grid.to_map(*example["forward"]["node"])

    assert mapped.shape == (1, 2)
    assert tuple(mapped[0]) == pytest.approx(example["forward"]["map"], abs=0.005)
    assert grid.method_code == example["method_code"]


@pytest.mark.parametrize("example", EPSG_EXAMPLES, ids=lambda e: e["case_id"])
def test_reverse_reproduces_the_epsg_example(example: dict[str, Any]) -> None:
    grid = p6(example["parameters"], example["handedness"])

    node = grid.to_bin(*example["reverse"]["map"])

    assert tuple(node[0]) == pytest.approx(example["reverse"]["node"], abs=0.001)


@pytest.mark.parametrize("grid", GRIDS, ids=lambda g: g["case_id"])
def test_both_directions_agree_with_the_independent_reference(
    grid: dict[str, Any],
) -> None:
    """Across every quadrant, handedness, increment and scale factor."""
    parameters = p6(grid["parameters"])
    (i0, i1), (j0, j1) = grid["inline_range"], grid["crossline_range"]
    nodes = [
        (i0, j0),
        (i1, j1),
        (i0, j1),
        ((i0 + i1) / 2, (j0 + j1) / 3),
        (i1 + 7.25, j0 - 3.5),
    ]
    expected_map = [epsg.to_map(grid["parameters"], i, j) for i, j in nodes]

    mapped = parameters.to_map(nodes)
    back = parameters.to_bin(mapped)

    np.testing.assert_allclose(mapped, expected_map, rtol=0, atol=1e-6)
    np.testing.assert_allclose(back, nodes, rtol=0, atol=1e-8)
    for (e, n), (i, j) in zip(expected_map, back, strict=True):
        assert epsg.to_bin(grid["parameters"], e, n) == pytest.approx((i, j), abs=1e-8)


def test_positions_can_be_given_in_every_shape_transform_accepts() -> None:
    grid = p6(NORTH_UP)
    nodes = [(1, 1000), (101, 2000)]
    expected = grid.to_map(nodes)

    assert expected.shape == (2, 2)
    np.testing.assert_array_equal(grid.to_map(np.array(nodes)), expected)
    np.testing.assert_array_equal(grid.to_map([1, 101], [1000, 2000]), expected)
    np.testing.assert_array_equal(
        grid.to_map(np.array([1, 101]), np.array([1000, 2000])), expected
    )
    np.testing.assert_array_equal(grid.to_map((1, 1000)), expected[:1])
    np.testing.assert_array_equal(grid.to_map(1, 1000), expected[:1])
    assert grid.to_map([]).shape == (0, 2)


def test_positions_can_be_one_shot_iterables_as_transform_accepts() -> None:
    grid = p6(NORTH_UP)
    nodes = [(1, 1000), (101, 2000)]
    expected = grid.to_map(nodes)

    np.testing.assert_array_equal(grid.to_map(iter(nodes)), expected)
    np.testing.assert_array_equal(grid.to_map(iter(n) for n in nodes), expected)
    np.testing.assert_array_equal(
        grid.to_map(zip([1, 101], [1000, 2000], strict=True)), expected
    )
    np.testing.assert_array_equal(
        grid.to_map(iter([1, 101]), (j for j in [1000, 2000])), expected
    )
    np.testing.assert_array_equal(grid.to_map(iter((1, 1000))), expected[:1])
    points = expected.tolist()
    np.testing.assert_array_equal(grid.to_bin(iter(points)), grid.to_bin(points))


def test_map_points_can_be_given_in_every_shape_transform_accepts() -> None:
    grid = p6(NORTH_UP)
    points = [(500000.0, 3000000.0), (600000.0, 3100000.0)]
    expected = grid.to_bin(points)

    np.testing.assert_allclose(expected, [(1, 1000), (101, 2000)], atol=1e-9)
    np.testing.assert_array_equal(grid.to_bin(*zip(*points, strict=True)), expected)
    np.testing.assert_array_equal(grid.to_bin(points[0]), expected[:1])


@pytest.mark.parametrize(
    "arguments",
    [
        ([(1, 2, 3)], None),
        ([1, 2, 3], [1, 2]),
        ([[1, 2], [3]], None),
        (1, None),
    ],
    ids=["three-values", "mismatched-axes", "ragged", "lone-scalar"],
)
def test_malformed_positions_are_refused(arguments: tuple[Any, Any]) -> None:
    with pytest.raises(ValueError):
        p6(NORTH_UP).to_map(*arguments)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("scale_factor", 0.0),
        ("scale_factor", -1.0),
        ("scale_factor", math.inf),
        ("scale_factor", math.nan),
        ("bin_width_i", 0.0),
        ("bin_width_j", -25.0),
        ("bin_width_i", math.nan),
        ("increment_i", 0),
        ("increment_j", -2),
        ("increment_i", 1.5),
        ("increment_j", True),
        ("bearing_j", math.nan),
        ("origin_easting", math.inf),
        ("origin_i", math.nan),
        ("handedness", "clockwise"),
    ],
)
def test_out_of_range_parameters_are_refused(field: str, value: Any) -> None:
    fields = {**NORTH_UP, "handedness": Handedness.RIGHT, field: value}

    with pytest.raises(InvalidParameterError, match=field):
        P6Parameters(**fields)


@pytest.mark.parametrize(
    ("bearing", "normalised"),
    [(-10.0, 350.0), (360.0, 0.0), (720.5, 0.5), (-1e-15, 0.0), (359.999, 359.999)],
)
def test_the_bearing_is_normalised_to_a_full_circle(
    bearing: float, normalised: float
) -> None:
    grid = p6({**NORTH_UP, "bearing_j": bearing})

    assert grid.bearing_j == pytest.approx(normalised, abs=1e-12)
    assert 0.0 <= grid.bearing_j < 360.0


def test_handedness_may_be_given_by_value() -> None:
    fields = {**NORTH_UP, "handedness": "left"}

    assert P6Parameters(**fields).handedness is Handedness.LEFT


def test_numbers_are_stored_as_float_and_int() -> None:
    grid = p6({**NORTH_UP, "increment_i": np.int64(2), "origin_i": np.int32(1)})

    assert type(grid.origin_i) is float
    assert type(grid.origin_easting) is float
    assert type(grid.increment_i) is int


@pytest.mark.parametrize(
    ("handedness", "bearing_j", "bearing_i"),
    [
        ("right", 20.0, 110.0),
        ("left", 340.0, 250.0),
        ("right", 300.0, 30.0),
        ("left", 10.0, 280.0),
    ],
)
def test_the_i_axis_bearing_follows_from_handedness(
    handedness: str, bearing_j: float, bearing_i: float
) -> None:
    """EPSG's examples: I and J bearings 110/20 (9666) and 250/340 (1049)."""
    grid = p6({**NORTH_UP, "handedness": handedness, "bearing_j": bearing_j})

    assert grid.bearing_i == pytest.approx(bearing_i)


def test_grid_steps_are_scaled_bin_widths_per_number() -> None:
    grid = p6({**NORTH_UP, "scale_factor": 0.9996, "increment_i": 2, "increment_j": 4})

    assert grid.grid_step_i == pytest.approx(0.9996 * 1000.0 / 2)
    assert grid.grid_step_j == pytest.approx(0.9996 * 100.0 / 4)


@pytest.mark.parametrize("grid", GRIDS, ids=lambda g: g["case_id"])
def test_reanchoring_keeps_every_position(grid: dict[str, Any]) -> None:
    parameters = p6(grid["parameters"])
    (i0, i1), (j0, j1) = grid["inline_range"], grid["crossline_range"]
    nodes = [(i0, j0), (i1, j1), (i0, j1), (i1, j0)]

    moved = parameters.reanchored(i1, j0)

    assert (moved.origin_i, moved.origin_j) == (i1, j0)
    np.testing.assert_allclose(moved.to_map(nodes), parameters.to_map(nodes), atol=1e-6)
    assert dataclasses.replace(
        moved, origin_i=0.0, origin_j=0.0, origin_easting=0.0, origin_northing=0.0
    ) == dataclasses.replace(
        parameters, origin_i=0.0, origin_j=0.0, origin_easting=0.0, origin_northing=0.0
    )


def test_parameters_are_immutable() -> None:
    grid = p6(NORTH_UP)

    with pytest.raises(dataclasses.FrozenInstanceError):
        grid.bearing_j = 10.0  # type: ignore[misc]


def test_json_rendering_names_the_method_and_both_bearings() -> None:
    rendered = p6({**NORTH_UP, "handedness": "left", "bearing_j": 10.0}).to_json_dict()

    assert rendered["method_code"] == 1049
    assert rendered["handedness"] == "left"
    assert rendered["bearing_j"] == 10.0
    assert rendered["bearing_i"] == pytest.approx(280.0)
    assert rendered["bin_width_i"] == 1000.0
    assert rendered["scale_factor"] == 1.0
    assert rendered["increment_i"] == 1
