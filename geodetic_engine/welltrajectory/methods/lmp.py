"""LMP: the offsets integrated over the ellipsoid, at the depth they were drilled.

Each step between stations is laid on the ellipsoid as a change of latitude
and longitude, using the radii of curvature at the step's own latitude and
elevation:

    d(phi)    = dN / (M(phi) + h)
    d(lambda) = dE / ((N(phi) + h) cos(phi))

with ``M`` and ``N`` the meridian and prime vertical radii and ``h`` the
step's elevation, ``wellhead z - TVD``. A step drilled deep sweeps a larger
angle than the same step at the surface, which is the curvature correction
long, deep wells are said to need. The latitudes are found by fixed-point
iteration over the whole well at once, which converges in a couple of passes
because a well spans a tiny fraction of a radius.
"""

from __future__ import annotations

import numpy as np

from geodetic_engine.welltrajectory.methods.base import (
    FloatArray,
    LocalFrame,
    Placement,
)

# Each pass shrinks the latitude error by the reach over the earth's radius.
_PASSES = 3


def place(offsets: FloatArray, frame: LocalFrame) -> Placement:
    """Integrate the offsets over the ellipsoid, station by station."""
    semi_major, semi_minor = frame.semi_axes
    eccentricity = 1.0 - (semi_minor / semi_major) ** 2
    origin = np.radians(frame.latitude)
    d_east, d_north = np.diff(offsets[:, 0]), np.diff(offsets[:, 1])
    height = frame.height - 0.5 * (offsets[:-1, 2] + offsets[1:, 2])

    def radii(latitude: FloatArray) -> tuple[FloatArray, FloatArray]:
        weight = np.sqrt(1.0 - eccentricity * np.sin(latitude) ** 2)
        return semi_major * (1.0 - eccentricity) / weight**3, semi_major / weight

    latitude = np.full(len(offsets), origin)
    for _ in range(_PASSES):
        middle = 0.5 * (latitude[:-1] + latitude[1:])
        meridian, _ = radii(middle)
        latitude = origin + np.concatenate(
            [[0.0], np.cumsum(d_north / (meridian + height))]
        )
    middle = 0.5 * (latitude[:-1] + latitude[1:])
    _, normal = radii(middle)
    turned = d_east / ((normal + height) * np.cos(middle))
    longitude = np.radians(frame.longitude) + np.concatenate([[0.0], np.cumsum(turned)])

    return Placement(
        xy=frame.to_crs(np.degrees(longitude), np.degrees(latitude)),
        operations=(
            "integrated offsets over "
            f"{frame.geographic_crs.name}'s ellipsoid at each step's elevation",
            f"converted from {frame.geographic_crs.name} to "
            f"{frame.horizontal_crs.name}",
        ),
    )
