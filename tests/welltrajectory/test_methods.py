"""The four placement methods, against the geometry each one claims.

Each method is a different model of the same survey, so they are expected to
disagree, and by amounts that can be predicted: a flat tangent plane rises
above a curving ellipsoid, a step drilled deep sweeps a larger angle, a line of
constant azimuth is not a geodesic. The tests pin those amounts, which is what
distinguishes a model from a mistake.
"""

from __future__ import annotations

from typing import get_args

import numpy as np
import pytest

from geodetic_engine.geodesy import UnsupportedCRSError, projection_factors
from geodetic_engine.welltrajectory import (
    LocalFrame,
    Method,
    Survey,
    compute_trajectory,
)
from geodetic_engine.welltrajectory.methods import PLACEMENTS, MethodName

UTM31N = "EPSG:32631"
ON_CENTRAL_MERIDIAN = (500000.0, 6650000.0)  # 3 E, about 60 N
OFF_CENTRAL_MERIDIAN = (666000.0, 6660000.0)
EARTH_RADIUS = 6.39e6  # prime vertical radius near 60 N, good to 0.5 %


def test_the_names_editors_offer_are_the_methods() -> None:
    assert get_args(MethodName.__value__) == tuple(method.value for method in Method)
    assert set(PLACEMENTS) == set(Method)


def _xy(
    survey: Survey,
    wellhead: tuple[float, float],
    method: Method,
    *,
    crs: str = UTM31N,
    north: str = "TN",
) -> np.ndarray:
    trajectory = compute_trajectory(survey, wellhead, crs, north=north, method=method)
    return np.column_stack([trajectory.x, trajectory.y])


def test_the_frame_sits_at_the_wellhead() -> None:
    frame = LocalFrame.at(UTM31N, *ON_CENTRAL_MERIDIAN, height=25.0)

    assert frame.longitude == pytest.approx(3.0, abs=1e-12)
    assert frame.latitude == pytest.approx(59.987, abs=0.001)
    assert frame.height == 25.0
    assert frame.geographic_crs.name == "WGS 84"
    assert frame.to_crs([frame.longitude], [frame.latitude]) == pytest.approx(
        np.array([ON_CENTRAL_MERIDIAN])
    )


@pytest.mark.parametrize("method", list(Method))
@pytest.mark.parametrize(
    ("crs", "wellhead"),
    [
        (UTM31N, (500000.0, 6600000.0)),
        ("EPSG:23032", (500000.0, 6600000.0)),  # ED50 / UTM 32N
        ("EPSG:27572", (600000.0, 2200000.0)),  # NTF (Paris), grads from Paris
        ("EPSG:4230", (9.0, 60.0)),  # ED50 geographic
    ],
    ids=["wgs84", "ed50-utm", "ntf-paris", "ed50"],
)
def test_a_vertical_well_stays_under_its_wellhead(
    method: Method, crs: str, wellhead: tuple[float, float]
) -> None:
    """A datum shift, or a lost prime meridian, would move it by metres or more."""
    if method is Method.GRID_NORTH_LOCAL and crs == "EPSG:4230":
        pytest.skip("grid north local needs a projected CRS")
    survey = Survey([0, 1500, 3000], [0, 0, 0], [0, 0, 0])

    xy = _xy(survey, wellhead, method, crs=crs)

    assert xy == pytest.approx(np.array([wellhead] * 3), abs=1e-6)


def test_grid_north_local_scales_by_k_and_agrees_with_the_projection() -> None:
    """Grid distance is k times ground distance. Dividing by k, as the legacy
    service did, lands 2 (1 - k) of the reach away: 1.6 m here."""
    survey = Survey([0, 3000], [90, 90], [45, 45])
    local = _xy(survey, ON_CENTRAL_MERIDIAN, Method.GRID_NORTH_LOCAL, north="GN")
    projected = _xy(
        survey, ON_CENTRAL_MERIDIAN, Method.AZIMUTHAL_EQUIDISTANT, north="GN"
    )
    k = projection_factors(UTM31N, ON_CENTRAL_MERIDIAN).scale_factor[0]

    assert local == pytest.approx(projected, abs=1e-3)
    reach = local[-1] - np.array(ON_CENTRAL_MERIDIAN)
    divided = np.array(ON_CENTRAL_MERIDIAN) + reach / k**2
    assert np.linalg.norm(divided - projected[-1]) > 1.0


