"""OGC JSON-FG: reading places in their CRS, and writing documents that validate."""

from __future__ import annotations

import copy
import json
from itertools import pairwise
from pathlib import Path
from typing import Any

import jsonschema
import pytest
from referencing import Registry, Resource

from geodetic_engine.geodesy import CoordinateReferenceSystem, Transformation
from geodetic_engine.geodesy._crs_tools import compound
from geodetic_engine.geojson import (
    GeoJsonCrsError,
    UnsupportedGeoJsonError,
    convert_geojson,
)
from geodetic_engine.geojson._jsonfg import coord_ref_sys, crs_name
from geodetic_engine.geojson._model import JSONFG_CORE

SCHEMAS = Path(__file__).parent / "schemas"
URI = "http://www.opengis.net/def/crs/EPSG/0/{}"


def _registry() -> Registry:
    resources = []
    for path in SCHEMAS.glob("*.json"):
        schema = json.loads(path.read_text(encoding="utf-8"))
        resources.append((schema["$id"], Resource.from_contents(schema)))
    return Registry().with_resources(resources)


_REGISTRY = _registry()
_ROOT = json.loads((SCHEMAS / "jsonfg-root-object.json").read_text(encoding="utf-8"))


def _validate(document: dict[str, Any]) -> None:
    jsonschema.Draft202012Validator(_ROOT, registry=_REGISTRY).validate(document)


def _collection(*features: dict[str, Any], **members: Any) -> dict[str, Any]:
    return {"type": "FeatureCollection", "features": list(features), **members}


def _feature(geometry: dict[str, Any] | None = None, **members: Any) -> dict[str, Any]:
    return {"type": "Feature", "properties": {}, "geometry": geometry, **members}


def _point(*coordinates: float) -> dict[str, Any]:
    return {"type": "Point", "coordinates": list(coordinates)}


PLAIN = _collection(
    _feature(_point(9.0, 60.0), id=1),
    _feature(
        {"type": "Polygon", "coordinates": [[[9, 60], [10, 60], [10, 61], [9, 60]]]},
        id=2,
    ),
)


@pytest.mark.parametrize(
    ("text", "name"),
    [
        ("http://www.opengis.net/def/crs/EPSG/0/4258", "EPSG:4258"),
        ("https://www.opengis.net/def/crs/OGC/0/CRS84h", "OGC:CRS84h"),
        ("http://www.opengis.net/def/crs/OGC/1.3/CRS84", "OGC:CRS84"),
        ("urn:ogc:def:crs:EPSG::25832", "EPSG:25832"),
        ("[EPSG:4326]", "EPSG:4326"),
        ("epsg:4326", "EPSG:4326"),
        ("not a crs", None),
    ],
)
def test_crs_references_are_read_without_fetching(text: str, name: str | None) -> None:
    assert crs_name(text) == name


def test_plain_geojson_in_another_crs_is_written_as_json_fg() -> None:
    result = convert_geojson(PLAIN, "EPSG:32632", decimals=3)
    document = result.document
    _validate(document)
    assert document["conformsTo"] == [JSONFG_CORE]
    assert document["coordRefSys"] == URI.format(32632)
    feature = document["features"][0]
    assert feature["geometry"] is None
    expected = Transformation("OGC:CRS84", "EPSG:32632").transform((9.0, 60.0))
    assert feature["place"]["coordinates"] == pytest.approx(
        list(expected.coordinates[0]), abs=1e-3
    )
    assert list(feature) == ["type", "properties", "geometry", "place", "id"]


def test_json_fg_plus_keeps_a_wgs84_geometry_for_geojson_readers() -> None:
    result = convert_geojson(PLAIN, "EPSG:32632", output="jsonfg-plus")
    _validate(result.document)
    for converted, original in zip(
        result.document["features"], PLAIN["features"], strict=True
    ):
        assert converted["geometry"] == original["geometry"]
        assert converted["place"]["coordinates"] != original["geometry"]["coordinates"]


