"""Converting, squaring and placing a bin grid with real CRSs.

The cases are the OSDU acceptance tests' grid: 100 inlines by 1000 crosslines of
1000 x 100 m bins in WGS 84 / UTM zone 15N, and the same grid converted to
NAD27 / BLM 14N (ftUS) bound to WGS 84 by NAD27 to WGS 84 (79). Converting it
six degrees west of its own zone's central meridian bends it enough to measure:
0.38 of a crossline at the worst corner.
"""

from __future__ import annotations

import json
from typing import Any

import numpy as np
import pytest
from pyproj import CRS, Geod, Proj, Transformer
from pyproj.crs import BoundCRS, CoordinateOperation

from geodetic_engine.bingrid import (
    BinGridCorners,
    BinGridResult,
    Handedness,
    P6Parameters,
    UnsupportedCRSError,
    convert_bin_grid,
    corners_from_p6,
)
from geodetic_engine.geodesy import AmbiguousOperationError, OperationRoute
from tests.bingrid.conftest import corner_tuples, load, osdu_outcome

ACCEPTANCE = corner_tuples(
    next(
        c
        for c in load("sdu_spreadsheet_cases.json")["cases"]
        if c["case_id"] == "sdu_test1a"
    )
)
BLM_14N = BoundCRS(
    CRS.from_epsg(32064),
    CRS.from_epsg(4326),
    CoordinateOperation.from_authority("EPSG", 15851),
)


def _expected(case_id: str) -> tuple[dict[str, Any], dict[str, float]]:
    outcome = osdu_outcome(case_id)
    return outcome["expected"], outcome["tolerances"]


def test_without_a_target_the_grid_is_squared_where_it_is() -> None:
    expected, tolerance = _expected("without_to_crs")

    result = convert_bin_grid(ACCEPTANCE, "EPSG:32615")

    assert result.crs is result.source_crs
    assert not result.converted
    assert result.converted_corners is None
    assert result.conversion is None
    assert result.linear_unit == "metre"
    p = result.parameters
    assert (p.method_code, p.bearing_j) == (9666, 0.0)
    assert (p.grid_step_i, p.grid_step_j) == pytest.approx((1000.0, 100.0), rel=1e-12)
    assert (result.max_mislocation.di, result.max_mislocation.dj) == pytest.approx(
        (0.0, 0.0), abs=1e-9
    )
    np.testing.assert_allclose(
        result.wgs84_corners, expected["wgs84"], rtol=0, atol=tolerance["wgs84"]
    )
    assert result.wgs84_outline is not None
    assert result.wgs84_outline.labels == ("A", "C", "D", "B", "A")
    assert result.outline.labels == ("A", "C", "D", "B", "A")


def test_wgs84_corners_are_those_pyproj_gives_directly() -> None:
    result = convert_bin_grid(ACCEPTANCE, "EPSG:32615")

    direct = Transformer.from_crs(32615, 4326, always_xy=True)
    expected = [direct.transform(e, n) for _, _, e, n in ACCEPTANCE]
    np.testing.assert_allclose(result.wgs84_corners, expected, rtol=0, atol=1e-12)


def test_conversion_to_a_bound_crs_squares_the_grid_up_there() -> None:
    expected, tolerance = _expected("with_to_crs")

    result = convert_bin_grid(ACCEPTANCE, "EPSG:32615", target_crs=BLM_14N)

    assert result.converted
    assert result.linear_unit == "US survey foot"
    assert result.conversion is not None
    assert result.conversion.operation.route is OperationRoute.BOUND
    assert (
        result.conversion.operation.authority_code == expected["conversion_operation"]
    )
    assert result.converted_corners is not None
    np.testing.assert_allclose(
        result.converted_corners.coordinates,
        [c[3:] for c in expected["converted_corners"]],
        rtol=0,
        atol=expected["converted_corners_tolerance"],
    )
    p, reference = result.parameters, expected["p6"]
    assert p.method_code == reference["method_code"]
    assert (p.origin_i, p.origin_j) == (reference["origin_i"], reference["origin_j"])
    assert p.origin_easting == pytest.approx(
        reference["origin_easting"], abs=tolerance["origin"]
    )
    assert p.origin_northing == pytest.approx(
        reference["origin_northing"], abs=tolerance["origin"]
    )
    # Java states k = 1, so its bin widths are the map grid spacing.
    assert p.grid_step_i == pytest.approx(
        reference["bin_width_i"], abs=tolerance["bin_width"]
    )
    assert p.grid_step_j == pytest.approx(
        reference["bin_width_j"], abs=tolerance["bin_width"]
    )
    assert p.bearing_j == pytest.approx(
        reference["bearing_j"], abs=tolerance["bearing"]
    )
    mislocation = result.max_mislocation
    assert mislocation.di == pytest.approx(
        expected["max_mislocation"]["di"], abs=tolerance["mislocation"]
    )
    assert mislocation.dj == pytest.approx(
        expected["max_mislocation"]["dj"], abs=tolerance["mislocation"]
    )
    assert (round(mislocation.di, 2), round(mislocation.dj, 2)) == (0.0, 0.38)
    np.testing.assert_allclose(
        result.squared_corners.coordinates,
        [c[3:] for c in expected["corners"]],
        rtol=0,
        atol=tolerance["map"],
    )
    np.testing.assert_allclose(
        result.wgs84_corners, expected["wgs84"], rtol=0, atol=tolerance["wgs84"]
    )


