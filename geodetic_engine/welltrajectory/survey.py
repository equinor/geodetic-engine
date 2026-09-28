"""Survey input: stations, the wellhead they hang from, and their units.

A directional survey states, at each station, the measured depth along the
hole (MD), the inclination from vertical and the azimuth of the hole's
direction. Units are named explicitly and resolved once, here, so everything
downstream works in metres and radians.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from enum import StrEnum
from urllib.parse import unquote

import numpy as np
from numpy.typing import ArrayLike, NDArray

from geodetic_engine.persistablereference import (
    PersistableReferenceError,
    UnitReference,
    looks_like_reference,
    parse_persistable_reference,
)
from geodetic_engine.welltrajectory.errors import InvalidSurveyError, UnitError

type FloatArray = NDArray[np.float64]

# Metres per unit, by casefolded symbol or name.
_LENGTHS = {
    **dict.fromkeys(("m", "metre", "meter", "metres", "meters"), 1.0),
    **dict.fromkeys(("ft", "foot", "feet", "international foot"), 0.3048),
    **dict.fromkeys(("ftus", "ft[us]", "usft", "us survey foot"), 1200 / 3937),
    "km": 1000.0,
    "cm": 0.01,
    "mm": 0.001,
    "in": 0.0254,
}
# Radians per unit, by casefolded symbol or name.
_ANGLES = {
    **dict.fromkeys(("deg", "dega", "degree", "degrees"), math.pi / 180),
    **dict.fromkeys(("rad", "radian", "radians"), 1.0),
    **dict.fromkeys(("gon", "grad", "grads"), math.pi / 200),
}
_UNIT_OF_MEASURE = "UnitOfMeasure:"
# OSDU baseMeasurement ancestries accepted for each quantity, casefolded.
_MEASUREMENTS = {"length": {"length"}, "angle": {"plane_angle", "angle"}}


class NorthReference(StrEnum):
    """What a survey's azimuths are measured from."""

    GRID = "GN"
    TRUE = "TN"


@dataclass(frozen=True, slots=True)
class Wellhead:
    """Where the survey starts: the reference point MD and TVD count from.

    Attributes:
        x: First coordinate value in the trajectory CRS, in ``xy`` order:
            longitude for a geographic CRS, easting for a projected one.
        y: Second coordinate value: latitude or northing.
        z: Elevation of the reference point, positive up, in the trajectory's
            vertical unit. Positions are reported as ``z - TVD``.
    """

    x: float
    y: float
    z: float = 0.0


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
    """Metres per ``unit``.

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
    return _factor(unit, _LENGTHS, "length")


def angle_factor(unit: str) -> float:
    """Radians per ``unit``, read the same ways as :func:`length_factor`."""
    return _factor(unit, _ANGLES, "angle")


def _factor(unit: str, table: dict[str, float], quantity: str) -> float:
    text = unquote(str(unit)).strip()
    if looks_like_reference(text):
        return _reference_factor(text, quantity)
    if _UNIT_OF_MEASURE in text:
        text = text.split(_UNIT_OF_MEASURE, 1)[1].split(":", 1)[0]
    if (factor := table.get(text.casefold())) is None:
        raise UnitError(f"{unit!r} is not a {quantity} unit this package knows")
    return factor


def _reference_factor(payload: str, quantity: str) -> float:
    """The scale of an OSDU unit persistableReference, refusing an offset."""
    try:
        reference = parse_persistable_reference(payload)
        if not isinstance(reference, UnitReference):
            raise UnitError(f"the payload given as a {quantity} unit is not a unit")
        scale, offset = reference.scale, reference.offset
    except PersistableReferenceError as error:
        raise UnitError(f"could not read a {quantity} unit: {error}") from error
    if reference.measurement.casefold() not in _MEASUREMENTS[quantity] or offset:
        raise UnitError(
            f"{reference.symbol!r} ({reference.measurement or 'no measurement'}) "
            f"is not a {quantity} unit converted by a plain scale"
        )
    return scale


def _column(values: ArrayLike, name: str) -> FloatArray:
    column = np.asarray(values, dtype=np.float64)
    if column.ndim != 1 or not np.all(np.isfinite(column)):
        raise InvalidSurveyError(f"{name} must be a flat sequence of finite numbers")
    return column
