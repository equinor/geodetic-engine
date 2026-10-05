"""Deriving P6 parameters from four corners, and the squaring QC."""

from __future__ import annotations

import math
from typing import Any

import numpy as np
import pytest

from geodetic_engine.bingrid import (
    BinGridCorners,
    Handedness,
    InvalidParameterError,
    MaxMislocation,
    corners_from_p6,
    derive_p6,
    square_up,
)
from tests.bingrid import _java_reference as java
from tests.bingrid.conftest import (
    corner_tuples,
    exact_corners,
    load,
    p6,
    sdu_cases,
    synthetic_grids,
)

GRIDS = synthetic_grids()
SDU = sdu_cases()
ATAN2 = load("sdu_spreadsheet_cases.json")["atan2"]


def _bearing_difference(a: float, b: float) -> float:
    return abs((a - b + 180.0) % 360.0 - 180.0)


def _perturbed(
    corners: list[tuple[int, int, float, float]], shifts: list[tuple[float, float]]
) -> list[tuple[int, int, float, float]]:
    return [
        (i, j, e + de, n + dn)
        for (i, j, e, n), (de, dn) in zip(corners, shifts, strict=True)
    ]


@pytest.mark.parametrize("grid", GRIDS, ids=lambda g: g["case_id"])
def test_an_exact_grid_gives_its_own_parameters_back(grid: dict[str, Any]) -> None:
    """Every quadrant, both handednesses, increments and scale factors."""
    original = p6(grid["parameters"])
    corners = BinGridCorners.from_corners(exact_corners(grid))

    result = square_up(
        corners,
        scale_factor=original.scale_factor,
        increment_i=original.increment_i,
        increment_j=original.increment_j,
    )

    fitted = result.parameters
    (i_min, _), (j_min, _) = grid["inline_range"], grid["crossline_range"]
    expected = original.reanchored(i_min, j_min)
    assert fitted.handedness is original.handedness
    assert _bearing_difference(fitted.bearing_j, original.bearing_j) < 1e-9
    assert fitted.bin_width_i == pytest.approx(original.bin_width_i, rel=1e-11)
    assert fitted.bin_width_j == pytest.approx(original.bin_width_j, rel=1e-11)
    assert (fitted.origin_i, fitted.origin_j) == (i_min, j_min)
    assert (fitted.origin_easting, fitted.origin_northing) == pytest.approx(
        (expected.origin_easting, expected.origin_northing), abs=1e-6
    )
    assert (fitted.scale_factor, fitted.increment_i, fitted.increment_j) == (
        original.scale_factor, original.increment_i, original.increment_j
    )  # fmt: skip
    assert result.max_mislocation.di == pytest.approx(0.0, abs=1e-8)
    assert result.max_mislocation.dj == pytest.approx(0.0, abs=1e-8)
    assert result.max_mislocation.distance == pytest.approx(0.0, abs=1e-6)
    np.testing.assert_allclose(
        result.squared_corners.coordinates, corners.coordinates, rtol=0, atol=1e-6
    )


@pytest.mark.parametrize("case", SDU, ids=lambda c: c["case_id"])
def test_derived_parameters_match_the_sdu_spreadsheet(case: dict[str, Any]) -> None:
    """The spreadsheet anchors at the centre of the corners, as derive_p6 does."""
    derived = case["derived"]

    parameters = derive_p6(
        BinGridCorners.from_corners(corner_tuples(case)),
        increment_i=case["increment_i"],
        increment_j=case["increment_j"],
    )

    assert parameters.method_code == derived["method_code"]
    assert parameters.origin_i == pytest.approx(derived["origin_i"])
    assert parameters.origin_j == pytest.approx(derived["origin_j"])
    assert parameters.origin_easting == pytest.approx(
        derived["origin_easting"], abs=1e-6
    )
    assert parameters.origin_northing == pytest.approx(
        derived["origin_northing"], abs=1e-6
    )
    assert parameters.bin_width_i == pytest.approx(derived["bin_width_i"], rel=1e-12)
    assert parameters.bin_width_j == pytest.approx(derived["bin_width_j"], rel=1e-12)
    assert parameters.bearing_j == pytest.approx(derived["bearing_j"], abs=1e-9)
    assert parameters.scale_factor == derived["scale_factor"]


