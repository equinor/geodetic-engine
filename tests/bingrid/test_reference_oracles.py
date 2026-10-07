"""The test-side references are checked before anything is checked against them.

:mod:`tests.bingrid._epsg_reference` must reproduce the worked examples of the
EPSG guidance note, and :mod:`tests.bingrid._java_reference` must reproduce the
SDU spreadsheet, which implements the same method in Excel. The Java transcription
then demonstrates defect D2 on a grid whose answer is known exactly.
"""

from __future__ import annotations

import math
from typing import Any

import pytest

from tests.bingrid import _epsg_reference as epsg
from tests.bingrid import _java_reference as java
from tests.bingrid.conftest import corner_tuples, load, sdu_cases

EPSG_EXAMPLES = load("epsg_p6_examples.json")["examples"]


@pytest.mark.parametrize("example", EPSG_EXAMPLES, ids=lambda e: e["case_id"])
def test_epsg_reference_reproduces_the_forward_example(example: dict[str, Any]) -> None:
    """Node to map grid, to the 2 decimals the guidance note publishes."""
    parameters = {**example["parameters"], "handedness": example["handedness"]}
    forward = example["forward"]

    easting, northing = epsg.to_map(parameters, *forward["node"])

    assert (easting, northing) == pytest.approx(forward["map"], abs=0.005)
    origin_e = parameters["origin_easting"]
    origin_n = parameters["origin_northing"]
    assert easting - origin_e == pytest.approx(sum(forward["easting_terms"]), abs=0.001)
    assert northing - origin_n == pytest.approx(
        sum(forward["northing_terms"]), abs=0.001
    )


@pytest.mark.parametrize("example", EPSG_EXAMPLES, ids=lambda e: e["case_id"])
def test_epsg_reference_reproduces_the_reverse_example(example: dict[str, Any]) -> None:
    """Map grid back to the node, as far as the rounded coordinates allow."""
    parameters = {**example["parameters"], "handedness": example["handedness"]}
    reverse = example["reverse"]

    i, j = epsg.to_bin(parameters, *reverse["map"])

    assert (i, j) == pytest.approx(reverse["node"], abs=0.001)
    # The guidance note's second term is the factor incI / (k dI).
    k = parameters["scale_factor"]
    assert reverse["i_terms"][1] == pytest.approx(
        parameters["increment_i"] / (k * parameters["bin_width_i"]), rel=1e-7
    )
    assert reverse["j_terms"][1] == pytest.approx(
        parameters["increment_j"] / (k * parameters["bin_width_j"]), rel=1e-7
    )


@pytest.mark.parametrize("case", sdu_cases(), ids=lambda c: c["case_id"])
def test_java_reference_reproduces_the_sdu_spreadsheet(case: dict[str, Any]) -> None:
    """With scale factor 1 the Java method is the SDU method, cell for cell."""
    derived = case["derived"]

    result = java.squaring(
        corner_tuples(case),
        increment_i=case["increment_i"],
        increment_j=case["increment_j"],
    )

    assert result["method_code"] == derived["method_code"]
    assert result["bin_width_i"] == pytest.approx(derived["bin_width_i"], rel=1e-12)
    assert result["bin_width_j"] == pytest.approx(derived["bin_width_j"], rel=1e-12)
    assert result["bearing_j"] == pytest.approx(derived["bearing_j"], abs=1e-9)
    assert result["di"] == pytest.approx(case["max_mislocation"]["di"], abs=1e-9)
    assert result["dj"] == pytest.approx(case["max_mislocation"]["dj"], abs=1e-9)
    for squared, row in zip(result["squared"], case["squareness"], strict=True):
        assert squared == pytest.approx(
            (row["easting_model"], row["northing_model"]), abs=1e-6
        )


def test_java_reference_misplaces_an_exact_grid_whose_scale_factor_is_not_one() -> None:
    """Defect D2, demonstrated: the Java method fails a perfect rectangle.

    The corners of a P6 grid with scale factor 0.9996 are an exact rectangle,
    so the correct squaring error is zero and the squared corners are the input
    corners. The Java method derives map-grid bin widths and then scales them
    by k again: it reports (span/2)(1 - 1/k) bins of error -- 0.02 and 0.2 on
    this 100 x 1000 grid -- and pulls every corner 20 m towards the centre.
    """
    parameters = {
        "origin_i": 1, "origin_j": 1000, "origin_easting": 500000.0,
        "origin_northing": 3000000.0, "bin_width_i": 1000.0 / 0.9996,
        "bin_width_j": 100.0 / 0.9996, "bearing_j": 0.0, "handedness": "right",
        "scale_factor": 0.9996,
    }  # fmt: skip
    corners = epsg.corners(parameters, (1, 101), (1000, 2000))

    result = java.squaring(corners, scale_factor=0.9996)

    assert result["di"] == pytest.approx(50 * (1 / 0.9996 - 1), rel=1e-9)
    assert result["dj"] == pytest.approx(500 * (1 / 0.9996 - 1), rel=1e-9)
    assert round(result["di"], 2) == 0.02
    assert round(result["dj"], 2) == 0.2
    displacement = [
        math.dist(squared, corner[2:])
        for squared, corner in zip(result["squared"], corners, strict=True)
    ]
    assert displacement == pytest.approx([20.0 * math.sqrt(2)] * 4, rel=1e-6)
