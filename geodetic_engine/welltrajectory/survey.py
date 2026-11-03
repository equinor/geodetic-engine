"""Survey input: stations, the wellhead they hang from, and their units.

A directional survey states, at each station, the measured depth along the
hole (MD), the inclination from vertical and the azimuth of the hole's
direction. Units are named explicitly and resolved once, here, so everything
downstream works in metres and radians.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from enum import StrEnum

import numpy as np
from numpy.typing import ArrayLike, NDArray

from geodetic_engine import units
from geodetic_engine.welltrajectory.errors import (
    InvalidInputError,
    InvalidSurveyError,
    UnitError,
)

type FloatArray = NDArray[np.float64]


class NorthReference(StrEnum):
    """What a survey's azimuths are measured from, ``"GN"`` or ``"TN"``.

    Read in any case, and also as ``"grid"``, ``"grid north"`` or
    ``"GRID_NORTH"``, and ``"true"``, ``"true north"`` or ``"TRUE_NORTH"``.
    """

    GRID = "GN"
    TRUE = "TN"

    @classmethod
    def _missing_(cls, value: object) -> NorthReference | None:
        text = re.sub(r"[\W_]+", "", str(value).casefold()).removesuffix("north")
        names = {"gn": cls.GRID, "grid": cls.GRID, "tn": cls.TRUE, "true": cls.TRUE}
        return names.get(text)


@dataclass(frozen=True, slots=True)
class Wellhead:
    """Where the survey starts: the position of its first station.

    True vertical depth and the offsets count from here. Measured depth is kept
    as surveyed, so for a survey whose first station is not at MD 0 the
    wellhead is that tie-in point, such as the seabed.

    Attributes:
        x: First coordinate value in the trajectory CRS, in ``xy`` order:
            longitude for a geographic CRS, easting for a projected one.
        y: Second coordinate value: latitude or northing.
        z: Elevation of the first station, positive up, in the trajectory's
            vertical unit. Positions are reported as ``z - TVD``.
    """

    x: float
    y: float
    z: float = 0.0

    def __post_init__(self) -> None:
        if not all(math.isfinite(value) for value in (self.x, self.y, self.z)):
            raise InvalidInputError(f"the wellhead must be finite, not {self}")


@dataclass(frozen=True, slots=True, init=False, eq=False)
class Survey:
    """Survey stations, with the units their values are stated in.

    Attributes:
        md: Measured depth of each station, in :attr:`md_unit`.
        inclination: Inclination from vertical, in :attr:`angle_unit`.
        azimuth: Azimuth of the hole's direction, clockwise from north, in
            :attr:`angle_unit`; None for an inclination-only survey, which is
            then taken to head north throughout.
        md_unit: Unit of :attr:`md`: a symbol such as ``"m"`` or ``"ft"``, an
            OSDU unit id, or an OSDU unit persistableReference.
        angle_unit: Unit of the angles, ``"degree"`` unless stated.

    Example:
        >>> survey = Survey([0, 1000, 2000], [0, 30, 60], [0, 45, 45])
        >>> survey.md_metres.tolist()
        [0.0, 1000.0, 2000.0]
    """

    md: FloatArray
    inclination: FloatArray
    azimuth: FloatArray | None
    md_unit: str
    angle_unit: str

    def __init__(
        self,
        md: ArrayLike,
        inclination: ArrayLike,
        azimuth: ArrayLike | None = None,
        *,
        md_unit: str = "m",
        angle_unit: str = "degree",
    ) -> None:
        values = [_column(md, "md"), _column(inclination, "inclination")]
        if azimuth is not None:
            values.append(_column(azimuth, "azimuth"))
        if len({len(column) for column in values}) != 1:
            raise InvalidSurveyError(
                "md, inclination and azimuth must give one value per station, "
                f"not {[len(column) for column in values]}"
            )
        length_factor(md_unit)
        angle_factor(angle_unit)
        object.__setattr__(self, "md", values[0])
        object.__setattr__(self, "inclination", values[1])
        object.__setattr__(self, "azimuth", values[2] if azimuth is not None else None)
        object.__setattr__(self, "md_unit", md_unit)
        object.__setattr__(self, "angle_unit", angle_unit)

    @property
    def md_metres(self) -> FloatArray:
        """Measured depths in metres."""
        return self.md * length_factor(self.md_unit)

    @property
    def inclination_radians(self) -> FloatArray:
        """Inclinations in radians."""
        return self.inclination * angle_factor(self.angle_unit)

    @property
    def azimuth_degrees(self) -> FloatArray:
        """Azimuths in degrees, zero throughout for an inclination-only survey."""
        if self.azimuth is None:
            return np.zeros_like(self.md)
        return np.degrees(self.azimuth * angle_factor(self.angle_unit))


def length_factor(unit: str) -> float:
    """Metres per ``unit``, as :func:`geodetic_engine.units.length_unit` reads it.

    Args:
        unit: A symbol or name (``"m"``, ``"ft"``, ``"ftUS"``), an OSDU unit id
            such as ``"dev:reference-data--UnitOfMeasure:ft:"``, or an OSDU unit
            persistableReference.

    Raises:
        UnitError: If the unit is not recognised, or is not a length.

    Example:
        >>> length_factor("ft")
        0.3048
    """
    try:
        return units.length_unit(unit).scale
    except units.UnitError as error:
        raise UnitError(str(error)) from error


def angle_factor(unit: str) -> float:
    """Radians per ``unit``, as :func:`geodetic_engine.units.angle_unit` reads it.

    Raises:
        UnitError: If the unit is not recognised, or is not an angle.
    """
    try:
        return units.angle_unit(unit).scale
    except units.UnitError as error:
        raise UnitError(str(error)) from error


def _column(values: ArrayLike, name: str) -> FloatArray:
    column = np.asarray(values, dtype=np.float64)
    if column.ndim != 1 or not np.all(np.isfinite(column)):
        raise InvalidSurveyError(f"{name} must be a flat sequence of finite numbers")
    return column
