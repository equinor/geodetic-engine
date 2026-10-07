"""Correct behaviour where the Java bin grid implementation is wrong: core defects.

Each test states what the computation must do and names the defect it guards
against; ``tests/bingrid/data/java_defects.json`` records where each defect is in
the Java source.
"""

from __future__ import annotations

import dataclasses
import math
from types import ModuleType
from typing import Any

import numpy as np
import pytest

from geodetic_engine.bingrid import (
    BinGridCorners,
    DegenerateBinGridError,
    InvalidCornersError,
    UnsupportedCRSError,
    convert_bin_grid,
    corners_from_p6,
    derive_p6,
    outline,
    square_up,
)
from tests.bingrid import _epsg_reference as epsg
from tests.bingrid import _java_reference as java
from tests.bingrid.conftest import (
    corner_tuples,
    exact_corners,
    load,
    p6,
    signed_area,
    synthetic_grids,
)

DEFECTS = load("java_defects.json")["defects"]
GRIDS = synthetic_grids()
ACCEPTANCE = [(1, 1000, 500000.0, 3000000.0), (1, 2000, 500000.0, 3100000.0),
              (101, 1000, 600000.0, 3000000.0), (101, 2000, 600000.0, 3100000.0)]  # fmt: skip
SCALED = [g for g in GRIDS if g["parameters"]["scale_factor"] != 1.0]


@pytest.mark.java_defect("D2")
@pytest.mark.parametrize("grid", SCALED, ids=lambda g: g["case_id"])
def test_d2_an_exact_grid_has_no_misfit_whatever_its_scale_factor(
    grid: dict[str, Any],
) -> None:
    """D2: bin width = increment x grid distance / (node span x k)."""
    original = p6(grid["parameters"])
    corners = exact_corners(grid)

    result = square_up(
        BinGridCorners.from_corners(corners), scale_factor=original.scale_factor
    )

    assert result.max_mislocation.di == pytest.approx(0.0, abs=1e-8)
    assert result.max_mislocation.dj == pytest.approx(0.0, abs=1e-8)
    assert result.parameters.bin_width_i == pytest.approx(
        original.bin_width_i, rel=1e-11
    )
    assert result.parameters.bin_width_j == pytest.approx(
        original.bin_width_j, rel=1e-11
    )
    np.testing.assert_allclose(
        result.squared_corners.coordinates, [c[2:] for c in corners], rtol=0, atol=1e-6
    )
    # The Java method misplaces this very grid.
    wrong = java.squaring(corners, scale_factor=original.scale_factor)
    assert max(wrong["di"], wrong["dj"]) > 0.01


@pytest.mark.java_defect("D2")
def test_d2_the_epsg_example_grid_is_recovered_from_its_corners() -> None:
    """The EPSG 9666 example: 25 x 12.5 m ground bins at map scale 0.99984."""
    example = load("epsg_p6_examples.json")["examples"][0]
    parameters = {**example["parameters"], "handedness": example["handedness"]}
    corners = epsg.corners(parameters, (1, 300), (1, 247))

    fitted = derive_p6(BinGridCorners.from_corners(corners), scale_factor=0.99984)

    assert fitted.bin_width_i == pytest.approx(25.0, rel=1e-12)
    assert fitted.bin_width_j == pytest.approx(12.5, rel=1e-12)
    assert fitted.bearing_j == pytest.approx(20.0, abs=1e-10)
    node = fitted.to_bin(*epsg.to_map(parameters, 300, 247))
    assert tuple(node[0]) == pytest.approx((300, 247), abs=1e-9)


@pytest.mark.java_defect("D3")
@pytest.mark.parametrize(
    "numbers",
    [
        [(1, 1000), (1, 2000), (1, 1000), (1, 2000)],
        [(1, 1000), (1, 1000), (101, 1000), (101, 1000)],
        [(1, 1000), (1, 2000), (101, 1000), (101, 1000)],
        [(1, 1000), (1, 2000), (50, 1000), (101, 2000)],
    ],
    ids=["no-inline-span", "no-crossline-span", "duplicate-corner", "three-inlines"],
)
def test_d3_corners_must_be_the_corners_of_an_inline_crossline_rectangle(
    numbers: list[tuple[int, int]],
) -> None:
    """D3: the layout is validated, not divided by zero (tutorial 7.3, check 4)."""
    given = [
        (i, j, e, n) for (i, j), (_, _, e, n) in zip(numbers, ACCEPTANCE, strict=True)
    ]

    with pytest.raises(InvalidCornersError, match=r"\(1, 1000\)"):
        convert_bin_grid(given, "EPSG:32615", wgs84=False)


