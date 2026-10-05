"""Properties of the bin grid mathematics, over generated grids.

Round trips must be exact to floating point, and the squaring QC must measure
the shape of the corners and nothing else: not where they are, how the map
grid is rotated, the unit they are in, which way round the grid is, the order
they were given in, or the scale factor declared for the grid.
"""

from __future__ import annotations

import math
from typing import Any

import numpy as np
from hypothesis import given
from hypothesis import strategies as st

from geodetic_engine.bingrid import (
    BinGridCorners,
    Handedness,
    P6Parameters,
    corners_from_p6,
    outline,
    square_up,
)
from tests.bingrid.conftest import signed_area


@st.composite
def grids(draw: st.DrawFn) -> tuple[P6Parameters, tuple[int, int], tuple[int, int]]:
    """A P6 grid with an extent of whole node steps."""
    increment_i = draw(st.integers(1, 8))
    increment_j = draw(st.integers(1, 8))
    parameters = P6Parameters(
        origin_i=draw(st.integers(-1000, 100000)),
        origin_j=draw(st.integers(-1000, 100000)),
        origin_easting=draw(st.floats(-1e6, 4e6)),
        origin_northing=draw(st.floats(0.0, 1.1e7)),
        bin_width_i=draw(st.floats(1.0, 500.0)),
        bin_width_j=draw(st.floats(1.0, 500.0)),
        bearing_j=draw(st.floats(0.0, 360.0, exclude_max=True)),
        handedness=draw(st.sampled_from(Handedness)),
        scale_factor=draw(st.floats(0.98, 1.02)),
        increment_i=increment_i,
        increment_j=increment_j,
    )
    i_min = int(parameters.origin_i) + increment_i * draw(st.integers(-50, 50))
    j_min = int(parameters.origin_j) + increment_j * draw(st.integers(-50, 50))
    i_span = increment_i * draw(st.integers(10, 3000))
    j_span = increment_j * draw(st.integers(10, 3000))
    return parameters, (i_min, i_min + i_span), (j_min, j_min + j_span)


@st.composite
def distorted_corners(draw: st.DrawFn) -> list[tuple[int, int, float, float]]:
    """Corners of a grid, each moved by up to 1 % of the grid's shorter side."""
    parameters, inline_range, crossline_range = draw(grids())
    corners = corners_from_p6(
        parameters, inline_range=inline_range, crossline_range=crossline_range
    )
    side = min(
        math.dist(corners.coordinates[0], corners.coordinates[1]),
        math.dist(corners.coordinates[0], corners.coordinates[2]),
    )
    shift = st.floats(-0.01 * side, 0.01 * side)
    return [
        (c.inline, c.crossline, c.easting + draw(shift), c.northing + draw(shift))
        for c in corners
    ]


def _bearing_difference(a: float, b: float) -> float:
    return abs((a - b + 180.0) % 360.0 - 180.0)


def _fit(corners: list[tuple[int, int, float, float]], **keywords: Any) -> Any:
    return square_up(BinGridCorners.from_corners(corners), **keywords)


@given(
    grids(),
    st.lists(st.tuples(st.floats(-1e4, 1e5), st.floats(-1e4, 1e5)), max_size=20),
)
def test_bin_to_map_to_bin_round_trips(
    grid: Any, offsets: list[tuple[float, float]]
) -> None:
    parameters, _, _ = grid
    nodes = np.array(
        [(parameters.origin_i + di, parameters.origin_j + dj) for di, dj in offsets]
    ).reshape(-1, 2)

    back = parameters.to_bin(parameters.to_map(nodes))

    np.testing.assert_allclose(back, nodes, rtol=0, atol=1e-6)


@given(
    grids(),
    st.lists(st.tuples(st.floats(-2e5, 2e5), st.floats(-2e5, 2e5)), max_size=20),
)
def test_map_to_bin_to_map_round_trips(
    grid: Any, offsets: list[tuple[float, float]]
) -> None:
    parameters, _, _ = grid
    points = np.array(
        [
            (parameters.origin_easting + de, parameters.origin_northing + dn)
            for de, dn in offsets
        ]
    ).reshape(-1, 2)

    back = parameters.to_map(parameters.to_bin(points))

    np.testing.assert_allclose(back, points, rtol=0, atol=1e-6)


@given(grids())
def test_p6_to_corners_to_p6_is_the_identity(grid: Any) -> None:
    parameters, inline_range, crossline_range = grid
    corners = corners_from_p6(
        parameters, inline_range=inline_range, crossline_range=crossline_range
    )

    result = square_up(
        corners,
        scale_factor=parameters.scale_factor,
        increment_i=parameters.increment_i,
        increment_j=parameters.increment_j,
    )

    fitted = result.parameters
    assert fitted.handedness is parameters.handedness
    assert _bearing_difference(fitted.bearing_j, parameters.bearing_j) < 1e-8
    assert math.isclose(fitted.bin_width_i, parameters.bin_width_i, rel_tol=1e-9)
    assert math.isclose(fitted.bin_width_j, parameters.bin_width_j, rel_tol=1e-9)
    assert result.max_mislocation.di < 1e-6
    assert result.max_mislocation.dj < 1e-6
    np.testing.assert_allclose(
        fitted.to_map(corners.numbers), corners.coordinates, rtol=0, atol=1e-5
    )