def test_json_fg_plus_computes_the_wgs84_geometry_from_another_crs() -> None:
    source = _collection(
        _feature(place=_point(500000.0, 6650000.0)), coordRefSys=URI.format(32632)
    )
    result = convert_geojson(source, "EPSG:32633", output="jsonfg-plus")
    _validate(result.document)
    lon, lat = result.document["features"][0]["geometry"]["coordinates"]
    assert lon == pytest.approx(9.0, abs=1e-9) and 59.9 < lat < 60.1


def test_json_fg_plus_from_anycrs_writes_a_geojson_fallback() -> None:
    point = {"type": "AnyCrsPoint", "coordinates": [3.0, 60.0, 10.0]}
    point["bbox"] = [0.0, 0.0, 0.0, 0.0, 0.0, 0.0]
    source = {
        "type": "AnyCrsFeatureCollection",
        "CoordinateReferenceSystemID": "EPSG:4326",
        "VerticalUnitID": "ft",
        "features": [{"type": "AnyCrsFeature", "properties": {}, "geometry": point}],
    }
    result = convert_geojson(source, "EPSG:4978", output="jsonfg-plus")
    _validate(result.document)
    feature = result.document["features"][0]
    fallback, place = feature["geometry"], feature["place"]
    assert fallback["type"] == place["type"] == "Point"
    # RFC 7946 heights are metres.
    assert fallback["coordinates"] == pytest.approx([3.0, 60.0, 3.048])
    assert fallback["bbox"] == fallback["coordinates"] * 2
    assert place["bbox"] == place["coordinates"] * 2


def test_the_fallback_is_rounded_wound_and_states_no_crs() -> None:
    ring = [[60.0, 2.0], [61.0, 3.0], [60.0, 3.0], [60.0, 2.0]]
    polygon = {
        "type": "Polygon",
        "coordinates": [ring],
        "coordRefSys": URI.format(4230),
    }
    source = _collection(_feature(place=polygon))
    result = convert_geojson(
        source,
        "EPSG:4230",
        output="jsonfg-plus",
        decimals=2,
        orient="ccw",
        fallback_operation="EPSG:1612",
    )
    fallback = result.document["features"][0]["geometry"]
    assert "coordRefSys" not in fallback
    positions = fallback["coordinates"][0]
    assert all(value == round(value, 2) for position in positions for value in position)
    area = sum(x0 * y1 - x1 * y0 for (x0, y0), (x1, y1) in pairwise(positions))
    assert area > 0
    again = convert_geojson(result.document, "EPSG:4230")
    assert again.document["features"][0]["geometry"] == fallback


def test_identical_scopes_are_converted_together() -> None:
    features = [
        _feature(place=_point(60.0 + n / 100, 3.0), coordRefSys=URI.format(4326))
        for n in range(20)
    ]
    result = convert_geojson(_collection(*features), "EPSG:32631")
    assert len(result.transformations) == 1


def test_json_fg_has_no_collection_in_a_collection() -> None:
    nested = {
        "type": "GeometryCollection",
        "geometries": [
            _point(3.0, 60.0),
            {"type": "GeometryCollection", "geometries": [_point(3.1, 60.1)]},
        ],
    }
    source = _collection(_feature(nested))
    with pytest.raises(ValueError, match=r"geometry\.geometries\[1\] is a"):
        convert_geojson(source, "EPSG:32631")
    assert convert_geojson(source, "EPSG:32631", output="anycrs").success_count == 2