def test_grid_north_local_drifts_only_as_k_and_gamma_change_across_the_reach() -> None:
    survey = Survey([0, 500, 1500, 2500], [0, 20, 60, 70], [0, 30, 45, 50])

    local = _xy(survey, OFF_CENTRAL_MERIDIAN, Method.GRID_NORTH_LOCAL, north="GN")
    projected = _xy(
        survey, OFF_CENTRAL_MERIDIAN, Method.AZIMUTHAL_EQUIDISTANT, north="GN"
    )

    assert 1e-4 < np.abs(local - projected).max() < 0.01


def test_grid_north_local_needs_a_grid() -> None:
    frame = LocalFrame.at("EPSG:4326", 3.0, 60.0, 0.0)

    with pytest.raises(UnsupportedCRSError, match="projected"):
        PLACEMENTS[Method.GRID_NORTH_LOCAL](np.zeros((1, 3)), frame)


def test_at_the_surface_the_tangent_plane_matches_the_projection() -> None:
    """The plane leaves the ellipsoid by d^3 / 3R^2 along it, 0.2 mm at 3 km."""
    survey = Survey([0, 3000], [90, 90], [90, 90])

    enu = _xy(survey, ON_CENTRAL_MERIDIAN, Method.ENU, north="TN")
    projected = _xy(
        survey, ON_CENTRAL_MERIDIAN, Method.AZIMUTHAL_EQUIDISTANT, north="TN"
    )

    assert enu == pytest.approx(projected, abs=1e-3)


@pytest.mark.parametrize("method", [Method.ENU, Method.LMP])
def test_a_step_drilled_deep_lands_further_out(method: Method) -> None:
    """3 km north at 3 km TVD: outward by reach * TVD / R, 1.41 m."""
    survey = Survey([0, 3000, 3000.001, 6000], [0, 0, 90, 90], [0, 0, 0, 0])

    shifted = _xy(survey, ON_CENTRAL_MERIDIAN, method, north="TN")
    projected = _xy(
        survey, ON_CENTRAL_MERIDIAN, Method.AZIMUTHAL_EQUIDISTANT, north="TN"
    )

    east, north = shifted[-1] - projected[-1]
    assert east == pytest.approx(0.0, abs=1e-3)
    assert north == pytest.approx(3000 * 3000 / EARTH_RADIUS, rel=0.01)


def test_lmp_holds_each_steps_azimuth_against_its_own_meridian() -> None:
    """Due east at 60 N, LMP follows the parallel while the projection follows
    the geodesic, which bends south of it by d^2 tan(phi) / 2N."""
    survey = Survey([0, 3000], [90, 90], [90, 90])
    latitude = LocalFrame.at(UTM31N, *ON_CENTRAL_MERIDIAN, 0.0).latitude

    lmp = _xy(survey, ON_CENTRAL_MERIDIAN, Method.LMP, north="TN")
    projected = _xy(
        survey, ON_CENTRAL_MERIDIAN, Method.AZIMUTHAL_EQUIDISTANT, north="TN"
    )

    expected = 3000**2 * np.tan(np.radians(latitude)) / (2 * EARTH_RADIUS)
    assert (lmp - projected)[-1, 1] == pytest.approx(expected, rel=0.01)


def test_the_methods_agree_closely_on_a_short_well() -> None:
    survey = Survey([0, 500, 1500, 2500], [0, 20, 60, 70], [0, 30, 45, 50])
    reference = _xy(survey, ON_CENTRAL_MERIDIAN, Method.AZIMUTHAL_EQUIDISTANT)

    for method in Method:
        assert np.abs(_xy(survey, ON_CENTRAL_MERIDIAN, method) - reference).max() < 0.5


def test_a_geographic_crs_gets_degrees_not_metres() -> None:
    """The legacy service added metre offsets to a longitude in degrees."""
    survey = Survey([0, 1000], [90, 90], [0, 0])

    for method in (Method.AZIMUTHAL_EQUIDISTANT, Method.ENU, Method.LMP):
        longitude, latitude = _xy(
            survey, (4.0, 58.0), method, crs="EPSG:4326", north="TN"
        )[-1]
        assert longitude == pytest.approx(4.0, abs=1e-9)
        assert latitude - 58.0 == pytest.approx(np.degrees(1000 / 6.36e6), rel=0.01)


def test_the_azimuthal_projection_is_reported_as_the_local_crs() -> None:
    survey = Survey([0, 1000], [0, 30], [0, 0])

    trajectory = compute_trajectory(
        survey, ON_CENTRAL_MERIDIAN, "EPSG:23032", north="TN"
    )

    assert trajectory.local_crs is not None
    assert trajectory.local_crs.crs.datum == trajectory.frame.geographic_crs.crs.datum
    assert "Azimuthal Equidistant" in trajectory.local_crs.name