@pytest.mark.java_defect("D4")
@pytest.mark.parametrize(
    "coordinates",
    [
        [(500000.0, 3000000.0)] * 4,
        [
            (500000.0, 3000000.0),
            (500000.0, 3000000.0),
            (600000.0, 3000000.0),
            (600000.0, 3100000.0),
        ],
        [
            (500000.0, 3000000.0),
            (600000.0, 3100000.0),
            (600000.0, 3000000.0),
            (500000.0, 3100000.0),
        ],
        [
            (500000.0, 3000000.0),
            (500000.0, 3100000.0),
            (500000.0, 3200000.0),
            (500000.0, 3300000.0),
        ],
    ],
    ids=["all-coincident", "a-equals-b", "b-and-d-swapped", "collinear"],
)
def test_d4_corner_coordinates_must_be_able_to_be_those_corners(
    coordinates: list[tuple[float, float]],
) -> None:
    """D4: coincident, collinear or twisted corners are refused, not averaged."""
    given = [
        (i, j, e, n)
        for (i, j, _, _), (e, n) in zip(ACCEPTANCE, coordinates, strict=True)
    ]

    with pytest.raises(DegenerateBinGridError):
        square_up(BinGridCorners.from_corners(given))


@pytest.mark.java_defect("D4")
def test_d4_non_finite_coordinates_are_refused() -> None:
    given = [*ACCEPTANCE[:3], (101, 2000, math.nan, 3100000.0)]

    with pytest.raises(DegenerateBinGridError, match="finite"):
        convert_bin_grid(given, "EPSG:32615", wgs84=False)


@pytest.mark.java_defect("D5")
@pytest.mark.parametrize("crs", ["EPSG:4326", "EPSG:4267", "EPSG:4978"])
def test_d5_a_grid_in_a_non_projected_crs_is_refused(crs: str) -> None:
    """D5: degrees are not map grid units (tutorial 7.3: Projected or BoundProjected)."""
    corners = [
        (1, 1000, -93.0, 27.0),
        (1, 2000, -93.0, 28.0),
        (101, 1000, -92.0, 27.0),
        (101, 2000, -92.0, 28.0),
    ]

    with pytest.raises(UnsupportedCRSError, match="projected"):
        convert_bin_grid(corners, crs, wgs84=False)


@pytest.mark.java_defect("D5")
def test_d5_a_non_projected_target_crs_is_refused() -> None:
    with pytest.raises(UnsupportedCRSError, match="projected"):
        convert_bin_grid(ACCEPTANCE, "EPSG:32615", target_crs="EPSG:4326")


@pytest.mark.java_defect("D13")
def test_d13_the_documented_spatial_area_ring_is_clockwise_and_is_reordered() -> None:
    """D13: tutorial 7.5 writes A,B,D,C,A for a right-handed grid; that is clockwise."""
    case = load("outline_cases.json")["cases"][0]
    corners = BinGridCorners.from_corners(corner_tuples(case))
    by_label = dict(corners.labelled())
    documented = [
        (by_label[label].easting, by_label[label].northing)
        for label in case["documented_labels"]
    ]

    ring = outline(corners)

    assert signed_area(documented) < 0
    assert ring.labels == ("A", "C", "D", "B", "A")


@pytest.mark.java_defect("D13")
def test_d13_conversion_returns_the_outline_in_the_map_crs_and_in_wgs84() -> None:
    result = convert_bin_grid(ACCEPTANCE, "EPSG:32615")

    assert result.outline.labels == ("A", "C", "D", "B", "A")
    assert result.wgs84_outline is not None
    assert result.wgs84_outline.labels == ("A", "C", "D", "B", "A")
    assert result.wgs84_outline.coordinates[1] == result.wgs84_corners[2]  # type: ignore[index]


