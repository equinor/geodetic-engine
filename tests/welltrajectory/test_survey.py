"""Units and survey input."""

from __future__ import annotations

import json
from urllib.parse import quote

import numpy as np
import pytest

from geodetic_engine.welltrajectory import (
    InvalidSurveyError,
    NorthReference,
    Survey,
    UnitError,
    angle_factor,
    length_factor,
)


def _unit_payload(symbol: str, scale: float, ancestry: str) -> str:
    return json.dumps(
        {
            "scaleOffset": {"scale": scale, "offset": 0.0},
            "symbol": symbol,
            "baseMeasurement": {"ancestry": ancestry, "type": "UM"},
            "type": "USO",
        }
    )


@pytest.mark.parametrize(
    ("unit", "metres"),
    [
        ("m", 1.0),
        ("Metre", 1.0),
        ("ft", 0.3048),
        ("ftUS", 1200 / 3937),
        ("dev:reference-data--UnitOfMeasure:ft:", 0.3048),
        (_unit_payload("ft", 0.3048, "Length"), 0.3048),
        (quote(_unit_payload("ft", 0.3048, "Length")), 0.3048),
    ],
)
def test_length_units_resolve(unit: str, metres: float) -> None:
    assert length_factor(unit) == pytest.approx(metres, rel=1e-15)


@pytest.mark.parametrize(
    "unit",
    [
        "fathom",
        "deg",
        "",
        _unit_payload("deg/30m", 5.81776417331443e-4, "Rotation_Per_Length"),
        '{"scaleOffset":{"scale":1.0,"offset":273.15},"symbol":"degC",'
        '"baseMeasurement":{"ancestry":"Length","type":"UM"},"type":"USO"}',
        _unit_payload("m0", 0.0, "Length"),
        _unit_payload("-ft", -0.3048, "Length"),
    ],
    ids=[
        "unknown",
        "an-angle",
        "empty",
        "a-rate-per-length",
        "with-offset",
        "zero-scale",
        "negative-scale",
    ],
)
def test_anything_that_is_not_a_plain_length_is_refused(unit: str) -> None:
    """A rate per length names a length in its ancestry, and is still not one."""
    with pytest.raises(UnitError):
        length_factor(unit)


@pytest.mark.parametrize(
    ("unit", "radians"),
    [
        ("degree", np.pi / 180),
        ("rad", 1.0),
        ("gon", np.pi / 200),
        (_unit_payload("dega", np.pi / 180, "Plane_Angle"), np.pi / 180),
    ],
)
def test_angle_units_resolve(unit: str, radians: float) -> None:
    assert angle_factor(unit) == pytest.approx(radians, rel=1e-15)


def test_a_survey_converts_once_to_metres_and_radians() -> None:
    survey = Survey([0, 1000], [0, 90], [0, 45], md_unit="ft")

    assert survey.md_metres.tolist() == [0.0, 304.8]
    assert survey.inclination_radians[-1] == pytest.approx(np.pi / 2)
    assert survey.azimuth_degrees.tolist() == [0.0, 45.0]


def test_an_inclination_only_survey_heads_north() -> None:
    survey = Survey([0, 1000], [0, 10])

    assert survey.azimuth is None
    assert survey.azimuth_degrees.tolist() == [0.0, 0.0]


@pytest.mark.parametrize(
    ("md", "inclination", "azimuth"),
    [
        ([0, 100], [0], [0, 0]),
        ([0, 100], [0, 1], [0, None]),
        ([[0, 100]], [[0, 1]], None),
    ],
    ids=["lengths-differ", "missing-azimuth", "not-flat"],
)
def test_malformed_stations_are_refused(
    md: list[object], inclination: list[object], azimuth: list[object] | None
) -> None:
    with pytest.raises(InvalidSurveyError):
        Survey(md, inclination, azimuth)  # type: ignore[arg-type]


def test_an_unknown_unit_is_refused_when_the_survey_is_made() -> None:
    with pytest.raises(UnitError):
        Survey([0, 1], [0, 0], md_unit="cubit")


@pytest.mark.parametrize(
    ("text", "north"),
    [
        ("gn", NorthReference.GRID),
        ("Grid", NorthReference.GRID),
        ("grid north", NorthReference.GRID),
        ("GRID_NORTH", NorthReference.GRID),
        ("TN", NorthReference.TRUE),
        ("True North", NorthReference.TRUE),
        ("TRUE_NORTH", NorthReference.TRUE),
    ],
)
def test_the_north_reference_is_read_under_its_usual_names(
    text: str, north: NorthReference
) -> None:
    assert NorthReference(text) is north


@pytest.mark.parametrize("text", ["MN", "magnetic north", "north", ""])
def test_magnetic_north_and_bare_north_are_refused(text: str) -> None:
    with pytest.raises(ValueError):
        NorthReference(text)
