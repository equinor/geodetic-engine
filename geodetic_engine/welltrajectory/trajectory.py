"""Computing a trajectory: a survey, a wellhead and a CRS, into positions.

The survey is reduced to offsets from the wellhead by minimum curvature, in
metres against true north, and those offsets are georeferenced in the CRS by
one of the :mod:`~geodetic_engine.welltrajectory.methods`. Grid azimuths are
turned onto true north first, with the grid convergence at the wellhead from
:func:`~geodetic_engine.geodesy.projection_factors`.

Nothing here changes datum. The wellhead is given in the trajectory CRS and
every position is computed on that CRS's own datum; moving the result to
another datum is :meth:`WellTrajectory.to_geographic`, which goes through
:class:`~geodetic_engine.geodesy.Transformation` and so needs the operation
named, or a bound CRS, exactly as any other datum change does.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

import numpy as np
from numpy.typing import ArrayLike

from geodetic_engine.geodesy import UnsupportedCRSError
from geodetic_engine.welltrajectory.datamodels.well_trajectory import (
    WellTrajectory,
    _describe,
    _georeferenced,
    _model_step,
)
from geodetic_engine.welltrajectory.methods import LocalFrame, Method, MethodName
from geodetic_engine.welltrajectory.minimum_curvature import MinimumCurvature
from geodetic_engine.welltrajectory.survey import (
    NorthReference,
    Survey,
    Wellhead,
    length_factor,
)


def compute_trajectory(
    survey: Survey,
    wellhead: Wellhead | Sequence[float],
    crs: Any,
    *,
    north: NorthReference | str = NorthReference.GRID,
    method: Method | MethodName = Method.AZIMUTHAL_EQUIDISTANT,
    z_unit: str = "m",
    md_step: float | None = None,
    md_points: ArrayLike | None = None,
    name: str | None = None,
) -> WellTrajectory:
    """Position a surveyed wellbore in a CRS.

    :meth:`TrajectoryInput.compute` calls this with everything an input holds.

    Args:
        survey: The survey stations.
        wellhead: The position of the first station, which TVD and the
            offsets count from: ``(x, y)`` or ``(x, y, z)`` in ``crs``, with
            ``z`` its elevation in ``z_unit``.
        crs: The trajectory CRS: anything
            :meth:`~geodetic_engine.geodesy.CoordinateReferenceSystem.from_user_input`
            accepts, including an OSDU persistableReference or a bound CRS.
        north: What the azimuths are measured from, ``"GN"`` or ``"TN"``.
        method: How the offsets are georeferenced in ``crs``:
            ``"AzimuthalEquidistant"``, the default, ``"GridNorthLocal"``,
            ``"ENU"`` or ``"LMP"``, or the :class:`Method` member; see
            :class:`Method` for what each does.
        z_unit: Unit of the wellhead elevation and of every reported depth,
            elevation and offset.
        md_step: If given, also compute a point every ``md_step`` of MD, in
            the survey's MD unit, between the surveyed stations.
        md_points: If given, also compute a point at each of these measured
            depths, in the survey's MD unit.
        name: The well's name, carried to the result.

    Returns:
        The positions, and how they were computed. Points added by
        ``md_step`` or ``md_points`` are in MD order among the stations, with
        :attr:`~WellTrajectory.is_survey_station` False.

    Raises:
        InvalidInputError: If a wellhead value is not finite.
        InvalidSurveyError: If the survey cannot describe a wellbore, or a
            depth in ``md_points`` is outside it.
        UnitError: If a unit is not recognised.
        geodetic_engine.geodesy.UnsupportedCRSError: If the CRS has no
            geographic or projected horizontal part, grid azimuths are given
            in a geographic CRS or in one whose projection does not preserve
            angles at the wellhead, or ``GridNorthLocal`` is asked for there.

    Example:
        >>> survey = Survey([0, 1000, 2000], [0, 30, 60], [45, 45, 45])
        >>> trajectory = compute_trajectory(
        ...     survey, (500000.0, 6600000.0, 25.0), "EPSG:32631", north="GN"
        ... )
        >>> trajectory.tvd[-1].round(2), trajectory.z[-1].round(2)  # doctest: +SKIP
        (1653.99, -1628.99)
    """
    point = wellhead if isinstance(wellhead, Wellhead) else Wellhead(*wellhead)
    north = NorthReference(north)
    method = Method(method)
    frame = LocalFrame.at(crs, point.x, point.y, point.z * length_factor(z_unit))
    if north is NorthReference.GRID and not frame.factors.projected:
        raise UnsupportedCRSError(
            f"azimuths are given against grid north, but {frame.crs.name} is "
            "geographic and has no grid; give them against true north"
        )
    if north is NorthReference.GRID and not frame.factors.conformal:
        raise UnsupportedCRSError(
            f"azimuths are given against grid north, but {frame.horizontal_crs.name} "
            "does not preserve angles at the wellhead, so no grid convergence "
            "turns them onto true north; give them against true north"
        )

    azimuth = survey.azimuth_degrees
    if north is NorthReference.GRID:
        azimuth = frame.factors.to_true_azimuth(azimuth)
    model = MinimumCurvature(
        survey.md_metres, survey.inclination_radians, np.radians(azimuth)
    )

    operations = [*_describe(frame, north, z_unit)]
    if survey.azimuth is None:
        operations.append("the survey states no azimuth; taken as 0 throughout")
    if survey.md[0] != 0:
        operations.append(
            f"the first station, at MD {survey.md[0]:g} {survey.md_unit}, is the "
            "wellhead: a tie-in point that TVD and the offsets count from"
        )
    operations.append(_model_step(model))
    to_metres = length_factor(survey.md_unit)
    stations = model.stations
    if md_step is not None:
        stations = model.resample(md_step * to_metres)
        operations.append(
            f"resampled every {md_step:g} {survey.md_unit}: {len(stations)} points"
        )
    listed = np.asarray([] if md_points is None else md_points, dtype=np.float64)
    if listed.size:
        stations = model.interpolate(np.union1d(stations.md, listed * to_metres))
        operations.append(
            f"added points at {listed.size} measured depths asked for: "
            f"{len(stations)} points"
        )
    return _georeferenced(
        stations,
        frame,
        model,
        method=method,
        north=north,
        md_unit=survey.md_unit,
        z_unit=z_unit,
        operations=tuple(operations),
        name=name,
    )