SQUARED_IN = [
    pytest.param({}, CRS.from_epsg(32615), id="utm-15n"),
    pytest.param({"target_crs": BLM_14N}, CRS.from_epsg(32064), id="blm-14n-ftus"),
]


def _ground_spacing_at_centre(result: BinGridResult, crs: CRS) -> tuple[float, float]:
    """Ellipsoidal length of one inline and one crossline step at the centre, in m."""
    to_lonlat = Transformer.from_crs(crs, crs.geodetic_crs, always_xy=True)
    geod = Geod(a=crs.ellipsoid.semi_major_metre, b=crs.ellipsoid.semi_minor_metre)
    i = sum(result.squared_corners.inline_range) / 2
    j = sum(result.squared_corners.crossline_range) / 2
    ends = result.parameters.to_map(
        [(i - 0.5, j), (i + 0.5, j), (i, j - 0.5), (i, j + 0.5)]
    )
    a, b, c, d = (to_lonlat.transform(*point) for point in ends)
    return geod.inv(*a, *b)[2], geod.inv(*c, *d)[2]


@pytest.mark.parametrize(("keywords", "crs"), SQUARED_IN)
def test_the_bin_widths_are_the_ground_spacing_of_the_bins_at_the_centre(
    keywords: dict[str, Any], crs: CRS
) -> None:
    result = convert_bin_grid(ACCEPTANCE, "EPSG:32615", wgs84=False, **keywords)

    metre = crs.axis_info[0].unit_conversion_factor
    p = result.parameters
    assert (p.bin_width_i * metre, p.bin_width_j * metre) == pytest.approx(
        _ground_spacing_at_centre(result, crs), rel=1e-8
    )


@pytest.mark.parametrize(("keywords", "crs"), SQUARED_IN)
def test_the_scale_factor_is_the_projections_scale_at_the_grid(
    keywords: dict[str, Any], crs: CRS
) -> None:
    """EPSG's bin grid scale factor: the point scale factor at the grid centre."""
    result = convert_bin_grid(ACCEPTANCE, "EPSG:32615", wgs84=False, **keywords)

    to_lonlat = Transformer.from_crs(crs, crs.geodetic_crs, always_xy=True)
    centre = to_lonlat.transform(*result.squared_corners.coordinates.mean(axis=0))
    point_scale = Proj(crs).get_factors(*centre).meridional_scale
    assert result.parameters.scale_factor == pytest.approx(point_scale, rel=1e-9)


@pytest.mark.parametrize(
    ("crs", "origin", "k0"),
    [
        pytest.param("EPSG:27572", (600000.0, 2200000.0), 0.99987742, id="grads-paris"),
        pytest.param("EPSG:20790", (200000.0, 300000.0), 1.0, id="lisbon-meridian"),
    ],
)
def test_the_scale_factor_holds_whatever_the_geographic_crs_states(
    crs: str, origin: tuple[float, float], k0: float
) -> None:
    """A grid centred on the projection's origin, where the scale factor is k0."""
    grid = P6Parameters(
        origin_i=1, origin_j=1,
        origin_easting=origin[0] - 5000.0, origin_northing=origin[1] - 5000.0,
        bin_width_i=25.0, bin_width_j=25.0, bearing_j=0.0, handedness=Handedness.RIGHT,
    )  # fmt: skip
    corners = corners_from_p6(grid, inline_range=(1, 401), crossline_range=(1, 401))

    result = convert_bin_grid(corners, crs, wgs84=False)

    assert result.parameters.scale_factor == pytest.approx(k0, rel=1e-9)


@pytest.mark.parametrize(
    ("crs", "centre"),
    [
        pytest.param("EPSG:5070", (-96.0, 37.5), id="albers-equal-area"),
        pytest.param("EPSG:30200", (-61.0, 10.4), id="cassini-36-km-off-meridian"),
    ],
)
def test_a_projection_not_conformal_at_the_grid_needs_a_stated_scale_factor(
    crs: str, centre: tuple[float, float]
) -> None:
    """Its scale depends on direction, so no one k makes both widths ground ones."""
    projected = CRS.from_user_input(crs)
    to_map = Transformer.from_crs(projected.geodetic_crs, projected, always_xy=True)
    easting, northing = to_map.transform(*centre)
    grid = P6Parameters(
        origin_i=1, origin_j=1, origin_easting=easting, origin_northing=northing,
        bin_width_i=25.0, bin_width_j=25.0, bearing_j=30.0, handedness=Handedness.RIGHT,
    )  # fmt: skip
    corners = corners_from_p6(grid, inline_range=(1, 401), crossline_range=(1, 401))

    with pytest.raises(UnsupportedCRSError, match="not conformal"):
        convert_bin_grid(corners, crs, wgs84=False)

    stated = convert_bin_grid(corners, crs, scale_factor=1.0, wgs84=False)
    assert stated.parameters.scale_factor == 1.0