@pytest.mark.parametrize("case", SDU, ids=lambda c: c["case_id"])
def test_squaring_matches_the_sdu_spreadsheet(case: dict[str, Any]) -> None:
    """Residuals per corner, squared coordinates and the maximum mis-location."""
    result = square_up(
        BinGridCorners.from_corners(corner_tuples(case)),
        increment_i=case["increment_i"],
        increment_j=case["increment_j"],
    )

    for residual, squared, row in zip(
        result.residuals, result.squared_corners, case["squareness"], strict=True
    ):
        assert residual.label == row["label"]
        assert residual.inline == pytest.approx(row["residual_inline"], abs=1e-9)
        assert residual.crossline == pytest.approx(row["residual_crossline"], abs=1e-9)
        assert residual.easting == pytest.approx(row["residual_easting"], abs=1e-6)
        assert residual.northing == pytest.approx(row["residual_northing"], abs=1e-6)
        assert (squared.easting, squared.northing) == pytest.approx(
            (row["easting_model"], row["northing_model"]), abs=1e-6
        )
    assert result.max_mislocation.di == pytest.approx(
        case["max_mislocation"]["di"], abs=1e-9
    )
    assert result.max_mislocation.dj == pytest.approx(
        case["max_mislocation"]["dj"], abs=1e-9
    )


@pytest.mark.parametrize("case", SDU, ids=lambda c: c["case_id"])
def test_squaring_agrees_with_the_java_service_at_scale_factor_one(
    case: dict[str, Any],
) -> None:
    """Where the Java method is right, the answer is the same, origin included."""
    reference = java.squaring(
        corner_tuples(case),
        increment_i=case["increment_i"],
        increment_j=case["increment_j"],
    )

    result = square_up(
        BinGridCorners.from_corners(corner_tuples(case)),
        increment_i=case["increment_i"],
        increment_j=case["increment_j"],
    )

    fitted = result.parameters
    assert fitted.method_code == reference["method_code"]
    assert fitted.bearing_j == pytest.approx(reference["bearing_j"], abs=1e-9)
    assert fitted.bin_width_i == pytest.approx(reference["bin_width_i"], rel=1e-12)
    assert fitted.bin_width_j == pytest.approx(reference["bin_width_j"], rel=1e-12)
    assert (
        fitted.origin_i, fitted.origin_j, fitted.origin_easting, fitted.origin_northing
    ) == pytest.approx(reference["origin"], abs=1e-6)  # fmt: skip
    np.testing.assert_allclose(
        result.squared_corners.coordinates, reference["squared"], rtol=0, atol=1e-6
    )
    assert result.max_mislocation.di == pytest.approx(reference["di"], abs=1e-9)
    assert result.max_mislocation.dj == pytest.approx(reference["dj"], abs=1e-9)


@pytest.mark.parametrize("grid", GRIDS[:6], ids=lambda g: g["case_id"])
def test_squaring_agrees_with_the_java_service_on_distorted_grids(
    grid: dict[str, Any],
) -> None:
    corners = exact_corners(grid)
    span = min(
        math.dist(corners[0][2:], corners[1][2:]),
        math.dist(corners[0][2:], corners[2][2:]),
    )
    shifts = [
        (0.004 * span, -0.002 * span),
        (0.0, 0.003 * span),
        (-0.001 * span, 0.0),
        (0.002 * span, 0.001 * span),
    ]
    distorted = _perturbed(corners, shifts)
    reference = java.squaring(distorted)

    result = square_up(BinGridCorners.from_corners(distorted))

    assert result.parameters.bearing_j == pytest.approx(
        reference["bearing_j"], abs=1e-9
    )
    assert result.parameters.bin_width_i == pytest.approx(
        reference["bin_width_i"], rel=1e-12
    )
    assert result.max_mislocation.di == pytest.approx(reference["di"], abs=1e-9)
    assert result.max_mislocation.dj == pytest.approx(reference["dj"], abs=1e-9)
    assert result.max_mislocation.di > 0.0 or result.max_mislocation.dj > 0.0


def test_one_displaced_corner_spreads_its_error_over_all_four() -> None:
    """25 m bins at 30 degrees, corner A moved 3 m east: about 1/20 of a bin."""
    parameters = {
        "origin_i": 1000, "origin_j": 2000, "origin_easting": 450000.0,
        "origin_northing": 6500000.0, "bin_width_i": 25.0, "bin_width_j": 25.0,
        "bearing_j": 30.0, "handedness": "right",
    }  # fmt: skip
    grid = {
        "parameters": parameters,
        "inline_range": [1000, 1800],
        "crossline_range": [2000, 3000],
    }
    corners = _perturbed(
        exact_corners(grid), [(3.0, 0.0), (0.0, 0.0), (0.0, 0.0), (0.0, 0.0)]
    )

    result = square_up(BinGridCorners.from_corners(corners))

    assert round(result.max_mislocation.di, 2) == 0.03
    assert round(result.max_mislocation.dj, 2) == 0.05
    # The fit absorbs more than half of the 3 m.
    assert result.max_mislocation.distance == pytest.approx(1.426, abs=0.001)
    assert result.parameters.bearing_j == pytest.approx(30.0, abs=0.01)


