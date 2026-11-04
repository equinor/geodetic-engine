"""Converting GeoJSON with its segments densified, straight or as geodesics."""

from __future__ import annotations

from typing import Any

import numpy as np
import pytest
import shapely

from geodetic_engine.geodesy import Transformation, TransformationFailedError
from geodetic_engine.geojson import convert_geojson

# A licence block's edges run along parallels and meridians, straight in
# longitude and latitude, as RFC 7946 draws a segment.
BLOCK = [[2.0, 60.0], [3.0, 60.0], [3.0, 61.0], [2.0, 61.0], [2.0, 60.0]]


def _feature(geometry: dict[str, Any]) -> dict[str, Any]:
    return {"type": "Feature", "properties": {}, "geometry": geometry}


def _ring(result: Any) -> np.ndarray:
    return np.array(result.document["geometry"]["coordinates"][0])


def test_edges_along_parallels_stay_on_them() -> None:
    block = _feature({"type": "Polygon", "coordinates": [BLOCK]})
    dense = convert_geojson(block, "EPSG:32631", output="anycrs", densify=0.01)
    chords = convert_geojson(block, "EPSG:32631", output="anycrs")
    expected = Transformation("OGC:CRS84", "EPSG:32631").transform_geometry(
        shapely.Polygon(BLOCK), densify=0.01
    )
    along = shapely.Polygon(_ring(dense))
    assert along.hausdorff_distance(expected.geometry) < 1e-6
    # The corners alone leave the edges along the parallels 100 m off.
    assert shapely.Polygon(_ring(chords)).hausdorff_distance(along) > 100
    assert dense.total_count == dense.success_count == len(_ring(dense))


def test_geodesics_are_those_of_a_geometry_bit_for_bit() -> None:
    document = {
        "type": "AnyCrsFeatureCollection",
        "CoordinateReferenceSystemID": "EPSG:4230",
        "features": [
            {
                "type": "AnyCrsFeature",
                "properties": {},
                "geometry": {
                    "type": "AnyCrsLineString",
                    "coordinates": [[5.0, 62.0], [10.0, 66.0]],
                },
            }
        ],
    }
    result = convert_geojson(document, "EPSG:23032", densify=1000, segments="geodesic")
    written = np.array(result.document["features"][0]["geometry"]["coordinates"])
    expected = Transformation("EPSG:4230", "EPSG:23032").transform_geometry(
        "LINESTRING (5 62, 10 66)", densify=1000, segments="geodesic"
    )
    assert np.array_equal(written, shapely.get_coordinates(expected.geometry))


def test_places_written_latitude_first_are_densified_in_value_order() -> None:
    lat_first = {
        "type": "Feature",
        "properties": {},
        "geometry": None,
        "place": {"type": "Polygon", "coordinates": [[[y, x] for x, y in BLOCK]]},
        "coordRefSys": "http://www.opengis.net/def/crs/EPSG/0/4326",
        "conformsTo": ["http://www.opengis.net/spec/json-fg-1/1.0/conf/core"],
    }
    from_lat_first = convert_geojson(lat_first, "EPSG:32631", densify=0.01)
    plain = _feature({"type": "Polygon", "coordinates": [BLOCK]})
    from_plain = convert_geojson(plain, "EPSG:32631", densify=0.01)
    assert (
        from_lat_first.document["place"]["coordinates"]
        == from_plain.document["place"]["coordinates"]
    )


def test_a_failing_inserted_vertex_is_named_after_the_position_before() -> None:
    # Near the equator, 90 degrees from UTM zone 31's central meridian is
    # outside its projection; the ends of this line are not. Of the seven
    # vertices inserted, the second, at (87.5 5), is the first outside.
    line = _feature(
        {"type": "LineString", "coordinates": [[80.0, 10.0], [110.0, -10.0]]}
    )
    with pytest.raises(
        TransformationFailedError,
        match=r"coordinates\[0\] \(vertex 2 densify inserted after it\)",
    ):
        convert_geojson(line, "EPSG:32631", output="anycrs", densify=5.0)
    result = convert_geojson(
        line, "EPSG:32631", output="anycrs", densify=5.0, on_error="nan"
    )
    assert (result.success_count, result.total_count) == (5, 9)


def test_points_are_not_densified() -> None:
    points = _feature({"type": "MultiPoint", "coordinates": [[2.0, 60.0], [9.0, 61.0]]})
    result = convert_geojson(points, "EPSG:32631", output="anycrs", densify=0.01)
    assert len(result.document["geometry"]["coordinates"]) == 2


@pytest.mark.parametrize(
    ("options", "message"),
    [
        ({"segments": "geodesic"}, "densify"),
        ({"densify": 1.0, "segments": "rhumb"}, "segments"),
        ({"densify": 0}, "positive"),
        ({"densify": float("nan")}, "positive"),
    ],
)
def test_densify_options_are_checked(options: dict[str, Any], message: str) -> None:
    block = _feature({"type": "Polygon", "coordinates": [BLOCK]})
    with pytest.raises(ValueError, match=message):
        convert_geojson(block, "EPSG:32631", **options)
