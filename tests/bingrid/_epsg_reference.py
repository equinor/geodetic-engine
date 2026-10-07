"""Scalar transcription of the EPSG P6 bin grid formulas, independent of the package.

Written straight from IOGP Guidance Note 7-2, methods 9666 (``P6 I=J+90``) and
1049 (``P6 I=J-90``), one point at a time and without numpy, so that a test
comparing the package with it cannot pass by sharing a mistake with it. It is
itself checked against the guidance note's worked examples in
``test_reference_oracles.py``.

Parameters are the plain dicts of ``tests/bingrid/data``: ``origin_i``,
``origin_j``, ``origin_easting``, ``origin_northing``, ``bin_width_i``,
``bin_width_j``, ``bearing_j`` (degrees), ``handedness`` (``"right"`` or
``"left"``) or ``method_code`` (9666 or 1049), and optionally ``scale_factor``,
``increment_i`` and ``increment_j``.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from typing import Any


def _sign(parameters: Mapping[str, Any]) -> int:
    if "handedness" in parameters:
        return {"right": 1, "left": -1}[parameters["handedness"]]
    return {9666: 1, 1049: -1}[parameters["method_code"]]


def _steps(parameters: Mapping[str, Any]) -> tuple[float, float]:
    """Map grid distance per unit of inline and of crossline number."""
    k = parameters.get("scale_factor", 1.0)
    return (
        k * parameters["bin_width_i"] / parameters.get("increment_i", 1),
        k * parameters["bin_width_j"] / parameters.get("increment_j", 1),
    )


def to_map(parameters: Mapping[str, Any], i: float, j: float) -> tuple[float, float]:
    """Easting and northing of bin grid position ``(i, j)``."""
    theta = math.radians(parameters["bearing_j"])
    step_i, step_j = _steps(parameters)
    sign = _sign(parameters)
    di = i - parameters["origin_i"]
    dj = j - parameters["origin_j"]
    easting = (
        parameters["origin_easting"]
        + sign * di * math.cos(theta) * step_i
        + dj * math.sin(theta) * step_j
    )
    northing = (
        parameters["origin_northing"]
        - sign * di * math.sin(theta) * step_i
        + dj * math.cos(theta) * step_j
    )
    return easting, northing


def to_bin(
    parameters: Mapping[str, Any], easting: float, northing: float
) -> tuple[float, float]:
    """Inline and crossline number of map grid point ``(easting, northing)``."""
    theta = math.radians(parameters["bearing_j"])
    step_i, step_j = _steps(parameters)
    sign = _sign(parameters)
    de = easting - parameters["origin_easting"]
    dn = northing - parameters["origin_northing"]
    i = (
        parameters["origin_i"]
        + sign * (de * math.cos(theta) - dn * math.sin(theta)) / step_i
    )
    j = parameters["origin_j"] + (de * math.sin(theta) + dn * math.cos(theta)) / step_j
    return i, j


def corners(
    parameters: Mapping[str, Any],
    inline_range: tuple[int, int],
    crossline_range: tuple[int, int],
) -> list[tuple[int, int, float, float]]:
    """Corners A, B, C, D as ``(inline, crossline, easting, northing)``."""
    (i_min, i_max), (j_min, j_max) = inline_range, crossline_range
    return [
        (i, j, *to_map(parameters, i, j))
        for i, j in ((i_min, j_min), (i_min, j_max), (i_max, j_min), (i_max, j_max))
    ]
