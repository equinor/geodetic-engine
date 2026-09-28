"""Grid north local: the offsets turned and scaled onto the grid at the wellhead.

The survey's offsets are ground distances referenced to true north. Turning
them by the grid convergence gamma and stretching them by the point scale
factor k, both taken at the wellhead, puts them on the grid:

    E_grid = k (E cos gamma - N sin gamma)
    N_grid = k (E sin gamma + N cos gamma)

which is the vector form of ``grid azimuth = true azimuth - gamma`` and ``grid
distance = k * ground distance``. Treating gamma and k as constant over the
whole well is the approximation: fine near the wellhead, and off by the change
of k and gamma across the reach further out. Needs a projected CRS, since a
geographic one has no grid to be local to.
"""

from __future__ import annotations

import numpy as np

from geodetic_engine.geodesy import UnsupportedCRSError
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
    gamma = np.radians(frame.factors.grid_convergence[0])
    scale = float(frame.factors.scale_factor[0])
    cosine, sine = np.cos(gamma), np.sin(gamma)
    rotation = np.array([[cosine, sine], [-sine, cosine]])
    grid = scale * offsets[:, :2] @ rotation
    return Placement(
        xy=frame.wellhead + grid / frame.horizontal_unit,
        operations=(
            f"turned offsets onto grid north by {np.degrees(gamma):.6f} deg and "
            f"scaled by {scale:.9f}, both taken at the wellhead",
        ),
    )
