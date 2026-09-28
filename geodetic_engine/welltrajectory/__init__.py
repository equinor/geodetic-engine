"""Well trajectories: directional surveys positioned in a CRS.

Public entry points:

* :func:`compute_trajectory` -- a survey, a wellhead and a CRS, into a
  :class:`WellTrajectory` of positions with their provenance.
* :class:`MinimumCurvature` -- the minimum curvature method on its own, free of
  any CRS, for offsets, dogleg severity and interpolation along the arcs.
* :class:`Method` -- how offsets are placed in the CRS.
* :func:`plot_trajectory` -- a 3D view; needs the ``plot`` extra.

:mod:`~geodetic_engine.welltrajectory.utils` holds adapters onto these, such as
:func:`~geodetic_engine.welltrajectory.utils.from_payload` for a trajectory
request given as one JSON body.

See ``README.md`` beside this file for the conventions.

Example:
    >>> from geodetic_engine.welltrajectory import Survey, compute_trajectory
    >>> survey = Survey([0, 1000, 2000], [0, 30, 60], [45, 45, 45])
    >>> trajectory = compute_trajectory(
    ...     survey, (500000.0, 6600000.0, 25.0), "EPSG:32631", north="GN"
    ... )
    >>> trajectory.to_dataframe()  # doctest: +SKIP
"""

from geodetic_engine.welltrajectory.errors import (
    DegenerateSurveyError,
    InvalidSurveyError,
    UnitError,
    WellTrajectoryError,
)
from geodetic_engine.welltrajectory.methods import LocalFrame, Method, Placement
from geodetic_engine.welltrajectory.minimum_curvature import MinimumCurvature, Stations
from geodetic_engine.welltrajectory.plot import plot_trajectory
from geodetic_engine.welltrajectory.survey import (
    NorthReference,
    Survey,
    Wellhead,
    angle_factor,
    length_factor,
)
from geodetic_engine.welltrajectory.trajectory import (
    WellTrajectory,
    compute_trajectory,
)

__all__ = [
    "DegenerateSurveyError",
    "InvalidSurveyError",
    "LocalFrame",
    "Method",
    "MinimumCurvature",
    "NorthReference",
    "Placement",
    "Stations",
    "Survey",
    "UnitError",
    "WellTrajectory",
    "WellTrajectoryError",
    "Wellhead",
    "angle_factor",
    "compute_trajectory",
    "length_factor",
    "plot_trajectory",
]
