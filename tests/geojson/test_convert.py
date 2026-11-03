"""Converting GeoJSON documents: positions, structure, heights and failures."""

from __future__ import annotations

import copy
import json
import math
from typing import Any

import numpy as np
import pytest
from pyproj import CRS

from geodetic_engine.geodesy import (
    AmbiguousOperationError,
    CoordinateOutOfRangeError,
    MissingCoordinateEpochError,
    Transformation,
    TransformationFailedError,
)
from geodetic_engine.geojson import (
    GeoJsonCrsError,
    GeoJsonResult,
    convert_geojson,
)
from geodetic_engine.persistablereference import to_persistable_reference

ED50_UTM31 = to_persistable_reference(CRS.from_epsg(23031))
SQUARE = [[2.0, 60.0], [3.0, 60.0], [3.0, 61.0], [2.0, 61.0], [2.0, 60.0]]
HOLE = [[2.2, 60.2], [2.2, 60.4], [2.4, 60.4], [2.2, 60.2]]
GEOMETRIES: dict[str, Any] = {
    "Point": [2.5, 60.5],
    "MultiPoint": [[2.5, 60.5], [3.5, 61.5]],
    "LineString": [[2.0, 60.0], [3.0, 61.0], [4.0, 62.0]],
    "MultiLineString": [[[2.0, 60.0], [3.0, 61.0]], [[4.0, 62.0], [5.0, 63.0]]],
    "Polygon": [SQUARE, HOLE],
    "MultiPolygon": [[SQUARE], [[[4.0, 62.0], [5.0, 62.0], [5.0, 63.0], [4.0, 62.0]]]],
}


def _feature(geometry: dict[str, Any] | None, **members: Any) -> dict[str, Any]:
    return {
        "type": "Feature",
        "properties": {"name": "x"},
        "geometry": geometry,
        **members,
    }


def _collection(*features: dict[str, Any], **members: Any) -> dict[str, Any]:
    return {"type": "FeatureCollection", "features": list(features), **members}


def _anycrs(value: Any) -> Any:
    if isinstance(value, dict):
        renamed = {key: _anycrs(item) for key, item in value.items()}
        if isinstance(value.get("type"), str):
            renamed["type"] = "AnyCrs" + value["type"]
        return renamed
    if isinstance(value, list):
        return [_anycrs(item) for item in value]
    return value


def _positions(value: Any) -> list[list[float]]:
    """Every position in a coordinates array, in document order."""
    if value and isinstance(value[0], int | float):
        return [list(value)]
    return [position for item in value for position in _positions(item)]


def _every_type() -> dict[str, Any]:
    geometries = [{"type": kind, "coordinates": c} for kind, c in GEOMETRIES.items()]
    collection = {
        "type": "GeometryCollection",
        "geometries": copy.deepcopy(geometries[:2]),
    }
    return _collection(
        *(_feature(g, id=i) for i, g in enumerate([*geometries, collection]))
    )


def test_every_position_is_what_transform_gives() -> None:
    source = _every_type()
    result = convert_geojson(source, "EPSG:32631", output="anycrs")
    flat = [p for kind, c in GEOMETRIES.items() for p in _positions(c)]
    flat += _positions(GEOMETRIES["Point"]) + _positions(GEOMETRIES["MultiPoint"])
    expected = (
        Transformation("OGC:CRS84", "EPSG:32631").transform_arrays(flat).to_numpy()
    )
    written = [
        p
        for feature in result.document["features"]
        for p in _positions(_coordinates(feature["geometry"]))
    ]
    assert np.array_equal(np.array(written), expected)
    assert result.success_count == result.total_count == len(flat)
    assert isinstance(result, GeoJsonResult)


def _coordinates(geometry: dict[str, Any]) -> Any:
    if "geometries" in geometry:
        return [c for g in geometry["geometries"] for c in _positions(g["coordinates"])]
    return geometry["coordinates"]


