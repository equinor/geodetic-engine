"""Which declared axis each coordinate value belongs to.

This package gives coordinate values in ``xy`` order, whatever order a CRS
declares its axes in: easting or longitude first, then northing or latitude,
then any height. This is the one place that order is worked out.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol

from pyproj import CRS

# PROJ's always_xy only reorders a literal north+east pair into east+north; a
# south/west axis (Krovak, Cape/Hartebeesthoek Lo grids) is left in its
# declared order, so those directions must not be bucketed in here too.
_EASTINGS = frozenset({"east"})
_NORTHINGS = frozenset({"north"})
_VERTICALS = frozenset({"up", "down"})


class Axis(Protocol):
    """A declared axis, as pyproj's ``Axis`` and ``geodesy.AxisSpec`` state it."""

    @property
    def abbrev(self) -> str: ...

    @property
    def direction(self) -> str: ...


def value_axis_order(axes: Sequence[Axis]) -> tuple[int, ...]:
    """Indices of the declared axes, in coordinate value order.

    Axes are identified by direction, falling back to the abbreviation for
    polar CRSs whose axes share a direction: ``EPSG:32661`` declares both of
    its axes pointing south, and only the ``N`` and ``E`` abbreviations
    distinguish them.

    Args:
        axes: The axes a CRS declares, in declared order.

    Returns:
        A permutation of ``range(len(axes))``.
    """
    directions = [axis.direction.lower() for axis in axes]
    east = next((i for i, d in enumerate(directions) if d in _EASTINGS), None)
    north = next((i for i, d in enumerate(directions) if d in _NORTHINGS), None)
    if east is None or north is None or east == north:
        # Both axes of a polar CRS share a direction, so resolve the pair
        # together from the abbreviations rather than mixing the two
        # sources and risking them naming the same axis.
        abbreviations = [axis.abbrev.upper() for axis in axes]
        by_letter = (
            _first_index(abbreviations, "E"),
            _first_index(abbreviations, "N"),
        )
        if by_letter[0] is not None and by_letter[0] != by_letter[1]:
            east, north = by_letter
    if east is None or north is None or east == north:
        return tuple(range(len(axes)))
    vertical = [i for i, d in enumerate(directions) if d in _VERTICALS]
    placed = {east, north, *vertical}
    rest = [i for i in range(len(axes)) if i not in placed]
    return (east, north, *vertical, *rest)


def value_axis_units(crs: CRS) -> tuple[float, ...]:
    """SI units per unit of each of a CRS's axes, in coordinate value order."""
    axes = crs.axis_info
    return tuple(axes[index].unit_conversion_factor for index in value_axis_order(axes))


def _first_index(values: list[str], wanted: str) -> int | None:
    """Position of the first matching entry, or None."""
    return values.index(wanted) if wanted in values else None
