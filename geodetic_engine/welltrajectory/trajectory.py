"""Computing a trajectory: a survey, a wellhead and a CRS, into positions.

The survey is reduced to offsets from the wellhead by minimum curvature, in
metres against true north, and those offsets are placed in the CRS by one of
the :mod:`~geodetic_engine.welltrajectory.methods`. Grid azimuths are turned
onto true north first, with the grid convergence at the wellhead from
:func:`~geodetic_engine.geodesy.projection_factors`.

Nothing here changes datum. The wellhead is given in the trajectory CRS and
every position is computed on that CRS's own datum; moving the result to
another datum is :meth:`WellTrajectory.to_geographic`, which goes through
:class:`~geodetic_engine.geodesy.Transformation` and so needs the operation
named, or a bound CRS, exactly as any other datum change does.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

import numpy as np
import pandas as pd
from numpy.typing import ArrayLike, NDArray

from geodetic_engine.geodesy import (
    CoordinateReferenceSystem,
    ProjectionFactors,
    Transformation,
    TransformationResult,
    UnsupportedCRSError,
    projection_factors,
)
from geodetic_engine.welltrajectory.methods import PLACEMENTS, LocalFrame, Method
from geodetic_engine.welltrajectory.minimum_curvature import (
    MinimumCurvature,
    Stations,
)
from geodetic_engine.welltrajectory.survey import (
    NorthReference,
    Survey,
    Wellhead,
    length_factor,
)

type FloatArray = NDArray[np.float64]

# Metres per foot, near enough to tell international and US survey feet apart
# from every metric unit when choosing a dogleg severity length.
_FOOT = 0.3048


@dataclass(frozen=True, slots=True, eq=False)
class WellTrajectory:
    """Positions along a wellbore, with how they were arrived at.

    One array entry per point, surveyed or interpolated.

    Attributes:
        crs: The trajectory CRS.
        method: How the offsets were placed in it.
        north_reference: What the survey's azimuths were measured from.
        md_unit: Unit of :attr:`md`.
        z_unit: Unit of :attr:`z`, :attr:`tvd` and the offsets.
        md: Measured depth.
        inclination: Inclination from vertical, in degrees.
        azimuth_true: Azimuth from true north, in degrees.
        azimuth_grid: Azimuth from grid north, in degrees; equal to
            :attr:`azimuth_true` in a geographic CRS.
        east: Offset east of the wellhead, against true north.
        north: Offset north of the wellhead, against true north.
        tvd: True vertical depth below the wellhead.
        x: First coordinate value in :attr:`crs`, in its own unit.
        y: Second coordinate value in :attr:`crs`.
        z: Elevation, ``wellhead z - tvd``.
        curvature: Dogleg per metre of MD, in radians; see :meth:`dls`.
        is_survey_station: True for a surveyed station, False for a point
            interpolated between them.
        frame: The wellhead and datum the trajectory was placed on.
        model: The minimum curvature model, in metres, for interpolating.
        local_crs: The local CRS the offsets were read in, if the method used
            one.
        operations: What was done, in order.
    """

    crs: CoordinateReferenceSystem
    method: Method
    north_reference: NorthReference
    md_unit: str
    z_unit: str
    md: FloatArray
    inclination: FloatArray
    azimuth_true: FloatArray
    azimuth_grid: FloatArray
    east: FloatArray
    north: FloatArray
    tvd: FloatArray
    x: FloatArray
    y: FloatArray
    z: FloatArray
    curvature: FloatArray
    is_survey_station: NDArray[np.bool_]
    frame: LocalFrame
    model: MinimumCurvature
    local_crs: CoordinateReferenceSystem | None
    operations: tuple[str, ...]

    @property
    def factors(self) -> ProjectionFactors:
        """Grid convergence and scale factor at the wellhead."""
        return self.frame.factors

    def __len__(self) -> int:
        return len(self.md)

    @property
    def dls_length(self) -> float:
        """Default MD length of a dogleg severity: 100 in feet, else 30."""
        return 100.0 if abs(length_factor(self.md_unit) - _FOOT) < 1e-5 else 30.0

    def dls(self, per_length: float | None = None) -> FloatArray:
        """Dogleg severity in degrees per ``per_length`` of MD, in :attr:`md_unit`.

        Defaults to :attr:`dls_length`, per 100 ft or per 30 m.
        """
        length = self.dls_length if per_length is None else per_length
        return np.degrees(self.curvature) * length_factor(self.md_unit) * length

    def interpolate(self, md: ArrayLike) -> WellTrajectory:
        """The trajectory at the given measured depths, on its arcs.

        Args:
            md: Depths in :attr:`md_unit`, within the surveyed interval.

        Raises:
            InvalidSurveyError: If a depth is outside the surveyed interval.
        """
        depths = np.asarray(md, dtype=np.float64) * length_factor(self.md_unit)
        stations = self.model.interpolate(depths)
        note = f"interpolated {len(stations)} points on the minimum curvature arcs"
        return self._replace(stations, note)

    def resample(self, step: float, *, include_survey: bool = True) -> WellTrajectory:
        """The trajectory every ``step`` of MD, in :attr:`md_unit`.

        Args:
            step: Spacing of the points.
            include_survey: Keep the surveyed stations as well.
        """
        stations = self.model.resample(
            step * length_factor(self.md_unit), include_survey=include_survey
        )
        note = f"resampled every {step:g} {self.md_unit}: {len(stations)} points"
        return self._replace(stations, note)

    def projection_factors(self) -> ProjectionFactors:
        """Grid convergence and scale factor at every point."""
        return projection_factors(self.crs, np.column_stack([self.x, self.y]))

    def to_geographic(
        self, target: Any = "EPSG:4326", operation: Any = None
    ) -> TransformationResult:
        """Horizontal positions in another CRS, usually a geographic one.

        A datum change needs ``operation`` named, or a bound trajectory CRS,
        exactly as for :class:`~geodetic_engine.geodesy.Transformation`.
        """
        source = self.crs if self.crs.crs.is_bound else self.frame.horizontal_crs
        transformation = Transformation(source, target, operation=operation)
        return transformation.transform(np.column_stack([self.x, self.y]))

    def to_dataframe(self) -> pd.DataFrame:
        """One row per point, with the dogleg severity at its default length."""
        return pd.DataFrame(
            {
                "md": self.md,
                "inclination": self.inclination,
                "azimuth_true": self.azimuth_true,
                "azimuth_grid": self.azimuth_grid,
                "east": self.east,
                "north": self.north,
                "tvd": self.tvd,
                "x": self.x,
                "y": self.y,
                "z": self.z,
                "dls": self.dls(),
                "is_survey_station": self.is_survey_station,
            }
        )

    def plot(self, **options: Any) -> Any:
        """A 3D plotly figure; see :func:`~.plot.plot_trajectory`."""
        from geodetic_engine.welltrajectory.plot import plot_trajectory

        return plot_trajectory(self, **options)

    def _replace(self, stations: Stations, note: str) -> WellTrajectory:
        head = _describe(self.frame, self.north_reference, self.z_unit)
        return _placed(
            stations,
            self.frame,
            self.model,
            method=self.method,
            north=self.north_reference,
            md_unit=self.md_unit,
            z_unit=self.z_unit,
            operations=(*head, note),
        )


def compute_trajectory(
    survey: Survey,
    wellhead: Wellhead | Sequence[float],
    crs: Any,
    *,
    north: NorthReference | str = NorthReference.GRID,
    method: Method | str = Method.AZIMUTHAL_EQUIDISTANT,
    z_unit: str = "m",
    md_step: float | None = None,
) -> WellTrajectory:
    """Position a surveyed wellbore in a CRS.

    Args:
        survey: The survey stations.
        wellhead: Where MD and TVD count from: ``(x, y)`` or ``(x, y, z)`` in
            ``crs``, with ``z`` its elevation in ``z_unit``.
        crs: The trajectory CRS: anything
            :meth:`~geodetic_engine.geodesy.CoordinateReferenceSystem.from_user_input`
            accepts, including an OSDU persistableReference or a bound CRS.
        north: What the azimuths are measured from, ``"GN"`` or ``"TN"``.
        method: How the offsets are placed in ``crs``; see :class:`Method`.
        z_unit: Unit of the wellhead elevation and of every reported depth,
            elevation and offset.
        md_step: If given, also compute a point every ``md_step`` of MD, in
            the survey's MD unit, between the surveyed stations.

    Returns:
        The positions, and how they were computed.

    Raises:
        InvalidSurveyError: If the survey cannot describe a wellbore.
        UnitError: If a unit is not recognised.
        UnsupportedCRSError: If the CRS has no geographic or projected
            horizontal part, or grid azimuths are given in a geographic CRS.

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

    azimuth = survey.azimuth_degrees
    if north is NorthReference.GRID:
        azimuth = frame.factors.to_true_azimuth(azimuth)
    model = MinimumCurvature(
        survey.md_metres, survey.inclination_radians, np.radians(azimuth)
    )

    operations = [*_describe(frame, north, z_unit)]
    if survey.azimuth is None:
        operations.append("the survey states no azimuth; taken as 0 throughout")
    operations.append(f"minimum curvature over {len(survey.md)} survey stations")
    stations = model.stations
    if md_step is not None:
        stations = model.resample(md_step * length_factor(survey.md_unit))
        operations.append(
            f"resampled every {md_step:g} {survey.md_unit}: {len(stations)} points"
        )
    return _placed(
        stations,
        frame,
        model,
        method=method,
        north=north,
        md_unit=survey.md_unit,
        z_unit=z_unit,
        operations=tuple(operations),
    )


