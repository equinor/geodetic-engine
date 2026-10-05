"""Exceptions raised while computing a well trajectory.

CRS and coordinate operation failures keep the types
:mod:`geodetic_engine.geodesy` raises for them; these cover the survey itself.
"""

from geodetic_engine.errors import GeodeticEngineError


class WellTrajectoryError(GeodeticEngineError):
    """Base class for all well trajectory failures."""


class InvalidSurveyError(WellTrajectoryError):
    """The survey cannot describe a wellbore as given.

    Too few stations, a measured depth that does not strictly increase, an
    angle out of range, or a depth asked for outside the surveyed interval.
    """


class DegenerateSurveyError(InvalidSurveyError):
    """Two stations point in opposite directions, so no arc joins them."""


class InvalidInputError(WellTrajectoryError):
    """A trajectory input is incomplete or malformed.

    A required setting is missing, a setting has a value it cannot take, a
    survey file does not follow the format, or a payload lacks a field.
    """


class UnitError(WellTrajectoryError):
    """A unit was not recognised, or is not a unit of the quantity needed.

    Never resolved to a default: a length read in the wrong unit moves every
    position by a plausible looking amount.
    """