def test_the_acceptance_grid_is_square_with_1000_by_100_m_bins() -> None:
    case = next(c for c in SDU if c["case_id"] == "sdu_test1a")

    result = square_up(BinGridCorners.from_corners(corner_tuples(case)))

    p = result.parameters
    assert (p.method_code, p.bearing_j, p.bin_width_i, p.bin_width_j) == (
        9666,
        0.0,
        1000.0,
        100.0,
    )
    assert (p.origin_i, p.origin_j, p.origin_easting, p.origin_northing) == (
        1.0, 1000.0, 500000.0, 3000000.0
    )  # fmt: skip
    assert (result.max_mislocation.di, result.max_mislocation.dj) == (0.0, 0.0)


@pytest.mark.parametrize(
    "pair",
    ATAN2["mean_bearings"],
    ids=lambda p: f"{p['bearing_1']:g}+{p['bearing_2']:g}",
)
def test_edge_bearings_are_averaged_around_the_circle(pair: dict[str, float]) -> None:
    """The spreadsheet's Euler averaging cases, including across 0 and 180 degrees."""
    b1, b2, mean = (math.radians(pair[k]) for k in ("bearing_1", "bearing_2", "mean"))
    length, width = 10000.0, 4000.0
    a = (500000.0, 6000000.0)
    c = (a[0] + width * math.cos(mean), a[1] - width * math.sin(mean))
    b = (a[0] + length * math.sin(b1), a[1] + length * math.cos(b1))
    d = (c[0] + length * math.sin(b2), c[1] + length * math.cos(b2))
    corners = [(1, 1, *a), (1, 401, *b), (161, 1, *c), (161, 401, *d)]

    parameters = derive_p6(BinGridCorners.from_corners(corners))

    assert _bearing_difference(parameters.bearing_j, pair["mean"]) < 1e-9
    assert parameters.handedness is Handedness.RIGHT


def test_mislocation_is_also_given_in_bins() -> None:
    mislocation = MaxMislocation(
        di=0.5, dj=0.3, distance=2.0, increment_i=2, increment_j=3
    )

    assert mislocation.di_bins == pytest.approx(0.25)
    assert mislocation.dj_bins == pytest.approx(0.1)


def test_increments_scale_bin_widths_and_bins() -> None:
    """Inline numbers stepping by 2 per node: widths per node, residuals in numbers."""
    case = next(c for c in SDU if c["case_id"] == "sdu_bingridmath")
    corners = BinGridCorners.from_corners(corner_tuples(case))

    single = square_up(corners)
    stepped = square_up(corners, increment_i=5, increment_j=2)

    assert stepped.parameters.bin_width_i == pytest.approx(
        5 * single.parameters.bin_width_i
    )
    assert stepped.parameters.bin_width_j == pytest.approx(
        2 * single.parameters.bin_width_j
    )
    # Residuals are differences of numbers around 1e4: compare them absolutely.
    assert stepped.max_mislocation.di == pytest.approx(
        single.max_mislocation.di, abs=1e-9
    )
    assert stepped.max_mislocation.dj_bins == pytest.approx(
        single.max_mislocation.dj / 2, abs=1e-9
    )


@pytest.mark.parametrize(
    ("keyword", "value"),
    [
        ("scale_factor", 0.0),
        ("scale_factor", -0.5),
        ("increment_i", 0),
        ("increment_j", 2.5),
    ],
)
def test_parameters_of_the_fit_are_validated(keyword: str, value: Any) -> None:
    corners = BinGridCorners.from_corners(corner_tuples(SDU[1]))

    with pytest.raises(InvalidParameterError, match=keyword):
        square_up(corners, **{keyword: value})


def test_squaring_result_renders_as_plain_data() -> None:
    result = square_up(BinGridCorners.from_corners(corner_tuples(SDU[1])))

    rendered = result.to_json_dict()

    assert rendered["parameters"]["method_code"] == 9666
    assert [r["label"] for r in rendered["residuals"]] == ["A", "B", "C", "D"]
    assert rendered["max_mislocation"]["di"] == 0.0
    assert rendered["squared_corners"][0]["label"] == "A"
    assert rendered["input_corners"][3]["inline"] == 101


def test_p6_corners_square_up_to_the_same_grid() -> None:
    """P6 definition to four corners and back is the identity."""
    grid = next(g for g in GRIDS if g["case_id"] == "sdu_note_increments_1_4")
    original = p6(grid["parameters"])
    corners = corners_from_p6(
        original,
        inline_range=tuple(grid["inline_range"]),
        crossline_range=tuple(grid["crossline_range"]),
    )

    fitted = square_up(corners, increment_j=4).parameters

    assert fitted.bin_width_j == pytest.approx(25.0, rel=1e-12)
    assert fitted.bin_width_i == pytest.approx(30.0, rel=1e-12)
    assert fitted.bearing_j == pytest.approx(2.48019694, abs=1e-10)
    assert (fitted.origin_easting, fitted.origin_northing) == pytest.approx(
        (423081.91, 3227689.59), abs=1e-6
    )
