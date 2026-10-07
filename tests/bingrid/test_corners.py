"""Four corners, labelled A to D by their inline and crossline numbers."""

from __future__ import annotations

import dataclasses
import itertools
import math
from typing import Any

import numpy as np
import pytest

from geodetic_engine.bingrid import (
    BinGridCorner,
    BinGridCorners,
    DegenerateBinGridError,
    InvalidCornersError,
    corners_from_p6,
)
from tests.bingrid.conftest import (
    corner_tuples,
    exact_corners,
    load,
    p6,
    synthetic_grids,
)

NOTE = load("sdu_note_examples.json")["four_point_example"]
GRIDS = synthetic_grids()


def test_corners_are_labelled_by_their_numbers_whatever_their_order() -> None:
    given = corner_tuples(NOTE)

    for order in itertools.permutations(given):
        corners = BinGridCorners.from_corners(order)

        assert [(c.inline, c.crossline) for c in corners] == [
            (14100, 5161), (14100, 9409), (17700, 5161), (17700, 9409)
        ]  # fmt: skip
        assert corners.a == BinGridCorner(14100, 5161, 423081.91, 3227689.59)


def test_corner_objects_and_tuples_can_be_mixed() -> None:
    given = corner_tuples(NOTE)
    mixed = [BinGridCorner(*given[0]), *given[1:]]

    assert BinGridCorners.from_corners(mixed) == BinGridCorners.from_corners(given)


def test_labelled_corners_and_ranges() -> None:
    corners = BinGridCorners.from_corners(corner_tuples(NOTE))

    assert [label for label, _ in corners.labelled()] == ["A", "B", "C", "D"]
    assert corners.labelled()[3][1] is corners.d
    assert corners.inline_range == (14100, 17700)
    assert corners.crossline_range == (5161, 9409)
    np.testing.assert_array_equal(
        corners.coordinates,
        [[423081.91, 3227689.59], [491345.95, 3309043.51],
         [395504.31, 3250829.95], [463768.35, 3332183.87]],
    )  # fmt: skip
    np.testing.assert_array_equal(
        corners.numbers, [[14100, 5161], [14100, 9409], [17700, 5161], [17700, 9409]]
    )


def test_corners_can_be_moved_keeping_their_numbers() -> None:
    corners = BinGridCorners.from_corners(corner_tuples(NOTE))

    moved = corners.with_coordinates([(1.0, 2.0), (3.0, 4.0), (5.0, 6.0), (7.0, 8.0)])

    assert moved.c == BinGridCorner(17700, 5161, 5.0, 6.0)
    np.testing.assert_array_equal(moved.numbers, corners.numbers)
    with pytest.raises(InvalidCornersError, match="4"):
        corners.with_coordinates([(1.0, 2.0)])


@pytest.mark.parametrize("count", [0, 3, 5])
def test_exactly_four_corners_are_required(count: int) -> None:
    given = (corner_tuples(NOTE) * 2)[:count]

    with pytest.raises(InvalidCornersError, match=rf"4 corners.*got {count}"):
        BinGridCorners.from_corners(given)


@pytest.mark.parametrize(
    "numbers",
    [
        [(1, 1000), (1, 2000), (101, 1000), (101, 1000)],
        [(1, 1000), (1, 2000), (101, 1000), (102, 2000)],
        [(1, 1000), (1, 2000), (1, 1000), (1, 2000)],
        [(1, 1000), (2, 1000), (3, 1000), (4, 1000)],
        [(1, 1000), (1, 1000), (1, 1000), (1, 1000)],
    ],
    ids=["duplicate", "missing-combination", "one-inline", "one-crossline", "one-node"],
)
def test_numbers_must_be_the_four_combinations_of_two_inlines_and_two_crosslines(
    numbers: list[tuple[int, int]],
) -> None:
    coordinates = [(0.0, 0.0), (0.0, 1.0), (1.0, 0.0), (1.0, 1.0)]
    given = [(i, j, e, n) for (i, j), (e, n) in zip(numbers, coordinates, strict=True)]

    with pytest.raises(InvalidCornersError, match="inline"):
        BinGridCorners.from_corners(given)


