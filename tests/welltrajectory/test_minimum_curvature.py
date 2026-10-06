"""The minimum curvature method, against geometry rather than against itself.

A circular arc has closed forms, so the cases below are built from arcs whose
radius, depth and departure can be written down by hand. The legacy fixtures
are compared only where they agree with those; where they do not, a strict
xfail names the defect.
"""

from __future__ import annotations

import numpy as np
import pytest

from geodetic_engine.welltrajectory import (
    DegenerateSurveyError,
    InvalidSurveyError,
    MinimumCurvature,
)

from .conftest import legacy

DEGREE = np.pi / 180


def _survey(rows: list[list[float]]) -> MinimumCurvature:
    table = np.asarray(rows, dtype=float)
    return MinimumCurvature(table[:, 0], table[:, 1] * DEGREE, table[:, 2] * DEGREE)


def test_a_vertical_well_goes_straight_down() -> None:
    stations = MinimumCurvature([0, 500, 1800], [0, 0, 0], [0, 1.2, 3.0]).stations

    assert stations.east.tolist() == [0.0, 0.0, 0.0]
    assert stations.north.tolist() == [0.0, 0.0, 0.0]
    assert stations.tvd.tolist() == [0.0, 500.0, 1800.0]
    assert stations.dls().tolist() == [0.0, 0.0, 0.0]


@pytest.mark.parametrize("build", [1e-7, 10.0, 90.0, 170.0])
@pytest.mark.parametrize("azimuth", [0.0, 37.0, 245.0])
def test_a_constant_build_follows_its_circle(build: float, azimuth: float) -> None:
    length, theta = 800.0, build * DEGREE
    radius = length / theta

    stations = MinimumCurvature([0, length], [0, theta], [azimuth * DEGREE] * 2)

    # 2 sin^2(theta / 2) rather than 1 - cos(theta), which cancels to nothing.
    departure = radius * 2 * np.sin(theta / 2) ** 2
    east, north, tvd = stations.stations.offsets[-1]
    assert tvd == pytest.approx(radius * np.sin(theta), rel=1e-12, abs=1e-9)
    assert east == pytest.approx(departure * np.sin(azimuth * DEGREE), abs=1e-9)
    assert north == pytest.approx(departure * np.cos(azimuth * DEGREE), abs=1e-9)


def test_a_horizontal_turn_follows_its_circle() -> None:
    model = _survey([[0, 90, 0], [2000, 90, 90]])

    east, north, tvd = model.stations.offsets[-1]

    assert (east, north, tvd) == pytest.approx((4000 / np.pi, 4000 / np.pi, 0.0))
    assert model.stations.dls()[-1] == pytest.approx(90 / 2000 * 30)


def test_no_step_is_longer_than_the_hole_drilled() -> None:
    random = np.random.default_rng(84246)
    md = np.cumsum(random.uniform(1, 300, 200))
    inclination = random.uniform(0, np.pi, 200)
    azimuth = random.uniform(0, 2 * np.pi, 200)
    keep = np.concatenate([[True], np.abs(np.diff(inclination)) < 2.5])

    stations = MinimumCurvature(md[keep], inclination[keep], azimuth[keep]).stations

    steps = np.linalg.norm(np.diff(stations.offsets, axis=0), axis=1)
    assert np.all(steps <= np.diff(stations.md) * (1 + 1e-12))


def test_the_ratio_factor_is_continuous_where_its_series_takes_over() -> None:
    either_side = [0.99e-4, 1.01e-4]
    tvd = [
        MinimumCurvature([0, 1], [0, beta], [0, 0]).stations.tvd[-1]
        for beta in either_side
    ]

    for beta, value in zip(either_side, tvd, strict=True):
        assert value == pytest.approx(np.sin(beta) / beta, rel=1e-15)


def test_resampled_stations_reproduce_the_survey_they_came_from() -> None:
    """Minimum curvature is closed under its own arc interpolation."""
    model = _survey(legacy("simple_survey_input")["rows"])

    resampled = model.resample(7.3)
    again = MinimumCurvature(resampled.md, resampled.inclination, resampled.azimuth)

    assert np.abs(again.stations.offsets - resampled.offsets).max() < 1e-9
    kept = resampled.offsets[resampled.is_survey_station]
    assert np.abs(kept - model.stations.offsets).max() < 1e-9