def test_structure_members_and_key_order_are_kept() -> None:
    source = _collection(
        _feature(
            {"type": "Point", "coordinates": [2.5, 60.5]},
            id="a",
            time={"date": "2020-01-01"},
        ),
        links=[{"href": "http://example.com"}],
    )
    original = copy.deepcopy(source)
    result = convert_geojson(source, "EPSG:4326")
    assert source == original
    feature = result.document["features"][0]
    assert list(feature) == ["type", "properties", "geometry", "id", "time"]
    assert feature["properties"] == {"name": "x"}
    assert result.document["links"] == [{"href": "http://example.com"}]


def test_ed50_utm_to_wgs84_and_back() -> None:
    source = _anycrs(
        _collection(_feature({"type": "Point", "coordinates": [500000.0, 6650000.0]}))
    )
    source["persistableReferenceCrs"] = ED50_UTM31
    there = convert_geojson(
        source, "EPSG:4326", operation="EPSG:1612", output="geojson"
    )
    assert there.document["type"] == "FeatureCollection"
    assert "persistableReferenceCrs" not in there.document
    back = convert_geojson(
        there.document, ED50_UTM31, operation="EPSG:1612", output="anycrs"
    )
    position = back.document["features"][0]["geometry"]["coordinates"]
    assert position == pytest.approx([500000.0, 6650000.0], abs=1e-3)
    assert back.document["persistableReferenceCrs"] == ED50_UTM31


def test_a_datum_change_needs_an_operation() -> None:
    source = _anycrs(
        _collection(_feature({"type": "Point", "coordinates": [500000.0, 6650000.0]}))
    )
    source["persistableReferenceCrs"] = ED50_UTM31
    with pytest.raises(AmbiguousOperationError):
        convert_geojson(source, "EPSG:4326")


def test_an_early_bound_crs_brings_its_operation() -> None:
    bound = to_persistable_reference(
        CRS.from_user_input(
            "+proj=utm +zone=31 +ellps=intl +towgs84=-87,-98,-121 +units=m +no_defs +type=crs"
        )
    )
    source = _anycrs(
        _collection(_feature({"type": "Point", "coordinates": [500000.0, 6650000.0]}))
    )
    source["persistableReferenceCrs"] = bound
    result = convert_geojson(source, "EPSG:4326")
    assert result.document["type"] == "AnyCrsFeatureCollection"
    lon, lat = result.document["features"][0]["geometry"]["coordinates"]
    assert 2.9 < lon < 3.1 and 59.9 < lat < 60.1


def test_heights_are_converted_to_the_unit_asked_for() -> None:
    source = _anycrs(
        _collection(
            _feature({"type": "Point", "coordinates": [500000.0, 6650000.0, 10.0]})
        )
    )
    source["persistableReferenceCrs"] = ED50_UTM31
    source["VerticalUnitID"] = "osdu:reference-data--UnitOfMeasure:m:"
    result = convert_geojson(
        source, "EPSG:32631", operation="EPSG:1612", target_unit_z="ft"
    )
    height = result.document["features"][0]["geometry"]["coordinates"][2]
    assert height == pytest.approx(10.0 / 0.3048)
    assert "VerticalUnitID" not in result.document
    assert json.loads(result.document["persistableReferenceUnitZ"])["symbol"] == "ft"
    assert "heights converted from m to ft" in result.operations_applied
    back = convert_geojson(result.document, "EPSG:32631", target_unit_z="m")
    assert back.document["features"][0]["geometry"]["coordinates"][2] == pytest.approx(
        10.0
    )


def test_heights_keep_their_unit_when_none_is_asked_for() -> None:
    source = _anycrs(
        _collection(
            _feature({"type": "Point", "coordinates": [500000.0, 6650000.0, 10.0]})
        )
    )
    source["persistableReferenceCrs"] = ED50_UTM31
    source["VerticalUnitID"] = "ft"
    result = convert_geojson(source, "EPSG:32631", operation="EPSG:1612")
    assert result.document["features"][0]["geometry"]["coordinates"][2] == 10.0
    assert json.loads(result.document["persistableReferenceUnitZ"])["symbol"] == "ft"


