"""Finding the stored bin grid a legacy dataset is on (SDU note: "How to detect if
a dataset is on an already defined bin grid").

The store holds the SDU note's Table 2 grid: NAD27 / UTM zone 15N, 30 m inline
and 25 m crossline spacing at node increments 1 and 4. Datasets are sub-volumes
of it, as loaded traces of a stored grid are.
"""

from __future__ import annotations

import dataclasses
import json

import numpy as np
import pytest
from pyproj import Transformer

from geodetic_engine import bingrid
from geodetic_engine.bingrid import (
    BinGridCorners,
    P6Parameters,
    UnsupportedCRSError,
    corners_from_p6,
)
from geodetic_engine.bingrid.matching import (
    BinGridMatch,
    MatchResult,
    StoredBinGrid,
    match_bin_grid,
)
from tests.bingrid.conftest import load, p6

TABLE_2 = load("sdu_note_examples.json")["p6_example"]
GRID = p6(TABLE_2["parameters"], "right")  # method 9666
NAD27_UTM_15N = "EPSG:26715"
NAD27_BLM_15N_FTUS = "EPSG:32065"
STORED = StoredBinGrid("table-2", GRID, NAD27_UTM_15N)


def _volume(
    parameters: P6Parameters = GRID,
    *,
    inlines: tuple[int, int] = (14200, 15000),
    crosslines: tuple[int, int] = (5201, 7201),
) -> BinGridCorners:
    return corners_from_p6(parameters, inline_range=inlines, crossline_range=crosslines)


def _shifted(corners: BinGridCorners, east: float) -> BinGridCorners:
    return corners.with_coordinates(corners.coordinates + np.array([east, 0.0]))


def _moved(grid: P6Parameters, east: float) -> P6Parameters:
    return dataclasses.replace(grid, origin_easting=grid.origin_easting + east)


def test_a_dataset_on_a_stored_grid_matches_it() -> None:
    elsewhere = StoredBinGrid("elsewhere", _moved(GRID, 5000.0), NAD27_UTM_15N)

    result = match_bin_grid(_volume(), NAD27_UTM_15N, [elsewhere, STORED])

    assert isinstance(result, MatchResult)
    assert result.best is not None
    assert result.best.grid is STORED
    assert result.best.distance < 1e-6
    assert result.best.same_crs
    assert [match.grid.key for match in result.matches] == ["table-2"]


def test_the_tolerance_is_half_the_smaller_real_spacing_of_the_dataset() -> None:
    """Real spacings 30 x 25 m (every node at increments 1 x 4): 12.5 m."""
    result = match_bin_grid(_volume(), NAD27_UTM_15N, [STORED], increment_j=4)

    assert result.tolerance == pytest.approx(12.5)
    assert result.linear_unit == "metre"


@pytest.mark.parametrize(("east", "matched"), [(10.0, True), (15.0, False)])
def test_a_dataset_must_be_within_half_a_bin(east: float, matched: bool) -> None:
    dataset = _shifted(_volume(), east)

    result = match_bin_grid(dataset, NAD27_UTM_15N, [STORED], increment_j=4)

    assert (result.best is not None) is matched
    if result.best is not None:
        assert result.best.distance == pytest.approx(east)


def test_a_decimated_dataset_is_held_to_half_of_its_own_spacing() -> None:
    """Loaded at increments 2 x 8, traces are 60 x 50 m apart: 25 m is allowed."""
    dataset = _shifted(_volume(crosslines=(5201, 7201)), 20.0)

    result = match_bin_grid(
        dataset, NAD27_UTM_15N, [STORED], increment_i=2, increment_j=8
    )

    assert result.tolerance == pytest.approx(25.0)
    assert result.best is not None
    assert not result.best.same_increments


def test_coordinates_in_ftus_match_a_grid_defined_in_metres() -> None:
    """The SDU note's exception: NAD27 / BLM 15N (ftUS) data on a UTM 15N grid."""
    metres = _volume()
    to_feet = Transformer.from_crs(NAD27_UTM_15N, NAD27_BLM_15N_FTUS, always_xy=True)
    feet = metres.with_coordinates(
        [to_feet.transform(e, n) for e, n in metres.coordinates.tolist()]
    )
    # The note's shortcut, multiplying by 3937/1200, is right to 0.0033 ftUS:
    # EPSG states the BLM false easting rounded, 1640416.67 rather than
    # 500000 m. Matching converts exactly instead.
    np.testing.assert_allclose(
        feet.coordinates, metres.coordinates * 3937 / 1200, rtol=0, atol=0.004
    )

    result = match_bin_grid(feet, NAD27_BLM_15N_FTUS, [STORED], increment_j=4)

    assert result.linear_unit == "US survey foot"
    assert result.tolerance == pytest.approx(12.5 * 3937 / 1200)
    assert result.best is not None
    assert result.best.grid is STORED
    assert not result.best.same_crs
    assert result.best.distance < 1e-6


