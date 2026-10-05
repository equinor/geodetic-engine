"""Transcription of the Java bin grid squaring, defects included.

A line-by-line port of ``CRSConverter.squaring`` and the methods it calls in the
OSDU crs-conversion-service, used for two things only: to show, inside this
test suite, what the Java implementation computes where it is wrong (defect D2,
scale factor), and to check that the package agrees with it wherever it is
right (scale factor 1). It is never used as the expected value of a test of the
correct behaviour. Line numbers refer to
``local/crs-conversion-service/crs-converter-core/src/main/java/org/opengroup/osdu/crs/converter/CRSConverter.java``
at commit c28d6028a176fd636fe9f0cfa5efb866277190f2.

The Java rounding of its results (3 decimals for coordinates, 2 for dI and dJ)
is left out: it is a defect of its own (D6, D16), not part of the computation.

Ported from the OSDU crs-conversion-service, Apache License 2.0.
"""

from __future__ import annotations

import math
from collections.abc import Iterable
from typing import Any


def squaring(
    corners: Iterable[tuple[int, int, float, float]],
    *,
    scale_factor: float = 1.0,
    increment_i: int = 1,
    increment_j: int = 1,
) -> dict[str, Any]:
    """What ``squaring`` returns, for ``(inline, crossline, easting, northing)`` corners."""
    # sortAnyCRSFeature, L363-L385: by inline, then by crossline.
    (ia, ja, xa, ya), (ib, jb, xb, yb), (ic, jc, xc, yc), (id_, jd, xd, yd) = sorted(
        corners, key=lambda corner: (corner[0], corner[1])
    )
    # prepareSchemaParameters, L278-L333: the centre of the corners is the origin,
    # and 0 stands for an omitted scale factor or increment.
    k = 1.0 if scale_factor == 0.0 else scale_factor
    inc_i = 1 if increment_i == 0 else increment_i
    inc_j = 1 if increment_j == 0 else increment_j
    origin_i = (ia + ib + ic + id_) / 4
    origin_j = (ja + jb + jc + jd) / 4
    origin_e = (xa + xb + xc + xd) / 4
    origin_n = (ya + yb + yc + yd) / 4

    # RcomputationBetweenPoints, L499-L527.
    r_ac = math.sqrt((yc - ya) ** 2 + (xc - xa) ** 2)
    r_bd = math.sqrt((yd - yb) ** 2 + (xd - xb) ** 2)
    r_ab = math.sqrt((yb - ya) ** 2 + (xb - xa) ** 2)
    r_cd = math.sqrt((yd - yc) ** 2 + (xd - xc) ** 2)

    # rDeltaIandJComputation, L529-L556: map grid distances, never divided by k.
    delta_i = inc_i * ((r_ac + r_bd) / 2) / (ic - ia)
    delta_j = inc_j * ((r_ab + r_cd) / 2) / (jb - ja)

    # thetaCalculation, L558-L765.
    theta_ab = math.degrees(math.atan2(xb - xa, yb - ya))
    theta_cd = math.degrees(math.atan2(xd - xc, yd - yc))
    theta = math.degrees(
        math.atan2(
            math.sin(math.radians(theta_ab)) + math.sin(math.radians(theta_cd)),
            math.cos(math.radians(theta_ab)) + math.cos(math.radians(theta_cd)),
        )
    )
    if theta < 0:
        theta += 360
    cos_t = math.cos(math.radians(theta))
    sin_t = math.sin(math.radians(theta))
    right = (xc - xa) * cos_t > (yc - ya) * sin_t
    sign = 1 if right else -1

    squared = []
    residual_i = []
    residual_j = []
    for i, j, x, y in (
        (ia, ja, xa, ya),
        (ib, jb, xb, yb),
        (ic, jc, xc, yc),
        (id_, jd, xd, yd),
    ):
        squared.append(
            (
                origin_e
                + sign * ((i - origin_i) * cos_t * k * delta_i / inc_i)
                + ((j - origin_j) * sin_t * k * delta_j / inc_j),
                origin_n
                - sign * ((i - origin_i) * sin_t * k * delta_i / inc_i)
                + ((j - origin_j) * cos_t * k * delta_j / inc_j),
            )
        )
        model_i = origin_i + sign * (
            ((x - origin_e) * cos_t - (y - origin_n) * sin_t) * (inc_i / (k * delta_i))
        )
        model_j = origin_j + (
            ((x - origin_e) * sin_t + (y - origin_n) * cos_t) * (inc_j / (k * delta_j))
        )
        residual_i.append(i - model_i)
        residual_j.append(j - model_j)

    return {
        "method_code": 9666 if right else 1049,
        "bearing_j": theta,
        "bin_width_i": delta_i,
        "bin_width_j": delta_j,
        "scale_factor": k,
        # L761-L765: the reported origin is squared corner A.
        "origin": (float(ia), float(ja), squared[0][0], squared[0][1]),
        "squared": squared,
        "di": max(max(residual_i), -min(residual_i)),
        "dj": max(max(residual_j), -min(residual_j)),
    }
