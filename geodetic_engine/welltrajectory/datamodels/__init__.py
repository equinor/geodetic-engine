"""The data a trajectory is computed from, and the data it computes.

* :class:`TrajectoryInput`, in ``trajectory_input``: the survey, wellhead,
  CRS and settings, with a constructor for each form a survey comes in.
* :class:`WellTrajectory`, in ``well_trajectory``: the positions along the
  wellbore, with how they were arrived at.

Both are importable from :mod:`geodetic_engine.welltrajectory`.
"""

from geodetic_engine.welltrajectory.datamodels.trajectory_input import (
    TrajectoryInput,
    TrajectoryOptions,
)
from geodetic_engine.welltrajectory.datamodels.well_trajectory import WellTrajectory

__all__ = ["TrajectoryInput", "TrajectoryOptions", "WellTrajectory"]