def test_positions_at_different_epochs_are_refused() -> None:
    def at(epoch: float, latitude: float) -> dict[str, Any]:
        reference = {"type": "Reference", "href": URI.format(7912), "epoch": epoch}
        return _feature(place=_point(latitude, 3.0, 100.0), coordRefSys=reference)

    same = convert_geojson(_collection(at(2010.0, 60.0), at(2010.0, 61.0)), "EPSG:7912")
    _validate(same.document)
    assert same.document["coordRefSys"]["epoch"] == 2010.0
    with pytest.raises(ValueError, match=r"epochs 2010\.0, 2020\.0"):
        convert_geojson(_collection(at(2010.0, 60.0), at(2020.0, 61.0)), "EPSG:7912")


def test_positions_are_in_the_declared_axis_order_of_their_crs() -> None:
    # ETRS89 declares latitude first.
    source = _collection(
        _feature(place=_point(60.0, 9.0)), coordRefSys=URI.format(4258)
    )
    result = convert_geojson(source, "EPSG:25832")
    expected = Transformation("EPSG:4258", "EPSG:25832").transform((9.0, 60.0))
    place = result.document["features"][0]["place"]["coordinates"]
    assert place == list(expected.coordinates[0])
    back = convert_geojson(result.document, "EPSG:4258")
    _validate(back.document)
    assert back.document["coordRefSys"] == URI.format(4258)
    assert back.document["features"][0]["place"]["coordinates"] == pytest.approx(
        [60.0, 9.0], abs=1e-9
    )


def test_a_place_in_wgs84_is_written_as_the_geometry() -> None:
    source = _collection(
        _feature(place=_point(500000.0, 6650000.0)), coordRefSys=URI.format(32632)
    )
    result = convert_geojson(source, "EPSG:4326")
    _validate(result.document)
    feature = result.document["features"][0]
    assert "place" not in feature
    assert "coordRefSys" not in result.document
    assert feature["geometry"]["coordinates"][0] == pytest.approx(9.0)


def test_the_nearest_coord_ref_sys_applies() -> None:
    projected = Transformation("OGC:CRS84", "EPSG:32632").transform((9.0, 60.0))
    source = _collection(
        _feature(place=_point(*projected.coordinates[0])),
        _feature(place=_point(60.0, 9.0), coordRefSys=URI.format(4326)),
        coordRefSys=URI.format(32632),
    )
    result = convert_geojson(source, "EPSG:4326", output="geojson")
    first, second = (f["geometry"]["coordinates"] for f in result.document["features"])
    assert first == pytest.approx([9.0, 60.0], abs=1e-9)
    assert second == [9.0, 60.0]
    assert len(result.transformations) == 2


def test_a_feature_with_both_keeps_its_wgs84_geometry() -> None:
    source = _collection(
        _feature(_point(9.0, 60.0), place=_point(500000.0, 6650000.0)),
        coordRefSys=URI.format(25832),
    )
    original = copy.deepcopy(source)
    result = convert_geojson(source, "EPSG:25833")
    feature = result.document["features"][0]
    assert feature["geometry"] == original["features"][0]["geometry"]
    assert result.total_count == 1
    _validate(result.document)


def test_json_fg_converts_to_anycrs() -> None:
    source = _collection(
        _feature(place=_point(500000.0, 6650000.0)),
        coordRefSys=URI.format(25832),
        conformsTo=[JSONFG_CORE, "http://example.com/other"],
    )
    result = convert_geojson(source, "EPSG:25833", output="anycrs")
    document = result.document
    assert document["type"] == "AnyCrsFeatureCollection"
    assert document["conformsTo"] == ["http://example.com/other"]
    assert "coordRefSys" not in document
    feature = document["features"][0]
    assert "place" not in feature and feature["geometry"]["type"] == "AnyCrsPoint"
    assert "persistableReferenceCrs" in document


def test_the_epoch_of_a_coord_ref_sys_is_the_coordinate_epoch() -> None:
    reference = {"type": "Reference", "href": URI.format(4896), "epoch": 2010.0}
    source = {
        "type": "Point",
        "coordinates": [-2593197.524, 5656917.6189, -1394397.8828],
        "coordRefSys": reference,
        "conformsTo": [JSONFG_CORE],
    }
    result = convert_geojson(source, "EPSG:4938", operation="EPSG:6277")
    assert result.transformations[0]["coordinate_epoch"] == 2010.0
    with pytest.raises(GeoJsonCrsError, match="epoch"):
        convert_geojson(
            source, "EPSG:4938", operation="EPSG:6277", coordinate_epoch=2000.0
        )


