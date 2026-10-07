"""Well trajectories: directional surveys positioned in a CRS.

Public entry points:

* :class:`TrajectoryInput` -- everything a trajectory is computed from: the
  survey, the wellhead, the CRS and the settings. Built from arrays, rows, a
  DataFrame, a survey file or an OSDU request body, and computed with
  :meth:`TrajectoryInput.compute`.
* :class:`WellTrajectory` -- the result: positions, angles and dogleg
  severity at every point, with their provenance.
* :func:`compute_trajectory` -- the same computation from a :class:`Survey`,
  a wellhead and a CRS given separately.
* :class:`MinimumCurvature` -- the minimum curvature method on its own, free of
  any CRS, for offsets, dogleg severity and interpolation along the arcs.
* :class:`Method` -- how offsets are georeferenced in the CRS.
* :func:`plot_trajectory` and :func:`open_in_browser` -- a 3D view to rotate,
  pan and zoom, in a notebook or a browser; needs the ``plot`` extra.

The input and the result are in ``geodetic_engine.welltrajectory.datamodels``;
a synthetic survey file to try them on is in the ``example_data`` folder
beside this file. See ``README.md`` there too, for the conventions.

Example:
    >>> from geodetic_engine.welltrajectory import TrajectoryInput
    >>> well = TrajectoryInput.from_arrays(
    ...     md=[0, 1000, 2000],
    ...     inclination=[0, 30, 60],
    ...     azimuth=[45, 45, 45],
    ...     wellhead=(500000.0, 6600000.0, 25.0),
    ...     crs="EPSG:32631",
    ...     north_reference="GN",
    ... )
    >>> well.compute().to_dataframe()  # doctest: +SKIP
"""

from geodetic_engine.welltrajectory.datamodels import (
    TrajectoryInput,
    TrajectoryOptions,
    WellTrajectory,
)
from geodetic_engine.welltrajectory.errors import (
    DegenerateSurveyError,
    InvalidInputError,
    InvalidSurveyError,
    UnitError,
    WellTrajectoryError,
)
from geodetic_engine.welltrajectory.methods import LocalFrame, Method, Placement
from geodetic_engine.welltrajectory.minimum_curvature import MinimumCurvature, Stations
from geodetic_engine.welltrajectory.plot import open_in_browser, plot_trajectory
from geodetic_engine.welltrajectory.survey import (
    NorthReference,
    Survey,
    Wellhead,
    angle_factor,
    length_factor,
)
from geodetic_engine.welltrajectory.trajectory import compute_trajectory

__all__ = [
    "DegenerateSurveyError",
    "InvalidInputError",
    "InvalidSurveyError",
    "LocalFrame",
    "Method",
    "MinimumCurvature",
    "NorthReference",
    "Placement",
    "Stations",
    "Survey",
    "TrajectoryInput",
    "TrajectoryOptions",
    "UnitError",
    "WellTrajectory",
    "WellTrajectoryError",
    "Wellhead",
    "angle_factor",
    "compute_trajectory",
    "length_factor",
    "open_in_browser",
    "plot_trajectory",
]
