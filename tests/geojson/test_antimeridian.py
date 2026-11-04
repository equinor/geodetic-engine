"""Converting GeoJSON across a geographic target's antimeridian, and round a pole."""

from __future__ import annotations

from typing import Any

import numpy as np
import pytest
import shapely
from pyproj import Transformer

from geodetic_engine.geodesy import Transformation
from geodetic_engine.geojson import convert_geojson, convert_geojson_request

JSONFG = "http://www.opengis.net/spec/json-fg-1/1.0/conf/core"
# 200 km by 50 km in UTM zone 60, whose central meridian is 177 E: from
# 178.8 E to 177.6 W.
STRIP = [
    [600000.0, 6650000.0],
    [800000.0, 6650000.0],
    [800000.0, 6700000.0],
    [600000.0, 6700000.0],
    [600000.0, 6650000.0],
]
LINE = [[600000.0, 6650000.0], [800000.0, 6680000.0]]


def _anycrs(*geometries: dict[str, Any], crs: str = "EPSG:32660") -> dict[str, Any]:
    return {
        "type": "AnyCrsFeatureCollection",
        "CoordinateReferenceSystemID": crs,
        "features": [
            {"type": "AnyCrsFeature", "properties": {}, "geometry": geometry}
            for geometry in geometries
        ],
    }


def _strip_and_line() -> dict[str, Any]:
    return _anycrs(
        {"type": "AnyCrsPolygon", "coordinates": [STRIP]},
        {"type": "AnyCrsLineString", "coordinates": LINE},
    )


def _geometries(result: Any) -> list[dict[str, Any]]:
    return [feature["geometry"] for feature in result.document["features"]]


def _corners_in_wgs84() -> np.ndarray:
    columns = (
        Transformation("EPSG:32660", "EPSG:4326")
        .transform_arrays(np.array(STRIP))
        .columns
    )
    return np.column_stack(columns)


def test_a_strip_across_the_antimeridian_is_cut_as_rfc_7946_has_it() -> None:
    result = convert_geojson(_strip_and_line(), "EPSG:4326", output="geojson")
    polygon, line = _geometries(result)
    assert (polygon["type"], line["type"]) == ("MultiPolygon", "MultiLineString")
    expected = Transformation("EPSG:32660", "EPSG:4326").transform_geometries(
        [shapely.Polygon(STRIP), shapely.LineString(LINE)]
    )
    for written, geometry in zip((polygon, line), expected.geometries, strict=True):
        assert shapely.geometry.shape(written).equals_exact(geometry, 0)
    longitudes = np.array(
        [p[0] for part in polygon["coordinates"] for p in part[0]]
        + [p[0] for part in line["coordinates"] for p in part]
    )
    assert np.abs(longitudes).max() == 180.0
    assert shapely.geometry.shape(polygon).is_valid
    # A cut gains positions on the antimeridian, which are not counted.
    assert result.success_count == result.total_count == 7


def test_positions_that_are_cut_round_are_kept_bit_for_bit() -> None:
    result = convert_geojson(_strip_and_line(), "EPSG:4326", output="geojson")
    polygon = shapely.geometry.shape(_geometries(result)[0])
    written = {tuple(position) for position in shapely.get_coordinates(polygon)}
    assert {tuple(corner) for corner in _corners_in_wgs84().tolist()} <= written


def test_anycrs_output_keeps_its_prefix_for_the_multipart_type() -> None:
    result = convert_geojson(_strip_and_line(), "EPSG:4326", output="anycrs")
    assert [g["type"] for g in _geometries(result)] == [
        "AnyCrsMultiPolygon",
        "AnyCrsMultiLineString",
    ]
    assert result.document["type"] == "AnyCrsFeatureCollection"


def test_unwrap_carries_the_longitudes_on_past_the_antimeridian() -> None:
    result = convert_geojson(
        _strip_and_line(), "EPSG:4326", output="geojson", antimeridian="unwrap"
    )
    polygon, line = _geometries(result)
    assert (polygon["type"], line["type"]) == ("Polygon", "LineString")
    ring = np.array(polygon["coordinates"][0])
    assert ring[:, 0].max() > 180
    assert ring[:, 0].max() - ring[:, 0].min() < 4


def test_none_keeps_the_positions_as_transformed() -> None:
    result = convert_geojson(
        _strip_and_line(), "EPSG:4326", output="geojson", antimeridian=None
    )
    polygon, line = _geometries(result)
    assert (polygon["type"], line["type"]) == ("Polygon", "LineString")
    np.testing.assert_array_equal(
        np.array(polygon["coordinates"][0]), _corners_in_wgs84()
    )


def test_the_service_request_writes_positions_as_transformed() -> None:
    response = convert_geojson_request(
        {"toCRS": "EPSG:4326", "featureCollection": _strip_and_line()}
    )
    features = response["featureCollection"]["features"]
    assert [f["geometry"]["type"] for f in features] == ["Polygon", "LineString"]


def test_a_segment_the_long_way_round_in_its_source_is_kept() -> None:
    # Straight in longitude and latitude, as RFC 7946 reads it, this segment
    # does go the long way round.
    line = {"type": "LineString", "coordinates": [[179.0, 60.0], [-179.0, 60.0]]}
    feature = {"type": "Feature", "properties": {}, "geometry": line}
    result = convert_geojson(feature, "EPSG:4326", output="geojson")
    assert result.document["geometry"] == line