def test_labels_given_out_of_place_are_refused() -> None:
    a, b, c, d = BinGridCorners.from_corners(corner_tuples(NOTE))

    with pytest.raises(InvalidCornersError):
        BinGridCorners(a=b, b=a, c=c, d=d)


@pytest.mark.parametrize("number", [1.5, 1.0, True, "1", None])
def test_inline_and_crossline_numbers_must_be_integers(number: Any) -> None:
    with pytest.raises(InvalidCornersError, match="integer"):
        BinGridCorner(number, 1000, 500000.0, 3000000.0)
    with pytest.raises(InvalidCornersError, match="integer"):
        BinGridCorner(1, number, 500000.0, 3000000.0)


def test_numpy_integers_are_numbers() -> None:
    corner = BinGridCorner(np.int64(1), np.int32(1000), np.float32(5.5), 3.0)

    assert (type(corner.inline), type(corner.crossline)) == (int, int)
    assert type(corner.easting) is float


@pytest.mark.parametrize("value", [math.nan, math.inf, -math.inf])
def test_coordinates_must_be_finite(value: float) -> None:
    with pytest.raises(DegenerateBinGridError, match="finite"):
        BinGridCorner(1, 1000, value, 3000000.0)
    with pytest.raises(DegenerateBinGridError, match="finite"):
        BinGridCorner(1, 1000, 500000.0, value)


def test_corners_are_immutable() -> None:
    corners = BinGridCorners.from_corners(corner_tuples(NOTE))

    with pytest.raises(dataclasses.FrozenInstanceError):
        corners.a = corners.b  # type: ignore[misc]


@pytest.mark.parametrize("grid", GRIDS, ids=lambda g: g["case_id"])
def test_corners_of_a_p6_grid_are_those_of_the_independent_reference(
    grid: dict[str, Any],
) -> None:
    corners = corners_from_p6(
        p6(grid["parameters"]),
        inline_range=tuple(grid["inline_range"]),
        crossline_range=tuple(grid["crossline_range"]),
    )

    expected = exact_corners(grid)
    assert [(c.inline, c.crossline) for c in corners] == [e[:2] for e in expected]
    np.testing.assert_allclose(
        corners.coordinates, [e[2:] for e in expected], atol=1e-6
    )


@pytest.mark.parametrize(
    ("inline_range", "crossline_range"),
    [((10, 10), (1, 5)), ((10, 1), (1, 5)), ((1, 5), (7, 3)), ((1.5, 5), (1, 5))],
    ids=["empty-inline", "reversed-inline", "reversed-crossline", "fractional"],
)
def test_p6_corner_ranges_must_be_increasing_integers(
    inline_range: tuple[Any, Any], crossline_range: tuple[Any, Any]
) -> None:
    with pytest.raises(InvalidCornersError):
        corners_from_p6(
            p6(GRIDS[0]["parameters"]),
            inline_range=inline_range,
            crossline_range=crossline_range,
        )


def test_p6_corner_ranges_must_span_whole_node_increments() -> None:
    """The SDU note's grid numbers crosslines every 4: 5161 to 9410 has no node at 9410."""
    grid = p6(
        next(g for g in GRIDS if g["case_id"] == "sdu_note_increments_1_4")[
            "parameters"
        ]
    )

    with pytest.raises(InvalidCornersError, match=r"4249 numbers.*increment 4"):
        corners_from_p6(grid, inline_range=(14100, 17700), crossline_range=(5161, 9410))

    corners = corners_from_p6(
        grid, inline_range=(14100, 17700), crossline_range=(5161, 9409)
    )
    assert corners.crossline_range == (5161, 9409)


def test_json_rendering_is_labelled() -> None:
    rendered = BinGridCorners.from_corners(corner_tuples(NOTE)).to_json_dict()

    assert rendered[1] == {
        "label": "B", "inline": 14100, "crossline": 9409,
        "easting": 491345.95, "northing": 3309043.51,
    }  # fmt: skip
