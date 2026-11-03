"""The OSDU convertGeoJson request and response, against the service's examples."""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

import pytest

from geodetic_engine.geodesy import AmbiguousOperationError, CoordinateReferenceSystem
from geodetic_engine.geojson import convert_geojson_request
from geodetic_engine.geojson._scope import is_wgs84_lonlat

DATA = Path(__file__).parent / "data"
EXAMPLES = sorted((DATA / "osdu").glob("*.json"))
# A datum change without a named operation, which the service settles by its
# own choice and this package refuses to.
NEEDS_AN_OPERATION = {"05_point_2D_SAD69_to_WGS84", "14_geometry_collection"}


def _load(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _positions(value: Any) -> list[list[float]]:
    found: list[list[float]] = []
    if isinstance(value, dict):
        for key, item in value.items():
            if key == "coordinates":
                found += _flatten(item)
            elif key != "bbox":
                found += _positions(item)
    elif isinstance(value, list):
        for item in value:
            found += _positions(item)
    return found


def _flatten(value: Any) -> list[list[float]]:
    if value and isinstance(value[0], int | float):
        return [list(value)]
    return [position for item in value for position in _flatten(item)]


@pytest.mark.parametrize("path", EXAMPLES, ids=[path.stem for path in EXAMPLES])
def test_every_service_example_is_answered(path: Path) -> None:
    request = _load(path)["value"]
    if path.stem in NEEDS_AN_OPERATION:
        with pytest.raises(AmbiguousOperationError):
            convert_geojson_request(request)
        return
    response = convert_geojson_request(request)
    assert set(response) == {
        "featureCollection",
        "successCount",
        "totalCount",
        "operationsApplied",
    }
    collection = response["featureCollection"]
    positions = _positions(collection)
    assert response["successCount"] == response["totalCount"] == len(positions)
    assert all(math.isfinite(v) for position in positions for v in position)
    plain = is_wgs84_lonlat(CoordinateReferenceSystem.from_user_input(request["toCRS"]))
    if plain:
        assert collection["type"] == "FeatureCollection"
        assert "persistableReferenceCrs" not in collection
    else:
        assert collection["type"] == "AnyCrsFeatureCollection"
        assert collection["persistableReferenceCrs"]
    json.dumps(response, allow_nan=False)


def test_heights_in_plain_geojson_are_metres_whatever_to_unit_z_says() -> None:
    request = _load(DATA / "osdu" / "06_point_3D_with_unit_conversion.json")["value"]
    response = convert_geojson_request(request)
    assert response["featureCollection"]["type"] == "FeatureCollection"
    assert not any(
        "heights converted" in line for line in response["operationsApplied"]
    )


def test_bounding_boxes_are_recalculated() -> None:
    request = _load(DATA / "osdu" / "19_bbox_preservation.json")["value"]
    collection = convert_geojson_request(request)["featureCollection"]
    feature = collection["features"][0]
    lons = [p[0] for p in _positions(feature)]
    assert feature["bbox"][0] == min(lons) and feature["bbox"][2] == max(lons)
    assert feature["geometry"]["bbox"] == feature["bbox"]
    assert collection["bbox"] == feature["bbox"]


def test_the_recorded_service_response_is_matched() -> None:
    case = _load(DATA / "osdu_recorded_response.json")
    response = convert_geojson_request(case["input"])
    expected = case["expected"]
    assert response["successCount"] == expected["successCount"]
    assert response["totalCount"] == expected["totalCount"]
    collection = response["featureCollection"]
    assert collection["type"] == expected["featureCollection"]["type"]
    assert collection["persistableReferenceCrs"] == case["input"]["toCRS"]
    assert "persistableReferenceUnitZ" in collection
    got = _positions(collection)
    wanted = _positions(expected["featureCollection"])
    assert len(got) == len(wanted)
    for position, reference in zip(got, wanted, strict=True):
        # Trinidad links, of 0.2 m each.
        assert position == pytest.approx(reference, abs=1e-5)


def test_a_position_that_fails_is_nan_and_counted_out() -> None:
    request = {
        "toCRS": "EPSG:32631",
        "featureCollection": {
            "type": "FeatureCollection",
            "features": [
                {
                    "type": "Feature",
                    "properties": {},
                    "geometry": {"type": "Point", "coordinates": [3.0, 60.0]},
                },
                {
                    "type": "Feature",
                    "properties": {},
                    "geometry": {"type": "Point", "coordinates": [93.0, 0.0]},
                },
            ],
        },
    }
    response = convert_geojson_request(request)
    assert (response["successCount"], response["totalCount"]) == (1, 2)
    failed = response["featureCollection"]["features"][1]["geometry"]["coordinates"]
    assert all(math.isnan(value) for value in failed)


@pytest.mark.parametrize(
    "request_body",
    [
        {
            "toCRS": "EPSG:4326",
            "featureCollection": {
                "type": "Feature",
                "properties": {},
                "geometry": None,
            },
        },
        {"featureCollection": {"type": "FeatureCollection", "features": []}},
    ],
)
def test_a_request_is_checked(request_body: dict[str, Any]) -> None:
    with pytest.raises(ValueError):
        convert_geojson_request(request_body)
    with pytest.raises(TypeError):
        convert_geojson_request([])  # type: ignore[arg-type]