def test_plain_geojson_heights_are_metres() -> None:
    source = _collection(
        _feature({"type": "Point", "coordinates": [500000.0, 6650000.0, 10.0]})
    )
    with pytest.raises(ValueError, match="metres"):
        convert_geojson(
            source, "EPSG:4326", source_crs="EPSG:32631", target_unit_z="ft"
        )


def test_a_vertical_crs_converts_heights_between_datums() -> None:
    source = _anycrs(
        _collection(_feature({"type": "Point", "coordinates": [10.0, 60.0, 40.0]}))
    )
    source["CoordinateReferenceSystemID"] = "EPSG:4326"
    source["VerticalCoordinateReferenceSystemID"] = "EPSG:3855"
    result = convert_geojson(source, "EPSG:4979", operation="EPSG:3858")
    expected = Transformation(
        "EPSG:9518", "EPSG:4979", operation="EPSG:3858"
    ).transform((10.0, 60.0, 40.0))
    assert result.document["features"][0]["geometry"]["coordinates"] == pytest.approx(
        list(expected.coordinates[0]), abs=1e-9
    )
    assert "VerticalCoordinateReferenceSystemID" not in result.document


def test_bounding_boxes_are_computed_again_where_they_were() -> None:
    source = _collection(
        _feature(
            {"type": "Polygon", "coordinates": [SQUARE], "bbox": [0, 0, 0, 0]},
            bbox=[0, 0, 0, 0],
        ),
        _feature({"type": "Point", "coordinates": [4.0, 62.0]}),
        bbox=[0, 0, 0, 0],
    )
    result = convert_geojson(source, "EPSG:32631", output="anycrs", decimals=3)
    document = result.document
    eastings = [
        p[0] for p in _positions(document["features"][0]["geometry"]["coordinates"])
    ]
    assert document["features"][0]["bbox"][0] == min(eastings)
    assert (
        document["features"][0]["geometry"]["bbox"] == document["features"][0]["bbox"]
    )
    assert document["bbox"][2] == max(
        [*eastings, document["features"][1]["geometry"]["coordinates"][0]]
    )
    assert "bbox" not in document["features"][1]


def test_decimals_round_half_up_after_converting() -> None:
    result = convert_geojson(
        _collection(_feature({"type": "Point", "coordinates": [2.5, 60.5]})),
        "EPSG:4326",
        decimals=(3, 2),
    )
    assert result.document["features"][0]["geometry"]["coordinates"] == [2.5, 60.5]
    utm = convert_geojson(
        _collection(_feature({"type": "Point", "coordinates": [2.5, 60.5]})),
        "EPSG:32631",
        output="anycrs",
        decimals=0,
    )
    assert all(
        float(v).is_integer()
        for v in utm.document["features"][0]["geometry"]["coordinates"]
    )


def test_rings_are_wound_as_asked() -> None:
    clockwise = [list(p) for p in reversed(SQUARE)]
    source = _collection(_feature({"type": "Polygon", "coordinates": [clockwise]}))
    kept = convert_geojson(source, "EPSG:32631", output="anycrs")
    ring = np.array(kept.document["features"][0]["geometry"]["coordinates"][0])
    assert _area(ring) < 0
    wound = convert_geojson(source, "EPSG:32631", output="anycrs", orient="ccw")
    assert (
        _area(np.array(wound.document["features"][0]["geometry"]["coordinates"][0])) > 0
    )


def _area(ring: np.ndarray) -> float:
    return float(np.dot(ring[:-1, 0], ring[1:, 1]) - np.dot(ring[1:, 0], ring[:-1, 1]))


def _failing() -> dict[str, Any]:
    return _collection(
        _feature({"type": "Point", "coordinates": [3.0, 60.0]}),
        _feature({"type": "LineString", "coordinates": [[3.0, 60.0], [93.0, 0.0]]}),
        _feature({"type": "Point", "coordinates": [3.0, 61.0]}, bbox=[0, 0, 0, 0]),
    )


