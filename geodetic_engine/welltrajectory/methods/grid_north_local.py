"""Grid north local: the offsets turned and scaled onto the grid at the wellhead.

The survey's offsets are distances referenced to true north. Turning them by
the grid convergence gamma and stretching them by the point scale factor k,
both taken at the wellhead, puts them on the grid:

    E_grid = k (E cos gamma - N sin gamma)
    N_grid = k (E sin gamma + N cos gamma)

Holding gamma and k constant over the whole well is the approximation.
The projection must preserve angles locally, so that one scale factor applies
in every direction. Native axis order, directions and units are respected.
"""

from __future__ import annotations

import numpy as np

from geodetic_engine.geodesy import UnsupportedCRSError
from geodetic_engine.geodesy.factors import _grid_axes, _require_conformal
from geodetic_engine.welltrajectory.methods.base import (
    FloatArray,
    LocalFrame,
    Placement,
)


def place(offsets: FloatArray, frame: LocalFrame) -> Placement:
    """Turn by the wellhead's grid convergence and scale by its scale factor."""
    if not frame.factors.projected:
        raise UnsupportedCRSError(
            f"grid north local needs a projected CRS, and {frame.crs.name} "
            "is geographic"
        )
    _require_conformal(frame.factors)
    gamma = np.radians(frame.factors.grid_convergence[0])
    scale = float(frame.factors.scale_factor[0])
    cosine, sine = np.cos(gamma), np.sin(gamma)
    rotation = np.array([[cosine, sine], [-sine, cosine]])
    grid = scale * offsets[:, :2] @ rotation @ _grid_axes(frame.horizontal_crs)
    units = np.array(
        [
            frame.horizontal_crs.axes[index].unit_conversion_factor
            for index in frame.horizontal_crs.value_axis_order[:2]
        ]
    )
    return Placement(
        xy=frame.wellhead + grid / units,
        operations=(
            f"turned offsets onto grid north by {np.degrees(gamma):.6f} deg and "
            f"scaled by {scale:.9f}, both taken at the wellhead",
        ),
    )