def test_a_grid_on_another_datum_is_never_compared() -> None:
    """The same numbers in WGS 84 / UTM 15N would match exactly if compared as
    numbers, but lie tens of metres away on the ground. Comparing across a
    datum change needs a transformation, which a match must not choose.
    """
    wgs84 = StoredBinGrid("wgs84-utm-15n", GRID, "EPSG:32615")

    result = match_bin_grid(_volume(), NAD27_UTM_15N, [wgs84])

    assert result.best is None
    assert result.matches == ()


def test_the_grid_in_the_datasets_own_crs_is_preferred() -> None:
    """Step 1 of the tie-break, ahead of a closer grid in another CRS."""
    feet = StoredBinGrid(
        "ftus-copy",
        dataclasses.replace(
            GRID,
            origin_easting=GRID.origin_easting * 3937 / 1200,
            origin_northing=GRID.origin_northing * 3937 / 1200,
            bin_width_i=GRID.bin_width_i * 3937 / 1200,
            bin_width_j=GRID.bin_width_j * 3937 / 1200,
        ),
        NAD27_BLM_15N_FTUS,
    )
    near = StoredBinGrid("one-metre-off", _moved(GRID, 1.0), NAD27_UTM_15N)

    result = match_bin_grid(_volume(), NAD27_UTM_15N, [feet, near], increment_j=4)

    assert [match.grid.key for match in result.matches] == [
        "one-metre-off",
        "ftus-copy",
    ]
    assert result.matches[1].distance < result.matches[0].distance


def test_then_the_grid_with_the_datasets_increments_is_preferred() -> None:
    """Step 2: the same grid stored again at the dataset's increments."""
    coarse = StoredBinGrid(
        "increments-2x8",
        dataclasses.replace(
            _moved(GRID, 1.0),
            bin_width_i=GRID.bin_width_i * 2,
            bin_width_j=GRID.bin_width_j * 2,
            increment_i=2,
            increment_j=8,
        ),
        NAD27_UTM_15N,
    )

    result = match_bin_grid(
        _volume(), NAD27_UTM_15N, [STORED, coarse], increment_i=2, increment_j=8
    )

    assert [match.grid.key for match in result.matches] == [
        "increments-2x8",
        "table-2",
    ]
    assert result.matches[0].same_increments


def test_then_the_closest_grid_is_preferred() -> None:
    """Step 3: smallest distance criterion."""
    two = StoredBinGrid("two-metres-off", _moved(GRID, 2.0), NAD27_UTM_15N)
    one = StoredBinGrid("one-metre-off", _moved(GRID, -1.0), NAD27_UTM_15N)

    result = match_bin_grid(_volume(), NAD27_UTM_15N, [two, STORED, one])

    assert [match.grid.key for match in result.matches] == [
        "table-2",
        "one-metre-off",
        "two-metres-off",
    ]
    assert [round(match.distance, 6) for match in result.matches] == [0.0, 1.0, 2.0]


def test_an_empty_store_has_no_match() -> None:
    result = match_bin_grid(_volume(), NAD27_UTM_15N, [])

    assert result.best is None
    assert result.matches == ()


def test_a_dataset_must_be_in_a_projected_crs() -> None:
    with pytest.raises(UnsupportedCRSError, match="projected"):
        match_bin_grid(_volume(), "EPSG:4267", [STORED])


def test_a_stored_grid_states_its_crs_resolved() -> None:
    assert STORED.crs.authority_code == "EPSG:26715"


def test_the_result_renders_as_json() -> None:
    result = match_bin_grid(_volume(), NAD27_UTM_15N, [STORED], increment_j=4)

    rendered = json.loads(json.dumps(result.to_json_dict()))

    assert rendered["tolerance"] == pytest.approx(12.5)
    assert rendered["linear_unit"] == "metre"
    (match,) = rendered["matches"]
    assert match["key"] == "table-2"
    assert match["crs"] == "EPSG:26715"
    assert match["same_crs"] is True
    assert match["parameters"]["method_code"] == 9666
    assert isinstance(result.matches[0], BinGridMatch)


def test_matching_is_not_part_of_the_core_api() -> None:
    """An optional module: imported from geodetic_engine.bingrid.matching only."""
    assert not {"match_bin_grid", "StoredBinGrid", "MatchResult"} & set(bingrid.__all__)
