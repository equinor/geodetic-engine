"""The outline of a bin grid, as a polygon ring.

The OSDU SeismicBinGrid spatial area is the polygon through the four corners,
written as an outer ring: closed (the last point repeats the first) and
counterclockwise, the OGC and GeoJSON convention. The corners are visited A, B,
D, C, or A, C, D, B where that order would be clockwise, which it is for every
right-handed grid on an easting/northing map grid.
"""

from __future__ import annotations

import logging
import math
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from geodetic_engine.bingrid.corners import LABELS, BinGridCorners
from geodetic_engine.bingrid.errors import DegenerateBinGridError

logger = logging.getLogger(__name__)

# Rings through the corners in the two possible directions, as label indices.
_RING = (0, 1, 3, 2)
_REVERSED_RING = (0, 2, 3, 1)


@dataclass(frozen=True, slots=True)
class BinGridOutline:
    """A closed, counterclockwise ring through the four corners.

    Attributes:
        labels: Corner labels of the five ring points, first and last equal.
        coordinates: The five ring points, in ``xy`` order.
    """

    labels: tuple[str, ...]
    coordinates: tuple[tuple[float, float], ...]

    def to_json_dict(self) -> dict[str, Any]:
        """Render the outline as plain data."""
        return {
            "labels": list(self.labels),
            "coordinates": [list(point) for point in self.coordinates],
        }


def outline(corners: BinGridCorners) -> BinGridOutline:
    """The outer ring of a bin grid.

    Args:
        corners: The corners, normally the squared ones.

    Returns:
        A closed, counterclockwise ring of five points.

    Raises:
        DegenerateBinGridError: If the outline A-B-D-C of the corners does not
            enclose a convex area.
    """
    return outline_of([(c.easting, c.northing) for c in corners])


def outline_of(points: Sequence[Sequence[float]]) -> BinGridOutline:
    """The outer ring through four points given in order A, B, C, D.

    For points that are not bin grid corners as such, such as the corners'
    longitude and latitude.

    Args:
        points: Four ``xy`` pairs, in order A, B, C, D.

    Returns:
        A closed, counterclockwise ring of five points.

    Raises:
        DegenerateBinGridError: If the outline A-B-D-C of the points does not
            enclose a convex area: it crosses itself, turns back, or has no area.
    """
    if len(points) != 4 or any(len(point) != 2 for point in points):
        raise DegenerateBinGridError(
            f"an outline needs 4 xy pairs, in order A, B, C, D, got {points!r}"
        )
    xy = [(float(x), float(y)) for x, y in points]
    turns = _turns([xy[index] for index in _RING])
    if all(0.0 < turn < math.inf for turn in turns):
        ring = _RING
    elif all(-math.inf < turn < 0.0 for turn in turns):
        ring = _REVERSED_RING
    else:
        raise DegenerateBinGridError(
            f"the outline A-B-D-C of the corners {xy} does not enclose a convex "
            "area, as happens when corners are swapped or lie on one line"
        )
    order = (*ring, ring[0])
    return BinGridOutline(
        labels=tuple(LABELS[index] for index in order),
        coordinates=tuple(xy[index] for index in order),
    )


def _turns(ring: Sequence[tuple[float, float]]) -> list[float]:
    """Cross product of the two edges at each point of a ring: positive to the left."""
    turns = []
    for index, (x1, y1) in enumerate(ring):
        x0, y0 = ring[index - 1]
        x2, y2 = ring[(index + 1) % len(ring)]
        turns.append((x1 - x0) * (y2 - y1) - (y1 - y0) * (x2 - x1))
    return turns
