"""Projection factors, and the sign convention they are reported in.

Every expected value here comes from somewhere other than PROJ's factor
routine: a closed form, or a grid bearing measured by transforming two points
along a meridian. A test that only compared ``projection_factors`` against
``Proj.get_factors`` would agree with PROJ whichever sign it used.
"""

from __future__ import annotations

import json
import math
from typing import Any

import numpy as np
import pytest
from pyproj import CRS, Transformer
from pyproj.crs import BoundCRS, CoordinateOperation

from geodetic_engine.geodesy import (
    ProjectionFactors,
    TransformationFailedError,
    UnsupportedCRSError,
    projection_factors,
)
from geodetic_engine.persistablereference import to_persistable_reference

UTM31N = "EPSG:32631"  # central meridian 3 E


def _grid_bearing_of_true_north(crs: str, longitude: float, latitude: float) -> float:
    """Grid azimuth of the local meridian, measured with two transformed points.

    Grid north sits gamma clockwise of true north, so true north sits at grid
    azimuth ``-gamma``.
    """
    projected = CRS(crs)
    geodetic = projected.geodetic_crs
    assert geodetic is not None
    to_grid = Transformer.from_crs(geodetic, projected, always_xy=True)
    x1, y1 = to_grid.transform(longitude, latitude)
    x2, y2 = to_grid.transform(longitude, latitude + 1e-4)
    return math.degrees(math.atan2(x2 - x1, y2 - y1))


def _utm_convergence(longitude: float, latitude: float, central: float) -> float:
    """The closed form for transverse Mercator, good to a few 1e-6 degrees here."""
    delta = math.radians(longitude - central)
    return math.degrees(math.atan(math.tan(delta) * math.sin(math.radians(latitude))))


def test_on_the_central_meridian_there_is_no_convergence_and_k_is_k0() -> None:
    factors = projection_factors(UTM31N, (500000.0, 6600000.0))

    assert factors.projected
    assert factors.grid_convergence[0] == pytest.approx(0.0, abs=1e-12)
    # PROJ differentiates numerically, so k0 comes back good to about 1e-10.
    assert factors.scale_factor[0] == pytest.approx(0.9996, abs=1e-9)
    assert factors.longitude[0] == pytest.approx(3.0, abs=1e-9)


@pytest.mark.parametrize(
    ("longitude", "latitude"),
    [(6.0, 60.0), (0.0, 60.0), (6.0, -40.0), (0.5, -10.0)],
    ids=["north-east", "north-west", "south-east", "south-west"],
)
def test_grid_north_is_east_of_true_north_where_the_sign_is_positive(
    longitude: float, latitude: float
) -> None:
    factors = projection_factors(UTM31N, (longitude, latitude), geographic=True)
    gamma = float(factors.grid_convergence[0])

    assert gamma == pytest.approx(_utm_convergence(longitude, latitude, 3.0), abs=1e-4)
    assert -gamma == pytest.approx(
        _grid_bearing_of_true_north(UTM31N, longitude, latitude), abs=1e-4
    )


def test_projected_input_lands_on_the_same_factors_as_its_geographic_position() -> None:
    by_grid = projection_factors(UTM31N, (333978.556, 6655205.2))
    by_position = projection_factors(
        UTM31N, (by_grid.longitude[0], by_grid.latitude[0]), geographic=True
    )

    assert by_grid.grid_convergence == pytest.approx(by_position.grid_convergence)
    assert by_grid.scale_factor == pytest.approx(by_position.scale_factor)


def test_mercator_scale_matches_the_closed_form() -> None:
    latitude = 60.0
    ellipsoid = CRS("EPSG:3395").ellipsoid
    assert ellipsoid is not None
    e2 = 1 - (ellipsoid.semi_minor_metre / ellipsoid.semi_major_metre) ** 2
    phi = math.radians(latitude)
    expected = math.sqrt(1 - e2 * math.sin(phi) ** 2) / math.cos(phi)

    factors = projection_factors("EPSG:3395", (10.0, latitude), geographic=True)

    assert factors.scale_factor[0] == pytest.approx(expected, rel=1e-8)
    assert factors.meridional_scale[0] == pytest.approx(expected, rel=1e-8)
    assert factors.angular_distortion[0] == pytest.approx(0.0, abs=1e-9)


def test_a_non_greenwich_prime_meridian_is_honoured() -> None:
    """NTF (Paris) is in grads from Paris, and PROJ reads longitude from Paris.

    Handing PROJ a Greenwich longitude here doubles the convergence.
    """
    crs = "EPSG:27572"  # NTF (Paris) / Lambert zone II, grads from Paris
    geodetic = CRS(crs).geodetic_crs
    assert geodetic is not None
    to_grid = Transformer.from_crs(geodetic, crs, always_xy=True)
    grid = to_grid.transform(2.6, 52.0)  # grads, east of the Paris meridian

    factors = projection_factors(crs, grid)

    x1, y1 = grid
    x2, y2 = to_grid.transform(2.6, 52.0 + 1e-4)
    bearing = math.degrees(math.atan2(x2 - x1, y2 - y1))
    assert factors.longitude[0] == pytest.approx(2.6, abs=1e-9)
    assert -factors.grid_convergence[0] == pytest.approx(bearing, abs=1e-4)


