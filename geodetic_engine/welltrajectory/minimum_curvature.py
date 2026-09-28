"""The minimum curvature method, on its own and free of any CRS.

Between two survey stations the hole is taken to follow the circular arc
tangent to both stations' directions. Each station's direction is the unit
tangent, in a local (east, north, down) frame,

    t = (sin I sin A, sin I cos A, cos I)

and the arc between stations 1 and 2 turns through the dogleg angle beta
between t1 and t2. Its chord, which is the step in position, is

    delta = (dMD / 2) * RF * (t1 + t2),  RF = (2 / beta) tan(beta / 2)

where RF, the ratio factor, is 1 on a straight segment. Positions are the
running sum of the steps. Everything is vectorised over stations.

Interpolating at a depth inside an interval stays on that arc: the tangent is
the spherical interpolation of t1 and t2, and the position is the minimum
curvature step from station 1 over the partial arc. So a survey resampled
this way, and fed back through the method, lands on the same positions.

Units are whatever the caller gives: offsets come back in the unit of ``md``.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from numpy.typing import ArrayLike, NDArray

from geodetic_engine.welltrajectory.errors import (
    DegenerateSurveyError,
    InvalidSurveyError,
)

type FloatArray = NDArray[np.float64]

# Below this dogleg the ratio factor is taken from its series, whose next term
# 17 beta^6 / 20160 is below 1e-27 there; above it the closed form is exact.
_SMALL_DOGLEG = 1e-4
# Closer than this to a U-turn the arc's radius, and so its chord, is unbounded.
_REVERSAL = 1e-6


@dataclass(frozen=True, slots=True, eq=False)
class Stations:
    """Survey points with the positions minimum curvature gives them.

    Attributes:
        md: Measured depth of each point.
        inclination: Inclination in radians.
        azimuth: Azimuth in radians, clockwise from north, in ``[0, 2 pi)``.
        offsets: ``(n, 3)`` position relative to the first survey station, as
            (east, north, true vertical depth down), in the unit of :attr:`md`.
        curvature: Dogleg per unit of MD, in radians, of the arc each point
            ends; zero at the first survey station.
        is_survey_station: True where the point is a surveyed station rather
            than one interpolated between them.
    """

    md: FloatArray
    inclination: FloatArray
    azimuth: FloatArray
    offsets: FloatArray
    curvature: FloatArray
    is_survey_station: NDArray[np.bool_]

    @property
    def east(self) -> FloatArray:
        """Offset east of the first station."""
        return self.offsets[:, 0]

    @property
    def north(self) -> FloatArray:
        """Offset north of the first station."""
        return self.offsets[:, 1]

    @property
    def tvd(self) -> FloatArray:
        """True vertical depth below the first station, positive down."""
        return self.offsets[:, 2]

    def dls(self, per_length: float = 30.0) -> FloatArray:
        """Dogleg severity in degrees per ``per_length`` of MD, e.g. per 30 m."""
        return np.degrees(self.curvature) * per_length

    def __len__(self) -> int:
        return len(self.md)


class MinimumCurvature:
    """Positions along a surveyed wellbore by the minimum curvature method.

    Args:
        md: Measured depths, strictly increasing, in any length unit.
        inclination: Inclinations from vertical in radians, in ``[0, pi]``.
        azimuth: Azimuths in radians, clockwise from the north the offsets are
            to be referenced to.

    Raises:
        InvalidSurveyError: If there are fewer than two stations, the arrays
            disagree in length or hold non-finite values, MD does not strictly
            increase, or an inclination is outside ``[0, pi]``.
        DegenerateSurveyError: If two consecutive stations point in opposite
            directions.

    Example:
        A quarter circle from vertical to horizontal over 1000 m has radius
        2000 / pi:

        >>> model = MinimumCurvature([0, 1000], [0, np.pi / 2], [0, 0])
        >>> model.stations.offsets[-1].round(3).tolist()
        [0.0, 636.62, 636.62]
    """

    __slots__ = ("_curvature", "_dogleg", "_stations", "_tangents")

    def __init__(
        self, md: ArrayLike, inclination: ArrayLike, azimuth: ArrayLike
    ) -> None:
        md, inclination, azimuth = _validated(md, inclination, azimuth)
        tangents = _tangents(inclination, azimuth)
        dogleg = _angle_between(tangents[:-1], tangents[1:])
        if np.any(np.pi - dogleg < _REVERSAL):
            at = md[1:][np.pi - dogleg < _REVERSAL][0]
            raise DegenerateSurveyError(
                f"the hole reverses direction at MD {at:g}, which no arc can join"
            )
        steps = _chord(np.diff(md), dogleg, tangents[:-1], tangents[1:])
        offsets = np.vstack([np.zeros(3), np.cumsum(steps, axis=0)])
        curvature = np.concatenate([[0.0], dogleg / np.diff(md)])

        self._tangents = tangents
        self._dogleg = dogleg
        self._curvature = curvature
        self._stations = Stations(
            md=md,
            inclination=inclination,
            azimuth=azimuth,
            offsets=offsets,
            curvature=curvature,
            is_survey_station=np.ones(len(md), dtype=bool),
        )

    @property
    def stations(self) -> Stations:
        """The surveyed stations with their positions."""
        return self._stations

    @property
    def dogleg(self) -> FloatArray:
        """Angle turned over each interval, in radians; one fewer than stations."""
        return self._dogleg

    def interpolate(self, md: ArrayLike) -> Stations:
        """Points on the surveyed arcs at the given measured depths.

        A depth equal to a station's returns that station exactly.

        Args:
            md: Measured depths, in any order, within the surveyed interval.

        Returns:
            One point per depth, in the order given.

        Raises:
            InvalidSurveyError: If a depth is outside the surveyed interval.
        """
        survey = self._stations
        depth = np.atleast_1d(np.asarray(md, dtype=np.float64))
        if depth.ndim != 1 or not np.all(np.isfinite(depth)):
            raise InvalidSurveyError("interpolation depths must be finite numbers")
        outside = (depth < survey.md[0]) | (depth > survey.md[-1])
        if np.any(outside):
            raise InvalidSurveyError(
                f"MD {depth[outside][0]:g} is outside the surveyed interval "
                f"[{survey.md[0]:g}, {survey.md[-1]:g}]"
            )

        index = np.clip(np.searchsorted(survey.md, depth, side="right") - 1, 0, None)
        index = np.minimum(index, len(survey.md) - 2)
        length = np.diff(survey.md)[index]
        fraction = (depth - survey.md[index]) / length
        beta = self._dogleg[index]
        first, second = self._tangents[index], self._tangents[index + 1]

        tangent = _slerp(first, second, beta, fraction)
        offsets = survey.offsets[index] + _chord(
            fraction * length, fraction * beta, first, tangent
        )
        inclination = np.arccos(np.clip(tangent[:, 2], -1.0, 1.0))
        horizontal = np.hypot(tangent[:, 0], tangent[:, 1])
        azimuth = np.where(
            horizontal > 1e-12,
            np.mod(np.arctan2(tangent[:, 0], tangent[:, 1]), 2 * np.pi),
            survey.azimuth[index],
        )
        curvature = self._curvature[index + 1]

        station = np.searchsorted(survey.md, depth).clip(max=len(survey.md) - 1)
        exact = survey.md[station] == depth
        pick = station[exact]
        inclination[exact] = survey.inclination[pick]
        azimuth[exact] = survey.azimuth[pick]
        offsets[exact] = survey.offsets[pick]
        curvature[exact] = survey.curvature[pick]
        return Stations(depth, inclination, azimuth, offsets, curvature, exact)

    def resample(self, step: float, *, include_survey: bool = True) -> Stations:
        """Points every ``step`` of MD from the first station to the last.

        Args:
            step: Spacing in the unit of MD.
            include_survey: Also keep every surveyed station, flagged as such.

        Returns:
            The points, in order of MD. The last station is always included.

        Raises:
            InvalidSurveyError: If ``step`` is not positive and finite.
        """
        if not (np.isfinite(step) and step > 0):
            raise InvalidSurveyError(f"a resampling step must be positive, not {step}")
        md = self._stations.md
        count = int(np.floor((md[-1] - md[0]) / step + 1e-9)) + 1
        grid = md[0] + step * np.arange(count)
        # Snap onto a station that floating point arithmetic only nearly hits.
        right = np.searchsorted(md, grid).clip(1, len(md) - 1)
        left = right - 1
        closer_left = np.abs(md[left] - grid) <= np.abs(md[right] - grid)
        nearest = np.where(closer_left, md[left], md[right])
        grid = np.where(np.abs(grid - nearest) <= 1e-9 * step, nearest, grid)
        depths = np.union1d(grid, md if include_survey else md[-1:])
        return self.interpolate(depths)


def _validated(
    md: ArrayLike, inclination: ArrayLike, azimuth: ArrayLike
) -> tuple[FloatArray, FloatArray, FloatArray]:
    columns = [
        np.asarray(values, dtype=np.float64) for values in (md, inclination, azimuth)
    ]
    if (
        any(column.ndim != 1 for column in columns)
        or len({len(c) for c in columns}) != 1
    ):
        raise InvalidSurveyError("md, inclination and azimuth must be equal length 1D")
    md, inclination, azimuth = columns
    if len(md) < 2:
        raise InvalidSurveyError("a survey needs at least two stations")
    if not all(np.all(np.isfinite(column)) for column in columns):
        raise InvalidSurveyError("survey values must be finite")
    if np.any(np.diff(md) <= 0):
        at = md[1:][np.diff(md) <= 0][0]
        raise InvalidSurveyError(f"MD must strictly increase; it does not at {at:g}")
    if np.any((inclination < 0) | (inclination > np.pi)):
        raise InvalidSurveyError("inclination must lie between 0 and pi radians")
    return md, inclination, np.mod(azimuth, 2 * np.pi)


def _tangents(inclination: FloatArray, azimuth: FloatArray) -> FloatArray:
    """Unit direction of the hole, as (east, north, down)."""
    sine = np.sin(inclination)
    return np.column_stack(
        [sine * np.sin(azimuth), sine * np.cos(azimuth), np.cos(inclination)]
    )


def _angle_between(first: FloatArray, second: FloatArray) -> FloatArray:
    """Angle between unit vectors, well conditioned at every size."""
    cross = np.linalg.norm(np.cross(first, second), axis=1)
    dot: FloatArray = np.einsum("ij,ij->i", first, second)
    return np.arctan2(cross, dot)


def _ratio_factor(beta: FloatArray) -> FloatArray:
    small = beta < _SMALL_DOGLEG
    safe = np.where(small, 1.0, beta)
    series = 1.0 + beta**2 / 12.0 + beta**4 / 120.0
    return np.where(small, series, 2.0 / safe * np.tan(safe / 2.0))


def _chord(
    length: FloatArray, beta: FloatArray, first: FloatArray, second: FloatArray
) -> FloatArray:
    """Minimum curvature step over each arc of the given length and dogleg."""
    return (0.5 * length * _ratio_factor(beta))[:, np.newaxis] * (first + second)


def _slerp(
    first: FloatArray, second: FloatArray, beta: FloatArray, fraction: FloatArray
) -> FloatArray:
    """Unit tangent a fraction of the way round each arc."""
    small = beta < _SMALL_DOGLEG
    sine = np.where(small, 1.0, np.sin(beta))
    before = np.where(small, 1.0 - fraction, np.sin((1.0 - fraction) * beta) / sine)
    after = np.where(small, fraction, np.sin(fraction * beta) / sine)
    tangent = before[:, np.newaxis] * first + after[:, np.newaxis] * second
    return tangent / np.linalg.norm(tangent, axis=1, keepdims=True)
