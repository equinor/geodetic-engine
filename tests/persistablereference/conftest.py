"""Fixtures for the persistableReference tests.

The payloads in ``payloads.jsonl`` are real, taken unchanged from an OSDU
coordinate reference data catalogue. They are committed rather than read from a
catalogue at test time so that the suite runs without one, and so that a change
in behaviour shows up as a diff against a fixed input.

The unit and Molodensky-Badekas payloads are written here instead: OSDU
publishes units on other record kinds, and no catalogue to hand states a
Molodensky-Badekas transformation, but both are shapes this package claims to
read.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tests.persistablereference.utils import WorkedExample, examples

_PAYLOADS = Path(__file__).parent / "payloads.jsonl"


def _catalogue() -> dict[str, str]:
    """Every committed payload, by the case it illustrates."""
    with _PAYLOADS.open(encoding="utf-8") as stream:
        entries = [json.loads(line) for line in stream if line.strip()]
    return {entry["case"]: entry["payload"] for entry in entries}


@pytest.fixture(params=examples(), ids=lambda case: case.identifier)
def example(request: pytest.FixtureRequest) -> WorkedExample:
    """One case from ``worked_examples.json``."""
    found: WorkedExample = request.param
    return found


@pytest.fixture(scope="session")
def payloads() -> dict[str, str]:
    """Real persistableReference payloads, by case."""
    return _catalogue()


@pytest.fixture(scope="session")
def payload(payloads: dict[str, str]):  # type: ignore[no-untyped-def]
    """Look one payload up by case."""

    def lookup(case: str) -> str:
        return payloads[case]

    return lookup


def cases() -> list[str]:
    """Every case name, for parametrising over the whole set."""
    return sorted(_catalogue())


MOLODENSKY_BADEKAS = json.dumps(
    {
        "type": "ST",
        "name": "La_Canoa_To_WGS_1984_2",
        "authCode": {"auth": "EPSG", "code": "1771"},
        "wkt": (
            'GEOGTRAN["La_Canoa_To_WGS_1984_2",'
            'GEOGCS["GCS_La_Canoa",DATUM["D_La_Canoa",'
            'SPHEROID["International_1924",6378388.0,297.0]],'
            'PRIMEM["Greenwich",0.0],UNIT["Degree",0.0174532925199433]],'
            'GEOGCS["GCS_WGS_1984",DATUM["D_WGS_1984",'
            'SPHEROID["WGS_1984",6378137.0,298.257223563]],'
            'PRIMEM["Greenwich",0.0],UNIT["Degree",0.0174532925199433]],'
            'METHOD["Molodensky_Badekas"],'
            'PARAMETER["X_Axis_Translation",-270.933],'
            'PARAMETER["Y_Axis_Translation",115.599],'
            'PARAMETER["Z_Axis_Translation",-360.226],'
            'PARAMETER["X_Axis_Rotation",-5.266],'
            'PARAMETER["Y_Axis_Rotation",-1.238],'
            'PARAMETER["Z_Axis_Rotation",2.381],'
            'PARAMETER["Scale_Difference",-5.109],'
            'PARAMETER["X_Coordinate_of_Rotation_Origin",2464351.59],'
            'PARAMETER["Y_Coordinate_of_Rotation_Origin",-5783466.61],'
            'PARAMETER["Z_Coordinate_of_Rotation_Origin",974809.81],'
            'OPERATIONACCURACY[5.0],AUTHORITY["EPSG",1771]]'
        ),
    }
)
"""A ten parameter transformation, which the catalogues to hand do not state."""

FOOT = json.dumps(
    {
        "type": "USO",
        "name": "foot",
        "symbol": "ft",
        "scaleOffset": {"scale": 0.3048, "offset": 0.0},
        "baseMeasurement": {"ancestry": "Length"},
    }
)
"""A unit converting by a scale and an offset."""

DEGREES_FAHRENHEIT = json.dumps(
    {
        "type": "UAD",
        "name": "degrees Fahrenheit",
        "symbol": "degF",
        "abcd": {"a": 2298.35, "b": 5.0, "c": 9.0, "d": 0.0},
        "baseMeasurement": {"ancestry": "Thermodynamic_Temperature"},
    }
)
"""A unit converting by a polynomial that reduces to a scale and an offset."""