def test_an_ad_hoc_compound_crs_converts_heights_between_datums() -> None:
    source = {
        "type": "Point",
        "coordinates": [60.0, 10.0, 40.0],
        "coordRefSys": [URI.format(4326), URI.format(3855)],
        "conformsTo": [JSONFG_CORE],
    }
    result = convert_geojson(source, "EPSG:4979", operation="EPSG:3858")
    expected = Transformation(
        "EPSG:9518", "EPSG:4979", operation="EPSG:3858"
    ).transform((10.0, 60.0, 40.0))
    assert result.document["coordinates"] == pytest.approx(
        list(expected.coordinates[0]), abs=1e-9
    )


def test_a_three_dimensional_target_keeps_heights_in_json_fg() -> None:
    source = _collection(_feature(_point(9.0, 60.0, 100.0)))
    with pytest.raises(ValueError, match="three-dimensional"):
        convert_geojson(source, "EPSG:32632")
    result = convert_geojson(source, "EPSG:4979", output="jsonfg")
    assert len(result.document["features"][0]["geometry"]["coordinates"]) == 3


def test_two_dimensional_places_need_a_two_dimensional_target() -> None:
    source = _collection(
        _feature(place=_point(60.0, 9.0)), coordRefSys=URI.format(4258)
    )
    with pytest.raises(ValueError, match="two-dimensional target"):
        convert_geojson(source, "EPSG:4937")


def test_source_crs_epsg_4326_keeps_latitude_first() -> None:
    unscoped = _collection(_feature(place=_point(60.0, 9.0)))
    stated = {**unscoped, "coordRefSys": URI.format(4326)}
    given = convert_geojson(unscoped, "EPSG:32632", source_crs="EPSG:4326")
    expected = convert_geojson(stated, "EPSG:32632")
    assert given.document["features"] == expected.document["features"]


def test_a_stated_unit_applies_to_a_geocentric_third_axis() -> None:
    source = {
        "type": "AnyCrsFeatureCollection",
        "CoordinateReferenceSystemID": "EPSG:4978",
        "VerticalUnitID": "ft",
        "features": [
            {
                "type": "AnyCrsFeature",
                "properties": {},
                "geometry": {
                    "type": "AnyCrsPoint",
                    "coordinates": [1000.0, 2000.0, 3000.0],
                },
            }
        ],
    }
    result = convert_geojson(source, "EPSG:4978", output="jsonfg")
    assert result.document["features"][0]["place"]["coordinates"] == pytest.approx(
        [1000.0, 2000.0, 914.4]
    )


def test_a_coord_ref_sys_by_value_is_not_read() -> None:
    source = _collection(
        _feature(place=_point(1.0, 2.0)), coordRefSys={"type": "PROJJSON", "value": {}}
    )
    with pytest.raises(UnsupportedGeoJsonError, match="PROJJSON"):
        convert_geojson(source, "EPSG:4326")


def test_a_compound_crs_without_a_code_is_written_as_its_parts() -> None:
    joined = compound(
        CoordinateReferenceSystem.from_user_input("EPSG:25832"),
        CoordinateReferenceSystem.from_user_input("EPSG:5941"),
    )
    assert coord_ref_sys(joined, None) == [URI.format(25832), URI.format(5941)]
    with_epoch = coord_ref_sys(
        CoordinateReferenceSystem.from_user_input("EPSG:7912"), 2020.5
    )
    assert with_epoch == {
        "type": "Reference",
        "href": URI.format(7912),
        "epoch": 2020.5,
    }