def test_factors_are_unitless_whatever_the_crs_unit() -> None:
    """California zone 1 in US survey feet and in metres, at the same place."""
    in_feet = projection_factors("EPSG:2225", (6561666.667, 1640416.667))
    in_metres = projection_factors(
        "EPSG:26941",
        (in_feet.longitude[0], in_feet.latitude[0]),
        geographic=True,
    )

    assert in_feet.scale_factor == pytest.approx(in_metres.scale_factor, rel=1e-12)
    assert in_feet.grid_convergence == pytest.approx(in_metres.grid_convergence)


def test_a_bound_crs_and_its_payload_read_through_their_base() -> None:
    bound = BoundCRS(
        CRS("EPSG:23032"), CRS("EPSG:4326"), CoordinateOperation.from_epsg(1133)
    )
    point = (600000.0, 6643000.0)
    base = projection_factors("EPSG:23032", point)

    for crs in (bound, to_persistable_reference(bound)):
        factors = projection_factors(crs, point)
        assert factors.grid_convergence == pytest.approx(base.grid_convergence)
        assert factors.scale_factor == pytest.approx(base.scale_factor)


def test_a_compound_crs_reads_through_its_horizontal_part() -> None:
    point = (600000.0, 7000000.0, 120.0)

    compound = projection_factors("EPSG:6172", point)
    horizontal = projection_factors("EPSG:11022", point[:2])

    assert compound.grid_convergence == pytest.approx(horizontal.grid_convergence)
    assert compound.scale_factor == pytest.approx(horizontal.scale_factor)


def test_a_geographic_crs_has_no_grid() -> None:
    factors = projection_factors("EPSG:4326", [(6.0, 60.0), (7.0, 61.0)])

    assert not factors.projected
    assert factors.grid_convergence.tolist() == [0.0, 0.0]
    assert factors.scale_factor.tolist() == [1.0, 1.0]


@pytest.mark.parametrize("crs", ["EPSG:4978", "EPSG:5776", "EPSG:5817"])
def test_a_crs_without_a_map_projection_or_position_is_refused(crs: str) -> None:
    with pytest.raises(UnsupportedCRSError):
        projection_factors(crs, (1.0, 2.0))


def test_many_points_come_back_one_entry_each() -> None:
    points = np.column_stack([np.linspace(4e5, 6e5, 7), np.full(7, 6.6e6)])

    factors = projection_factors(UTM31N, points)

    assert factors.coordinates.shape == (7, 2)
    for name in ("longitude", "grid_convergence", "scale_factor", "areal_scale"):
        assert getattr(factors, name).shape == (7,)
    assert np.all(np.diff(factors.grid_convergence) > 0)


@pytest.mark.parametrize("points", [[1.0], [[1.0, 2.0, 3.0, 4.0]], [], [[[1.0, 2.0]]]])
def test_points_that_are_not_two_or_three_values_are_refused(
    points: list[Any],
) -> None:
    with pytest.raises(ValueError, match="one point"):
        projection_factors(UTM31N, points)


def test_a_position_proj_cannot_evaluate_is_an_error() -> None:
    with pytest.raises(TransformationFailedError):
        projection_factors(UTM31N, (6.0, 95.0), geographic=True)


def test_azimuths_turn_between_grid_and_true_north() -> None:
    factors = projection_factors(UTM31N, (6.0, 60.0), geographic=True)
    gamma = float(factors.grid_convergence[0])
    azimuths = np.array([0.0, 1.0, 180.0, 359.5])

    assert factors.to_grid_azimuth(0.0) == pytest.approx(360.0 - gamma)
    assert factors.to_true_azimuth(factors.to_grid_azimuth(azimuths)) == (
        pytest.approx(azimuths)
    )


def test_the_json_form_is_serialisable_and_states_its_convention() -> None:
    factors = projection_factors(UTM31N, [(500000.0, 6600000.0), (6e5, 6.7e6)])

    document = json.loads(json.dumps(factors.to_json_dict()))

    assert document["crs"] == UTM31N
    assert document["projected"] is True
    assert "grid azimuth = true azimuth - grid_convergence" in document["convention"]
    assert [point["coordinates"] for point in document["points"]] == [
        [500000.0, 6600000.0],
        [600000.0, 6700000.0],
    ]
    assert isinstance(factors, ProjectionFactors)