@given(distorted_corners(), st.floats(-1e6, 1e6), st.floats(-1e6, 1e6))
def test_misfit_does_not_depend_on_where_the_grid_is(
    corners: list[tuple[int, int, float, float]], dx: float, dy: float
) -> None:
    moved = [(i, j, e + dx, n + dy) for i, j, e, n in corners]

    here, there = _fit(corners), _fit(moved)

    assert math.isclose(here.max_mislocation.di, there.max_mislocation.di, abs_tol=1e-6)
    assert math.isclose(here.max_mislocation.dj, there.max_mislocation.dj, abs_tol=1e-6)
    assert math.isclose(
        here.parameters.bin_width_i, there.parameters.bin_width_i, rel_tol=1e-9
    )
    assert (
        _bearing_difference(here.parameters.bearing_j, there.parameters.bearing_j)
        < 1e-7
    )


@given(distorted_corners(), st.floats(0.0, 360.0))
def test_rotating_the_corners_rotates_the_bearing_and_nothing_else(
    corners: list[tuple[int, int, float, float]], angle: float
) -> None:
    turn = math.radians(angle)
    cx = sum(c[2] for c in corners) / 4
    cy = sum(c[3] for c in corners) / 4
    rotated = [
        (
            i,
            j,
            cx + (e - cx) * math.cos(turn) + (n - cy) * math.sin(turn),
            cy - (e - cx) * math.sin(turn) + (n - cy) * math.cos(turn),
        )
        for i, j, e, n in corners
    ]

    here, there = _fit(corners), _fit(rotated)

    expected = (here.parameters.bearing_j + angle) % 360.0
    assert _bearing_difference(there.parameters.bearing_j, expected) < 1e-7
    assert there.parameters.handedness is here.parameters.handedness
    assert math.isclose(here.max_mislocation.di, there.max_mislocation.di, abs_tol=1e-6)
    assert math.isclose(here.max_mislocation.dj, there.max_mislocation.dj, abs_tol=1e-6)


@given(distorted_corners(), st.floats(0.1, 10.0))
def test_a_change_of_unit_scales_the_widths_and_nothing_else(
    corners: list[tuple[int, int, float, float]], factor: float
) -> None:
    scaled = [(i, j, e * factor, n * factor) for i, j, e, n in corners]

    here, there = _fit(corners), _fit(scaled)

    assert math.isclose(
        there.parameters.bin_width_i, factor * here.parameters.bin_width_i, rel_tol=1e-9
    )
    assert math.isclose(
        there.parameters.bin_width_j, factor * here.parameters.bin_width_j, rel_tol=1e-9
    )
    assert math.isclose(here.max_mislocation.di, there.max_mislocation.di, abs_tol=1e-6)
    assert math.isclose(here.max_mislocation.dj, there.max_mislocation.dj, abs_tol=1e-6)


@given(distorted_corners())
def test_a_mirrored_grid_has_the_other_handedness_and_the_same_misfit(
    corners: list[tuple[int, int, float, float]],
) -> None:
    mirrored = [(i, j, -e, n) for i, j, e, n in corners]

    here, there = _fit(corners), _fit(mirrored)

    assert there.parameters.handedness is not here.parameters.handedness
    expected = (360.0 - here.parameters.bearing_j) % 360.0
    assert _bearing_difference(there.parameters.bearing_j, expected) < 1e-7
    assert math.isclose(here.max_mislocation.di, there.max_mislocation.di, abs_tol=1e-6)
    assert math.isclose(here.max_mislocation.dj, there.max_mislocation.dj, abs_tol=1e-6)


@given(distorted_corners(), st.permutations(range(4)))
def test_the_order_corners_are_given_in_does_not_matter(
    corners: list[tuple[int, int, float, float]], order: list[int]
) -> None:
    shuffled = [corners[index] for index in order]

    assert _fit(shuffled) == _fit(corners)


@given(distorted_corners(), st.floats(0.9, 1.1))
def test_the_declared_scale_factor_does_not_change_the_misfit(
    corners: list[tuple[int, int, float, float]], scale_factor: float
) -> None:
    """Java defect D2 made the misfit grow with |1 - k|; here k only rescales widths."""
    plain = _fit(corners)
    scaled = _fit(corners, scale_factor=scale_factor)

    assert math.isclose(
        plain.max_mislocation.di, scaled.max_mislocation.di, abs_tol=1e-9
    )
    assert math.isclose(
        plain.max_mislocation.dj, scaled.max_mislocation.dj, abs_tol=1e-9
    )
    assert math.isclose(
        scaled.parameters.bin_width_i * scale_factor,
        plain.parameters.bin_width_i,
        rel_tol=1e-12,
    )
    np.testing.assert_allclose(
        scaled.squared_corners.coordinates,
        plain.squared_corners.coordinates,
        rtol=0,
        atol=1e-6,
    )


@given(distorted_corners())
def test_the_outline_is_closed_counterclockwise_and_made_of_the_corners(
    corners: list[tuple[int, int, float, float]],
) -> None:
    labelled = BinGridCorners.from_corners(corners)

    ring = outline(labelled)

    points = ring.coordinates
    assert points[0] == points[-1]
    assert signed_area(points) > 0
    assert sorted(points[:4]) == sorted((c.easting, c.northing) for c in labelled)


@given(distorted_corners())
def test_the_bearing_is_always_in_a_full_circle(
    corners: list[tuple[int, int, float, float]],
) -> None:
    bearing = _fit(corners).parameters.bearing_j

    assert 0.0 <= bearing < 360.0
