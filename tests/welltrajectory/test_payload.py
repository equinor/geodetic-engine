"""The legacy ``/convertTrajectory`` request bodies, through the adapter."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import numpy as np
import pytest

from geodetic_engine.persistablereference import (
    CrsReference,
    parse_persistable_reference,
)
from geodetic_engine.welltrajectory import (
    InvalidSurveyError,
    Method,
    UnitError,
)
from geodetic_engine.welltrajectory.utils import from_payload

from .conftest import DATA, REQUESTS, request

EXAMPLES = DATA / "requests"


@pytest.mark.parametrize("path", REQUESTS, ids=lambda path: path.stem)
def test_every_legacy_example_computes(path: Path) -> None:
    body = request(path)

    result = from_payload(body)

    stations = result.stations
    assert len(stations) >= len(body["inputStations"])
    for values in (stations.x, stations.y, stations.z, stations.dls()):
        assert np.all(np.isfinite(values))
    assert stations.operations
    assert (result.stations_i is not None) == bool(body.get("MD_i"))
    assert (result.local_crs is not None) == (
        stations.method is Method.AZIMUTHAL_EQUIDISTANT
    )


def test_the_local_crs_is_a_readable_persistable_reference() -> None:
    result = from_payload(request(REQUESTS[1]))

    assert result.local_crs is not None
    assert isinstance(parse_persistable_reference(result.local_crs), CrsReference)


def test_json_text_is_read_as_readily_as_a_mapping() -> None:
    body = request(REQUESTS[1])

    assert from_payload(json.dumps(body)).stations.x == pytest.approx(
        from_payload(body).stations.x
    )


def test_listed_depths_come_back_in_order() -> None:
    result = from_payload(request(EXAMPLES / "06_feet_with_md_interpolation.json"))

    assert result.stations_i is not None
    assert result.stations_i.md == pytest.approx([1640, 4920, 8200])
    assert result.stations_i.md_unit == "ft"


def test_an_interval_covers_the_survey_end_to_end() -> None:
    result = from_payload(request(EXAMPLES / "08_md_interval_interpolation.json"))

    assert result.stations_i is not None
    assert result.stations_i.md.tolist() == [float(md) for md in range(201)]


def test_md_in_metres_with_depths_in_feet() -> None:
    body = request(EXAMPLES / "09_mixed_units_md_meters_z_feet.json")
    metric = {**body, "unitZ": "m", "unitMD": "m"}

    mixed, metres = from_payload(body).stations, from_payload(metric).stations

    assert mixed.md == pytest.approx(metres.md)
    assert mixed.tvd == pytest.approx(metres.tvd / 0.3048)
    assert mixed.x == pytest.approx(metres.x)


@pytest.mark.parametrize(
    ("change", "error", "match"),
    [
        ({"MD_i": {"md_i": [10.0], "md_interval": 5}}, InvalidSurveyError, "both"),
        ({"MD_i": {"md_i": [99999.0]}}, InvalidSurveyError, "outside"),
        ({"unitXY": "ft"}, UnitError, "unitXY"),
        ({"method": "Tangential"}, ValueError, "Tangential"),
    ],
    ids=["both-md-i-forms", "md-i-outside", "foreign-xy-unit", "unknown-method"],
)
def test_a_request_the_adapter_cannot_honour_is_refused(
    change: dict[str, Any], error: type[Exception], match: str
) -> None:
    body = request(EXAMPLES / "02_deviated_well_build.json") | change

    with pytest.raises(error, match=match):
        from_payload(body)


def test_the_crs_own_xy_unit_is_accepted() -> None:
    body = request(EXAMPLES / "02_deviated_well_build.json") | {"unitXY": "m"}

    assert len(from_payload(body).stations) == len(body["inputStations"])


def test_an_inclination_only_request_ignores_any_azimuth() -> None:
    body = request(EXAMPLES / "02_deviated_well_build.json") | {"inputKind": "MD_Incl"}
    body["azimuthReference"] = "TN"

    stations = from_payload(body).stations

    assert stations.east == pytest.approx(0.0, abs=1e-9)
    assert np.all(stations.north >= 0)