def test_a_failing_position_is_named_by_its_path() -> None:
    with pytest.raises(
        TransformationFailedError,
        match=r"\$\.features\[1\]\.geometry\.coordinates\[1\]",
    ):
        convert_geojson(_failing(), "EPSG:32631", output="anycrs")


def test_withdrawing_writes_a_failed_feature_without_geometry() -> None:
    result = convert_geojson(
        _failing(), "EPSG:32631", output="anycrs", on_error="withdraw"
    )
    features = result.document["features"]
    assert features[1]["geometry"] is None
    assert features[0]["geometry"] is not None and features[2]["bbox"][0] > 0
    assert (result.success_count, result.total_count) == (2, 4)
    [(path, error)] = result.failures
    assert path == "$.features[1].geometry.coordinates[1]"
    assert isinstance(error, TransformationFailedError)


def test_nan_writes_a_failed_position_as_the_service_does() -> None:
    result = convert_geojson(_failing(), "EPSG:32631", output="anycrs", on_error="nan")
    failed = result.document["features"][1]["geometry"]["coordinates"][1]
    assert all(math.isnan(value) for value in failed)
    assert (result.success_count, result.total_count) == (3, 4)
    assert "NaN" in json.dumps(result.document)
    assert "1 failed" in result.operations_applied[0]


def test_a_geometry_outside_a_feature_cannot_be_withdrawn() -> None:
    with pytest.raises(CoordinateOutOfRangeError, match=r"\$\.coordinates"):
        convert_geojson(
            {"type": "Point", "coordinates": [3.0, 95.0]},
            "EPSG:32631",
            on_error="withdraw",
        )


def test_an_epoch_dependent_crs_takes_the_epoch() -> None:
    source = {
        "type": "Point",
        "coordinates": [-2593197.524, 5656917.6189, -1394397.8828],
    }
    with pytest.raises(MissingCoordinateEpochError):
        convert_geojson(
            source,
            "EPSG:4938",
            source_crs="EPSG:4896",
            operation="EPSG:6277",
        )
    result = convert_geojson(
        source,
        "EPSG:4938",
        source_crs="EPSG:4896",
        operation="EPSG:6277",
        coordinate_epoch=2010.0,
    )
    assert result.transformations[0]["coordinate_epoch"] == 2010.0
    assert (
        result.document["coordRefSys"]
        == {
            "type": "Reference",
            "href": "http://www.opengis.net/def/crs/EPSG/0/4938",
            "epoch": 2010.0,
        }
        or result.document["coordRefSys"]
        == "http://www.opengis.net/def/crs/EPSG/0/4938"
    )


def test_options_are_checked() -> None:
    point = {"type": "Point", "coordinates": [3.0, 60.0]}
    with pytest.raises(ValueError, match="on_error"):
        convert_geojson(point, "EPSG:4326", on_error="skip")  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="orient"):
        convert_geojson(point, "EPSG:4326", orient="left")  # type: ignore[arg-type]


def test_source_crs_cannot_contradict_the_document() -> None:
    source = _anycrs(
        _collection(_feature({"type": "Point", "coordinates": [500000.0, 6650000.0]}))
    )
    source["persistableReferenceCrs"] = ED50_UTM31
    with pytest.raises(GeoJsonCrsError):
        convert_geojson(
            source, "EPSG:4326", source_crs="EPSG:32631", operation="EPSG:1612"
        )


def test_a_legacy_crs_member_is_read_and_dropped() -> None:
    member = {"type": "name", "properties": {"name": "urn:ogc:def:crs:EPSG::32631"}}
    source = _collection(
        _feature({"type": "Point", "coordinates": [500000.0, 6650000.0]}), crs=member
    )
    result = convert_geojson(source, "EPSG:4326")
    assert "crs" not in result.document
    lon, lat = result.document["features"][0]["geometry"]["coordinates"]
    assert lon == pytest.approx(3.0) and 59.9 < lat < 60.1