@pytest.mark.java_defect("D14")
def test_d14_the_result_keeps_input_converted_and_squared_corners() -> None:
    """D14: nothing is overwritten; each stage of the corners stays available."""
    from pyproj import CRS
    from pyproj.crs import BoundCRS, CoordinateOperation

    target = BoundCRS(
        CRS.from_epsg(32064),
        CRS.from_epsg(4326),
        CoordinateOperation.from_authority("EPSG", 15851),
    )
    labelled = BinGridCorners.from_corners(ACCEPTANCE)

    result = convert_bin_grid(labelled, "EPSG:32615", target_crs=target)

    assert result.input_corners == BinGridCorners.from_corners(ACCEPTANCE)
    assert result.converted_corners is not None
    assert result.converted_corners != result.squared_corners
    assert result.squaring.input_corners == result.converted_corners
    with pytest.raises(dataclasses.FrozenInstanceError):
        result.crs = result.source_crs  # type: ignore[misc]


@pytest.mark.java_defect("D15")
@pytest.mark.parametrize("target", ["EPSG:32615", "epsg:32615"])
def test_d15_no_conversion_is_applied_to_the_crs_the_grid_is_in(target: str) -> None:
    """D15: tutorial 7.2 -- if target CRS is the original CRS, conversion is omitted."""
    result = convert_bin_grid(ACCEPTANCE, "EPSG:32615", target_crs=target)

    assert not result.converted
    assert result.conversion is None
    assert result.converted_corners is None
    assert result.crs is result.source_crs
    assert not any("Converted" in step for step in result.applied_operations())


@pytest.mark.java_defect("D16")
def test_d16_nothing_is_rounded_in_the_core() -> None:
    """D16: full precision throughout; rounding is for presentation only."""
    grid = next(g for g in GRIDS if g["case_id"] == "bearing_89_9_right")
    corners = corners_from_p6(
        p6(grid["parameters"]),
        inline_range=tuple(grid["inline_range"]),
        crossline_range=tuple(grid["crossline_range"]),
    )

    result = square_up(corners)

    a = result.squared_corners.a
    assert (result.parameters.origin_easting, result.parameters.origin_northing) == (
        a.easting,
        a.northing,
    )
    exact = np.array([c[2:] for c in exact_corners(grid)])
    assert np.abs(exact - np.round(exact, 3)).max() > 1e-4
    np.testing.assert_allclose(
        result.squared_corners.coordinates, exact, rtol=0, atol=1e-6
    )


@pytest.mark.java_defect("D18")
def test_d18_the_ground_bin_widths_do_not_depend_on_the_crs() -> None:
    """D18: converted to BLM 14N, the bins keep the ground size they had in UTM 15N."""
    in_utm = convert_bin_grid(ACCEPTANCE, "EPSG:32615", wgs84=False)
    in_blm = convert_bin_grid(
        ACCEPTANCE,
        "EPSG:32615",
        target_crs="EPSG:32064",
        operation="EPSG:15851",
        wgs84=False,
    )

    utm, blm = in_utm.parameters, in_blm.parameters
    assert blm.scale_factor / utm.scale_factor > 1.005
    ftus = in_blm.crs.axes[0].unit_conversion_factor
    assert (blm.bin_width_i * ftus, blm.bin_width_j * ftus) == pytest.approx(
        (utm.bin_width_i, utm.bin_width_j), rel=2e-5
    )


def _defects_tested_in(module: ModuleType) -> set[str]:
    found: set[str] = set()
    for name, function in vars(module).items():
        if name.startswith("test_"):
            for mark in getattr(function, "pytestmark", []):
                if mark.name == "java_defect":
                    found.update(mark.args)
    return found


def test_every_registered_defect_has_a_test() -> None:
    """The registry and the tests cannot drift apart."""
    import sys

    tested = _defects_tested_in(sys.modules[__name__])

    assert {d["id"] for d in DEFECTS} == tested