def test_interpolating_at_a_station_returns_it_exactly() -> None:
    model = _survey(legacy("simple_survey_input")["rows"])
    stations = model.stations

    points = model.interpolate(stations.md[::-1])

    assert points.md.tolist() == stations.md[::-1].tolist()
    assert np.array_equal(points.offsets, stations.offsets[::-1])
    assert np.array_equal(points.azimuth, stations.azimuth[::-1])
    assert points.is_survey_station.all()


def test_a_point_part_way_round_an_arc_is_on_the_arc() -> None:
    length, theta = 1000.0, 60 * DEGREE
    model = MinimumCurvature([0, length], [0, theta], [30 * DEGREE] * 2)

    point = model.interpolate([250.0])

    turned, radius = theta / 4, length / theta
    assert point.inclination[0] == pytest.approx(turned)
    assert point.azimuth[0] == pytest.approx(30 * DEGREE)
    assert point.tvd[0] == pytest.approx(radius * np.sin(turned))
    assert np.hypot(point.east[0], point.north[0]) == pytest.approx(
        radius * (1 - np.cos(turned))
    )
    assert point.dls()[0] == pytest.approx(60 / 1000 * 30)
    assert not point.is_survey_station[0]


def test_a_depth_outside_the_survey_is_refused() -> None:
    model = MinimumCurvature([100, 200], [0, 0.1], [0, 0])

    with pytest.raises(InvalidSurveyError, match="outside"):
        model.interpolate([99.0, 150.0])


def test_resampling_keeps_both_ends_and_optionally_the_survey() -> None:
    model = MinimumCurvature([0, 105, 210.5], [0, 0.1, 0.2], [0, 0, 0])

    assert model.resample(100).md.tolist() == [0, 100, 105, 200, 210.5]
    assert model.resample(100, include_survey=False).md.tolist() == [
        0,
        100,
        200,
        210.5,
    ]
    assert model.resample(0.1).md[1050] == 105.0


@pytest.mark.parametrize(
    ("md", "inclination", "match"),
    [
        ([0], [0], "at least two"),
        ([0, 100, 100], [0, 0, 0], "strictly increase"),
        ([0, 100], [0, 3.5], "between 0 and pi"),
        ([0, np.nan], [0, 0], "finite"),
    ],
)
def test_a_survey_that_describes_no_wellbore_is_refused(
    md: list[float], inclination: list[float], match: str
) -> None:
    with pytest.raises(InvalidSurveyError, match=match):
        MinimumCurvature(md, inclination, np.zeros(len(md)))


def test_a_reversal_does_not_determine_a_unique_arc() -> None:
    with pytest.raises(DegenerateSurveyError, match="reverses"):
        MinimumCurvature([0, 100], [0, np.pi], [0, 0])


@pytest.mark.parametrize("gap", [1e-3, 1e-5])
def test_near_reversal_arcs_have_finite_radius_and_chord(gap: float) -> None:
    length = 1000.0
    dogleg = np.pi - gap
    radius = length / dogleg

    model = MinimumCurvature([0, length], [0, dogleg], [0, 0])

    chord = np.linalg.norm(model.stations.offsets[-1])
    assert chord == pytest.approx(2 * radius * np.sin(dogleg / 2), rel=1e-10)
    assert np.all(np.isfinite(model.interpolate([length / 2]).offsets))


_EXPECTED = legacy("expected_interpolated_trajectory")["rows"]


@pytest.mark.parametrize(
    "row", [row for row in _EXPECTED if row[0] <= 3000], ids=lambda row: f"md{row[0]}"
)
def test_the_legacy_build_section_agrees(row: list[float]) -> None:
    """Every legacy row up to MD 3000 is on the arcs, to the digits it gives."""
    model = _survey(legacy("simple_survey_input")["rows"])

    point = model.interpolate([row[0]])

    assert point.inclination[0] / DEGREE == pytest.approx(row[1], abs=1e-9)
    assert point.offsets[0] == pytest.approx(row[3:6], abs=1e-8)
    assert point.dls()[0] == pytest.approx(row[6], abs=1e-9)


@pytest.mark.xfail(
    strict=True,
    reason=(
        "legacy defect: a horizontal 90 degree turn over MD 3000-5000 moves the "
        "hole 4000/pi m east and north, but the legacy table has it move 1636.6 m "
        "east and none north, with a dogleg severity of 0"
    ),
)
@pytest.mark.parametrize("md", [5000, 6000])
def test_the_legacy_turn_section_is_wrong(md: float) -> None:
    model = _survey(legacy("simple_survey_input")["rows"])
    row = next(row for row in _EXPECTED if row[0] == md)

    point = model.interpolate([md])

    assert point.offsets[0] == pytest.approx(row[3:6], abs=1e-3)
