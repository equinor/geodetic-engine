"""Shared data for the bin grid tests.

Test data lives in ``tests/bingrid/data``; every file states its source. Corner
coordinates of the synthetic grids are not stored but generated from their P6
parameters by :mod:`tests.bingrid._epsg_reference`, an independent transcription
of the EPSG formulas, so that they are exact rectangles by construction.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from itertools import pairwise
from pathlib import Path
from typing import Any

from geodetic_engine.bingrid import Handedness, P6Parameters
from tests.bingrid import _epsg_reference

DATA = Path(__file__).parent / "data"


def load(name: str) -> Any:
    """Read one JSON file from ``tests/bingrid/data``."""
    return json.loads((DATA / name).read_text(encoding="utf-8"))


def osdu_outcome(case_id: str) -> dict[str, Any]:
    """Expected outcome of a case ported from the OSDU service's tests."""
    outcomes = load("osdu_expected_outcomes.json")
    case: dict[str, Any] = next(c for c in outcomes["cases"] if c["case_id"] == case_id)
    return {**case, "tolerances": outcomes["tolerances"]}


def synthetic_grids() -> list[dict[str, Any]]:
    """The hand-constructed P6 grids, with their extents."""
    return list(load("synthetic_grids.json")["grids"])


def sdu_cases() -> list[dict[str, Any]]:
    """The SDU spreadsheet's worked four-corner examples."""
    return list(load("sdu_spreadsheet_cases.json")["cases"])


def corner_tuples(case: dict[str, Any]) -> list[tuple[int, int, float, float]]:
    """``(inline, crossline, easting, northing)`` of a data file's corners."""
    return [
        (c["inline"], c["crossline"], c["easting"], c["northing"])
        for c in case["corners"]
    ]


def p6(parameters: dict[str, Any], handedness: str | None = None) -> P6Parameters:
    """The package's parameters for a data file's parameter dict."""
    fields = dict(parameters)
    fields["handedness"] = Handedness(handedness or fields["handedness"])
    return P6Parameters(**fields)


def exact_corners(grid: dict[str, Any]) -> list[tuple[int, int, float, float]]:
    """Corners A, B, C, D of a synthetic grid, from the EPSG reference."""
    return _epsg_reference.corners(
        grid["parameters"],
        tuple(grid["inline_range"]),  # type: ignore[arg-type]
        tuple(grid["crossline_range"]),  # type: ignore[arg-type]
    )


def signed_area(ring: Sequence[Sequence[float]]) -> float:
    """Shoelace area of a closed ring: positive when it is counterclockwise."""
    return 0.5 * sum(x0 * y1 - x1 * y0 for (x0, y0), (x1, y1) in pairwise(ring))
