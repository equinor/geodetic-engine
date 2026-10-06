"""Ways of placing minimum curvature offsets in a CRS, one module each.

Every method has the same signature, :data:`~.base.Place`: it takes the
offsets of each station from the wellhead, in metres against true north, and
the :class:`~.base.LocalFrame` describing the wellhead and its datum, and
returns a :class:`~.base.Placement`. They differ only in the geometry they
assume between a flat survey and a curved earth; see each module.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Literal

from geodetic_engine.welltrajectory.methods import (
    azimuthal_equidistant,
    enu,
    grid_north_local,
    lmp,
)
from geodetic_engine.welltrajectory.methods.base import LocalFrame, Place, Placement

# The values of Method, so that editors offer them and type checkers check them.
type MethodName = Literal["AzimuthalEquidistant", "GridNorthLocal", "ENU", "LMP"]


class Method(StrEnum):
    """How offsets from the wellhead are georeferenced in the trajectory CRS.

    Give one as ``method=`` to any
    :class:`~geodetic_engine.welltrajectory.TrajectoryInput` constructor or to
    :func:`~geodetic_engine.welltrajectory.compute_trajectory`: a member, such
    as ``Method.LMP``, or its value as text, such as ``"LMP"``, in any case.
    :doc:`/user-guide/welltrajectory/georeferencing` compares them.

    Example:
        >>> [method.value for method in Method]
        ['AzimuthalEquidistant', 'GridNorthLocal', 'ENU', 'LMP']
    """

    AZIMUTHAL_EQUIDISTANT = "AzimuthalEquidistant"
    """The default. The offsets are coordinates of an azimuthal equidistant
    projection centred on the wellhead, converted to the CRS. Any CRS."""

    GRID_NORTH_LOCAL = "GridNorthLocal"
    """The offsets turned by the grid convergence and scaled by the point scale
    factor, both taken at the wellhead. A projected CRS only."""

    ENU = "ENU"
    """The offsets are a local east-north-up plane at the wellhead, taken
    through geocentric coordinates. Any CRS."""

    LMP = "LMP"
    """Each step laid on the ellipsoid with the radii of curvature at its own
    latitude and elevation. Any CRS."""


PLACEMENTS: dict[Method, Place] = {
    Method.AZIMUTHAL_EQUIDISTANT: azimuthal_equidistant.place,
    Method.GRID_NORTH_LOCAL: grid_north_local.place,
    Method.ENU: enu.place,
    Method.LMP: lmp.place,
}

__all__ = ["PLACEMENTS", "LocalFrame", "Method", "MethodName", "Place", "Placement"]