def test_an_explicit_scale_factor_is_used_and_moves_nothing() -> None:
    derived = convert_bin_grid(
        ACCEPTANCE, "EPSG:32615", target_crs=BLM_14N, wgs84=False
    )
    stated = convert_bin_grid(
        ACCEPTANCE, "EPSG:32615", target_crs=BLM_14N, scale_factor=1.0, wgs84=False
    )

    assert stated.parameters.scale_factor == 1.0
    assert stated.parameters.bin_width_i == pytest.approx(
        derived.parameters.grid_step_i, rel=1e-12
    )
    np.testing.assert_allclose(
        stated.squared_corners.coordinates,
        derived.squared_corners.coordinates,
        rtol=0,
        atol=1e-6,
    )
    assert stated.max_mislocation.dj == pytest.approx(
        derived.max_mislocation.dj, abs=1e-9
    )


def test_applied_operations_name_both_crss_and_every_operation() -> None:
    result = convert_bin_grid(ACCEPTANCE, "EPSG:32615", target_crs=BLM_14N)

    steps = result.applied_operations()

    assert len(steps) == 3
    assert "WGS 84 / UTM zone 15N" in steps[0]
    assert "NAD27 / BLM 14N (ftUS)" in steps[0]
    assert "EPSG:15851" in steps[0]
    assert "dI=0.00 inline numbers, dJ=0.38 crossline numbers" in steps[1]
    assert "WGS 84" in steps[2]
    assert "EPSG:15851" in steps[2]


def test_a_datum_change_needs_an_operation_unless_a_crs_is_bound() -> None:
    with pytest.raises(AmbiguousOperationError):
        convert_bin_grid(ACCEPTANCE, "EPSG:32615", target_crs="EPSG:26715", wgs84=False)

    result = convert_bin_grid(
        ACCEPTANCE,
        "EPSG:32615",
        target_crs="EPSG:26715",
        operation="EPSG:15851",
        wgs84_operation="EPSG:15851",
    )

    assert result.conversion is not None
    assert result.conversion.operation.authority_code == "EPSG:15851"
    assert result.wgs84_conversion is not None
    assert result.wgs84_conversion.operation.authority_code == "EPSG:15851"


def test_an_unbound_crs_of_another_datum_has_no_wgs84_corners() -> None:
    """The OSDU acceptance test's 'invalid CRS': WGS 72 / UTM zone 21N."""
    with pytest.raises(AmbiguousOperationError, match="datum change"):
        convert_bin_grid(ACCEPTANCE, "EPSG:32221")

    result = convert_bin_grid(ACCEPTANCE, "EPSG:32221", wgs84=False)

    assert result.wgs84_corners is None
    assert result.wgs84_outline is None
    assert result.wgs84_conversion is None
    assert (result.max_mislocation.di, result.max_mislocation.dj) == pytest.approx(
        (0.0, 0.0), abs=1e-9
    )


def test_an_operation_without_a_target_is_a_mistake() -> None:
    with pytest.raises(ValueError, match="target_crs"):
        convert_bin_grid(ACCEPTANCE, "EPSG:32615", operation="EPSG:15851")


@pytest.mark.parametrize("crs", ["EPSG:2065"], ids=["krovak-south-west"])
def test_a_projected_crs_without_easting_and_northing_axes_is_refused(crs: str) -> None:
    with pytest.raises(UnsupportedCRSError, match="east"):
        convert_bin_grid(ACCEPTANCE, crs, wgs84=False)


def test_corners_may_be_given_in_any_order_or_already_labelled() -> None:
    labelled = BinGridCorners.from_corners(ACCEPTANCE)

    shuffled = convert_bin_grid(list(reversed(ACCEPTANCE)), "EPSG:32615", wgs84=False)
    given = convert_bin_grid(labelled, "EPSG:32615", wgs84=False)

    assert shuffled.squaring == given.squaring
    assert given.input_corners is labelled


def test_result_renders_as_json() -> None:
    result = convert_bin_grid(ACCEPTANCE, "EPSG:32615", target_crs=BLM_14N)

    rendered = json.loads(json.dumps(result.to_json_dict()))

    assert rendered["converted"] is True
    assert rendered["linear_unit"] == "US survey foot"
    assert rendered["parameters"]["method_code"] == 9666
    assert len(rendered["input_corners"]) == 4
    assert len(rendered["converted_corners"]) == 4
    assert len(rendered["squared_corners"]) == 4
    assert rendered["outline"]["labels"] == ["A", "C", "D", "B", "A"]
    assert rendered["conversion"]["operation"]["applied"] == "EPSG:15851"
    assert rendered["applied_operations"] == list(result.applied_operations())
