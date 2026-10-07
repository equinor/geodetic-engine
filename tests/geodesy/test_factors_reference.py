"""Projection factors against Blue Marble's Geographic Calculator.

The reference values in ``testdata/projection_scale_factor_convergence_tests.json``
come from Geographic Calculator. Each scale factor and grid convergence must
agree with them to 1e-4.

Geographic Calculator measures convergence over 1e-4 rad (20.6") of meridian
north of the point rather than at it, so its values can differ from the
package's by a few 1e-5 deg.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from pyproj import CRS

from geodetic_engine.geodesy import projection_factors

_CASES: list[dict[str, Any]] = json.loads(
    (
        Path(__file__).parent
        / "testdata"
        / "projection_scale_factor_convergence_tests.json"
    ).read_text(encoding="utf-8")
)


@pytest.mark.parametrize("quantity", ["scale_factor", "grid_convergence"])
@pytest.mark.parametrize("case", _CASES, ids=[case["test_name"] for case in _CASES])
def test_agrees_with_geographic_calculator(case: dict[str, Any], quantity: str) -> None:
    point = (case["input"]["lon"], case["input"]["lat"])
    factors = projection_factors(case["target_crs"], point, geographic=True)

    # The reference's input is longitude and latitude on the projection's own datum.
    assert factors.geographic_crs.crs.equals(CRS(case["source_crs"]))
    assert float(getattr(factors, quantity)[0]) == pytest.approx(
        case["expected"][quantity], abs=1e-4
    )
