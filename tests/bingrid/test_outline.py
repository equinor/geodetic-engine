"""The outline polygon of a bin grid: closed and counterclockwise."""

from __future__ import annotations

from typing import Any

import pytest

from geodetic_engine.bingrid import (
    BinGridCorners,
    DegenerateBinGridError,
    outline,
    outline_of,
)
from tests.bingrid.conftest import corner_tuples, load, signed_area

CASES = load("outline_cases.json")["cases"]


@pytest.mark.parametrize("case", CASES, ids=lambda c: c["case_id"])
def test_outline_is_a_closed_counterclockwise_ring_of_the_corners(
    case: dict[str, Any],
) -> None:
    corners = BinGridCorners.from_corners(corner_tuples(case))
    by_label = dict(corners.labelled())

    ring = outline(corners)

    assert ring.labels == tuple(case["expected_labels"])
    assert len(ring.coordinates) == 5
    assert ring.coordinates[0] == ring.coordinates[-1]
    assert signed_area(ring.coordinates) > 0
    for label, point in zip(ring.labels, ring.coordinates, strict=True):
        assert point == (by_label[label].easting, by_label[label].northing)


def test_outline_of_bare_points_follows_the_same_rule() -> None:
    case = CASES[0]
    points = [(c["easting"], c["northing"]) for c in case["corners"]]

    ring = outline_of(points)

    assert ring.labels == tuple(case["expected_labels"])
    assert signed_area(ring.coordinates) > 0


def test_corners_on_one_line_have_no_outline() -> None:
    corners = BinGridCorners.from_corners(
        [(1, 1, 0.0, 0.0), (1, 2, 0.0, 10.0), (2, 1, 0.0, 20.0), (2, 2, 0.0, 30.0)]
    )

    with pytest.raises(DegenerateBinGridError, match="area"):
        outline(corners)


def test_outline_renders_as_plain_data() -> None:
    rendered = outline(
        BinGridCorners.from_corners(corner_tuples(CASES[2]))
    ).to_json_dict()

    assert rendered["labels"] == ["A", "C", "D", "B", "A"]
    assert rendered["coordinates"][0] == [500000.0, 3000000.0]
    assert rendered["coordinates"][1] == [600000.0, 3000000.0]