def test_a_cap_round_the_pole_is_closed_through_it() -> None:
    angles = np.linspace(0, 2 * np.pi, 13)
    circle = np.column_stack([1e6 * np.cos(angles), 1e6 * np.sin(angles)])
    circle[-1] = circle[0]
    document = _anycrs(
        {"type": "AnyCrsPolygon", "coordinates": [circle.tolist()]}, crs="EPSG:3413"
    )
    for densify in (None, 50_000):
        result = convert_geojson(
            document, "EPSG:4326", output="geojson", densify=densify
        )
        cap = shapely.geometry.shape(_geometries(result)[0])
        assert cap.geom_type == "Polygon"
        assert cap.is_valid
        assert cap.covers(shapely.Point(0, 90))
        assert cap.bounds[0] == -180 and cap.bounds[2] == 180


def test_cut_positions_are_rounded_and_wound_as_asked() -> None:
    result = convert_geojson(
        _strip_and_line(), "EPSG:4326", output="geojson", decimals=3, orient="cw"
    )
    polygon = shapely.geometry.shape(_geometries(result)[0])
    values = shapely.get_coordinates(polygon).ravel()
    np.testing.assert_array_equal(values, np.round(values, 3))
    assert not any(shapely.is_ccw(part.exterior) for part in shapely.get_parts(polygon))


def test_heights_are_carried_to_the_positions_on_the_antimeridian() -> None:
    line = [[*LINE[0], 100.0], [*LINE[1], 300.0]]
    document = _anycrs({"type": "AnyCrsLineString", "coordinates": line})
    document["VerticalUnitID"] = "osdu:reference-data--UnitOfMeasure:m:"
    result = convert_geojson(document, "EPSG:4326", output="geojson")
    written = _geometries(result)[0]
    assert written["type"] == "MultiLineString"
    first, second = (np.array(part) for part in written["coordinates"])
    assert first.shape[1] == second.shape[1] == 3
    assert (first[0, 2], second[-1, 2]) == (100.0, 300.0)
    assert 100 < first[-1, 2] == second[0, 2] < 300


def test_a_member_of_a_geometry_collection_is_cut_on_its_own() -> None:
    collection = {
        "type": "AnyCrsGeometryCollection",
        "geometries": [
            {"type": "AnyCrsLineString", "coordinates": LINE},
            {"type": "AnyCrsPoint", "coordinates": LINE[0]},
        ],
    }
    result = convert_geojson(_anycrs(collection), "EPSG:4326", output="geojson")
    written = _geometries(result)[0]
    assert written["type"] == "GeometryCollection"
    assert [g["type"] for g in written["geometries"]] == ["MultiLineString", "Point"]


def _chatham_place() -> dict[str, Any]:
    """A place across the antimeridian in Chatham Islands TM 2000, northing first."""
    to_projected = Transformer.from_crs("EPSG:4167", "EPSG:3793", always_xy=True)
    corners = [(179.0, -44.5), (-179.0, -44.5), (-179.0, -44.0), (179.0, -44.0)]
    ring = [list(to_projected.transform(lon, lat))[::-1] for lon, lat in corners]
    return {
        "type": "FeatureCollection",
        "conformsTo": [JSONFG],
        "coordRefSys": "http://www.opengis.net/def/crs/EPSG/0/3793",
        "features": [
            {
                "type": "Feature",
                "properties": {},
                "geometry": None,
                "place": {"type": "Polygon", "coordinates": [[*ring, ring[0]]]},
            }
        ],
    }


def test_a_place_is_cut_by_longitude_and_written_in_its_crs_order() -> None:
    result = convert_geojson(_chatham_place(), "EPSG:4167", output="jsonfg", decimals=7)
    place = result.document["features"][0]["place"]
    assert place["type"] == "MultiPolygon"
    positions = {tuple(p) for part in place["coordinates"] for p in part[0]}
    # Latitude first, as NZGD2000 declares it, each corner kept.
    assert {(-44.5, 179.0), (-44.5, -179.0), (-44.0, 180.0)} <= positions
    lonlat = shapely.transform(shapely.geometry.shape(place), lambda xy: xy[:, ::-1])
    assert lonlat.is_valid
    assert lonlat.area == pytest.approx(1.0)


def test_the_wgs84_geometry_beside_a_place_is_cut_on_its_own() -> None:
    result = convert_geojson(
        _chatham_place(),
        "EPSG:3793",
        output="jsonfg-plus",
        fallback_operation="EPSG:1565",
    )
    feature = result.document["features"][0]
    # The place, in a projected CRS, has no antimeridian.
    assert feature["place"]["type"] == "Polygon"
    assert feature["geometry"]["type"] == "MultiPolygon"
    geometry = shapely.geometry.shape(feature["geometry"])
    assert geometry.is_valid
    assert geometry.area == pytest.approx(1.0, rel=1e-3)


def test_an_unknown_antimeridian_is_refused() -> None:
    with pytest.raises(ValueError, match="antimeridian must be one of"):
        convert_geojson(
            _strip_and_line(),
            "EPSG:4326",
            output="geojson",
            antimeridian="cut",  # type: ignore[arg-type]
        )
