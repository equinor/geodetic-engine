"""Supporting machinery for the well trajectory package.

Adapters onto :func:`~geodetic_engine.welltrajectory.compute_trajectory`
rather than trajectory computations in their own right.
"""

from geodetic_engine.welltrajectory.utils.payload import (
    INTERPOLATION_STEP,
    PayloadResult,
    from_payload,
)

__all__ = ["INTERPOLATION_STEP", "PayloadResult", "from_payload"]