def _placed(
    stations: Stations,
    frame: LocalFrame,
    model: MinimumCurvature,
    *,
    method: Method,
    north: NorthReference,
    md_unit: str,
    z_unit: str,
    operations: tuple[str, ...],
) -> WellTrajectory:
    """Place stations with ``method`` and state them in the caller's units."""
    placement = PLACEMENTS[method](stations.offsets, frame)
    z_factor = length_factor(z_unit)
    azimuth = np.degrees(stations.azimuth)
    return WellTrajectory(
        crs=frame.crs,
        method=method,
        north_reference=north,
        md_unit=md_unit,
        z_unit=z_unit,
        md=stations.md / length_factor(md_unit),
        inclination=np.degrees(stations.inclination),
        azimuth_true=azimuth,
        azimuth_grid=frame.factors.to_grid_azimuth(azimuth),
        east=stations.east / z_factor,
        north=stations.north / z_factor,
        tvd=stations.tvd / z_factor,
        x=placement.xy[:, 0],
        y=placement.xy[:, 1],
        z=(frame.height - stations.tvd) / z_factor,
        curvature=stations.curvature,
        is_survey_station=stations.is_survey_station,
        frame=frame,
        model=model,
        local_crs=placement.local_crs,
        operations=(*operations, *placement.operations),
    )


def _describe(frame: LocalFrame, north: NorthReference, z_unit: str) -> tuple[str, ...]:
    """Provenance shared by a trajectory and everything interpolated from it."""
    x, y = frame.wellhead
    height = frame.height / length_factor(z_unit)
    described = [
        f"trajectory CRS {frame.crs.authority_code or frame.crs.name}; wellhead "
        f"at ({x:.12g}, {y:.12g}), elevation {height:g} {z_unit}"
    ]
    if frame.factors.projected:
        described.append(
            f"grid convergence {frame.factors.grid_convergence[0]:.9f} deg and "
            f"scale factor {frame.factors.scale_factor[0]:.9f} at the wellhead"
        )
    described.append(
        "azimuths against grid north, turned onto true north by the grid convergence"
        if north is NorthReference.GRID
        else "azimuths against true north"
    )
    return tuple(described)
