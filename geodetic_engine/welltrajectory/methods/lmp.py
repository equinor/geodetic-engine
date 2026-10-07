"""LMP: the offsets integrated over the ellipsoid, at the depth they were drilled.

Each step between stations is laid on the ellipsoid as a change of latitude
and longitude, using the radii of curvature at the step's own latitude and
elevation:

    d(phi)    = dN / (M(phi) + h)
    d(lambda) = dE / ((N(phi) + h) cos(phi))

with ``M`` and ``N`` the meridian and prime vertical radii and ``h`` the
step's elevation, ``wellhead z - TVD``. As in ENU, the elevation stands in
for the ellipsoidal height, which it is not: an error ``dh`` in it moves a
station by ``d dh / R``, 3 cm for 40 m at 5 km. A step drilled deep sweeps a
larger angle than the same step at the surface, which is the curvature
correction long, deep wells are said to need. The latitudes are found by
fixed-point iteration over the whole well at once, which converges in a couple
of passes because a well spans a tiny fraction of a radius.

Queries are evaluated from the preceding original survey station. Adding,
removing or reordering query points never changes those station positions.
"""

from __future__ import annotations

import numpy as np

from geodetic_engine.welltrajectory.methods.base import (
    FloatArray,
    LocalFrame,
    Placement,
)
from geodetic_engine.welltrajectory.minimum_curvature import Stations

# Each pass shrinks the latitude error by the reach over the earth's radius.
_PASSES = 3


def place(offsets: FloatArray, frame: LocalFrame) -> Placement:
    """Integrate the offsets over the ellipsoid, station by station."""
    longitude, latitude = _geographic(offsets, frame)
    return _placement(longitude, latitude, frame)


def interpolate(stations: Stations, survey: Stations, frame: LocalFrame) -> Placement:
    """Position queries relative to their preceding original survey stations."""
    longitude, latitude = _geographic(survey.offsets, frame)
    index = np.searchsorted(survey.md, stations.md, side="right") - 1
    start = survey.offsets[index]
    delta = stations.offsets - start
    height = frame.height - 0.5 * (start[:, 2] + stations.tvd)
    origin = latitude[index]
    latitude = origin.copy()
    semi_major, semi_minor = frame.semi_axes
    eccentricity_squared = 1.0 - (semi_minor / semi_major) ** 2
    for _ in range(_PASSES):
        middle = 0.5 * (origin + latitude)
        meridian, _ = _radii(middle, semi_major, eccentricity_squared)
        latitude = origin + delta[:, 1] / (meridian + height)
    middle = 0.5 * (origin + latitude)
    _, normal = _radii(middle, semi_major, eccentricity_squared)
    longitude = longitude[index] + delta[:, 0] / ((normal + height) * np.cos(middle))
    return _placement(longitude, latitude, frame)


def _geographic(
    offsets: FloatArray, frame: LocalFrame
) -> tuple[FloatArray, FloatArray]:
    semi_major, semi_minor = frame.semi_axes
    eccentricity_squared = 1.0 - (semi_minor / semi_major) ** 2
    origin = np.radians(frame.latitude)
    d_east, d_north = np.diff(offsets[:, 0]), np.diff(offsets[:, 1])
    height = frame.height - 0.5 * (offsets[:-1, 2] + offsets[1:, 2])

    latitude = np.full(len(offsets), origin)
    for _ in range(_PASSES):
        middle = 0.5 * (latitude[:-1] + latitude[1:])
        meridian, _ = _radii(middle, semi_major, eccentricity_squared)
        latitude = origin + np.concatenate(
            [[0.0], np.cumsum(d_north / (meridian + height))]
        )
    middle = 0.5 * (latitude[:-1] + latitude[1:])
    _, normal = _radii(middle, semi_major, eccentricity_squared)
    turned = d_east / ((normal + height) * np.cos(middle))
    longitude = np.radians(frame.longitude) + np.concatenate([[0.0], np.cumsum(turned)])
    return longitude, latitude


def _radii(
    latitude: FloatArray, semi_major: float, eccentricity_squared: float
) -> tuple[FloatArray, FloatArray]:
    weight = np.sqrt(1.0 - eccentricity_squared * np.sin(latitude) ** 2)
    return semi_major * (1.0 - eccentricity_squared) / weight**3, semi_major / weight


def _placement(
    longitude: FloatArray, latitude: FloatArray, frame: LocalFrame
) -> Placement:
    return Placement(
        xy=frame.to_crs(np.degrees(longitude), np.degrees(latitude)).reshape(-1, 2),
        operations=(
            "integrated offsets over "
            f"{frame.geographic_crs.name}'s ellipsoid at each step's elevation",
            f"converted from {frame.geographic_crs.name} to "
            f"{frame.horizontal_crs.name}",
        ),
    )
