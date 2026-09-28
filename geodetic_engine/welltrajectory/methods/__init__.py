"""Ways of placing minimum curvature offsets in a CRS, one module each.

Every method has the same signature, :data:`~.base.Place`: it takes the
offsets of each station from the wellhead, in metres against true north, and
the :class:`~.base.LocalFrame` describing the wellhead and its datum, and
returns a :class:`~.base.Placement`. They differ only in the geometry they
assume between a flat survey and a curved earth; see each module.
"""

from __future__ import annotations

from enum import StrEnum

from geodetic_engine.welltrajectory.methods import (
    azimuthal_equidistant,
    enu,
    grid_north_local,
    lmp,
)
from geodetic_engine.welltrajectory.methods.base import LocalFrame, Place, Placement


class Method(StrEnum):
    """How offsets from the wellhead are placed in the trajectory CRS."""

    AZIMUTHAL_EQUIDISTANT = "AzimuthalEquidistant"
    GRID_NORTH_LOCAL = "GridNorthLocal"
    ENU = "ENU"
    LMP = "LMP"


PLACEMENTS: dict[Method, Place] = {
    Method.AZIMUTHAL_EQUIDISTANT: azimuthal_equidistant.place,
    Method.GRID_NORTH_LOCAL: grid_north_local.place,
    Method.ENU: enu.place,
    Method.LMP: lmp.place,
}

__all__ = ["PLACEMENTS", "LocalFrame", "Method", "Place", "Placement"]
