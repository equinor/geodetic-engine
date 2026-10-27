"""Reading a GeoJSON document into its geometries, and writing positions back."""

from __future__ import annotations

import copy
import json
import pickle
from decimal import Decimal
from typing import Any

import numpy as np
import pytest

from geodetic_engine.errors import GeodeticEngineError
from geodetic_engine.geojson import (
    GeoJsonError,
    InvalidGeoJsonError,
    UnsupportedGeoJsonError,
)
from geodetic_engine.geojson._model import Flavour, Kind, read_document

SQUARE = [[0, 0], [1, 0], [1, 1], [0, 1], [0, 0]]
HOLE = [[0.2, 0.2], [0.2, 0.4], [0.4, 0.4], [0.2, 0.2]]
GEOMETRIES: dict[str, Any] = {
    "Point": [1.5, 2.5],
    "MultiPoint": [[1, 2], [3, 4]],
    "LineString": [[1, 2], [3, 4], [5, 6]],
    "MultiLineString": [[[1, 2], [3, 4]], [[5, 6], [7, 8], [9, 10]]],
    "Polygon": [SQUARE, HOLE],
    "MultiPolygon": [[SQUARE], [HOLE]],
}


def _collection(*features: dict[str, Any], **members: Any) -> dict[str, Any]:
    return {"type": "FeatureCollection", "features": list(features), **members}


def _feature(geometry: dict[str, Any] | None, **members: Any) -> dict[str, Any]:
    return {"type": "Feature", "properties": {}, "geometry": geometry, **members}


def _anycrs(value: Any) -> Any:
    """``value`` with every type renamed to its AnyCrs twin."""
    if isinstance(value, dict):
        renamed = {key: _anycrs(item) for key, item in value.items()}
        if isinstance(value.get("type"), str):
            renamed["type"] = "AnyCrs" + value["type"]
        return renamed
    if isinstance(value, list):
        return [_anycrs(item) for item in value]
    return value


def _rich_document() -> dict[str, Any]:
    return _collection(
        *(
            _feature({"type": kind, "coordinates": coordinates}, id=index)
            for index, (kind, coordinates) in enumerate(GEOMETRIES.items())
        ),
        _feature(
            {
                "type": "GeometryCollection",
                "geometries": [
                    {"type": "Point", "coordinates": [7, 8, 9]},
                    {
                        "type": "GeometryCollection",
                        "geometries": [{"type": "LineString", "coordinates": []}],
                    },
                ],
                "bbox": [7, 8, 9, 7, 8, 9],
            },
            bbox=[7, 8, 9, 7, 8, 9],
        ),
        _feature(None, id="unlocated"),
        bbox=[0, 0, 10, 10],
    )


def _key_orders(value: Any) -> Any:
    """Every mapping's keys, in order, nested as in ``value``."""
    if isinstance(value, dict):
        return [(key, _key_orders(item)) for key, item in value.items()]
    if isinstance(value, list):
        return [_key_orders(item) for item in value]
    return None


@pytest.mark.parametrize("flavour", list(Flavour))
def test_reading_keeps_the_document_and_its_order(flavour: Flavour) -> None:
    source = _rich_document()
    if flavour is Flavour.ANYCRS:
        source = _anycrs(source)
    document = read_document(source)

    assert document.flavour is flavour
    assert document.kind is Kind.FEATURE_COLLECTION
    assert document.root == source
    assert _key_orders(document.root) == _key_orders(source)
    json.dumps(document.root, allow_nan=False)


def test_geometries_are_collected_in_document_order() -> None:
    document = read_document(_rich_document())
    kinds = [geometry.kind for geometry in document.geometries]
    assert kinds == [Kind(kind) for kind in GEOMETRIES] + [Kind.POINT, Kind.LINE_STRING]
    assert [geometry.count for geometry in document.geometries] == [
        1,
        2,
        3,
        5,
        9,
        9,
        1,
        0,
    ]
    assert [geometry.dimension for geometry in document.geometries] == [
        2, 2, 2, 2, 2, 2, 3, 0,
    ]  # fmt: skip
    assert document.count == 30
    assert document.dimensions == frozenset({2, 3})
    polygon = document.geometries[4]
    assert polygon.positions.dtype == np.float64
    assert polygon.positions.tolist() == [*map(list, SQUARE), *HOLE]


@pytest.mark.parametrize(
    ("index", "position", "path"),
    [
        (0, 0, "$.features[0].geometry.coordinates"),
        (3, 4, "$.features[3].geometry.coordinates[1][2]"),
        (4, 7, "$.features[4].geometry.coordinates[1][2]"),
        (5, 6, "$.features[5].geometry.coordinates[1][0][1]"),
        (6, 0, "$.features[6].geometry.geometries[0].coordinates"),
    ],
)
def test_each_position_has_its_json_path(index: int, position: int, path: str) -> None:
    document = read_document(_rich_document())
    assert document.geometries[index].position_path(position) == path


def test_bounding_boxes_cover_their_geometries_innermost_first() -> None:
    document = read_document(_rich_document())
    found = [(box.path, box.geometries) for box in document.bounding_boxes]
    assert found == [
        ("$.features[6].geometry", range(6, 8)),
        ("$.features[6]", range(6, 8)),
        ("$", range(0, 8)),
    ]
    assert document.bounding_boxes[-1].target is document.root


def test_writing_positions_rebuilds_the_nesting() -> None:
    document = read_document(_rich_document())
    for geometry in document.geometries:
        rows = (geometry.positions + 1000).tolist()
        geometry.write(rows)
    polygon = document.root["features"][4]["geometry"]["coordinates"]
    assert polygon[1][2] == [1000.4, 1000.4]
    multipolygon = document.root["features"][5]["geometry"]["coordinates"]
    assert multipolygon[1][0][3] == [1000.2, 1000.2]
    assert document.root["features"][0]["geometry"]["coordinates"] == [1001.5, 1002.5]


def test_failed_positions_can_be_written_as_null() -> None:
    document = read_document(_rich_document())
    line = document.geometries[2]
    line.write([[0.0, 0.0], None, [2.0, 2.0]])
    assert line.target["coordinates"] == [[0.0, 0.0], None, [2.0, 2.0]]
    with pytest.raises(ValueError, match="3 positions"):
        line.write([[0.0, 0.0]])


def test_retyping_renames_every_type() -> None:
    document = read_document(_rich_document())
    document.retype(Flavour.ANYCRS)
    assert document.root == _anycrs(_rich_document())
    document.retype(Flavour.GEOJSON)
    assert document.root == _rich_document()


def test_the_input_is_never_modified_and_not_shared() -> None:
    source = _collection(
        _feature(
            {"type": "Point", "coordinates": [1, 2]},
            properties={"nested": {"values": [1, 2]}},
        )
    )
    original = copy.deepcopy(source)
    document = read_document(source)
    document.geometries[0].write([[5.0, 6.0]])
    document.root["features"][0]["properties"]["nested"]["values"].append(3)
    document.retype(Flavour.ANYCRS)
    assert source == original


@pytest.mark.parametrize(
    "given",
    [
        json.dumps(_rich_document()),
        json.dumps(_rich_document()).encode(),
        type("Shape", (), {"__geo_interface__": _rich_document()})(),
    ],
    ids=["text", "bytes", "geo-interface"],
)
def test_documents_are_read_from_text_and_geo_interfaces(given: object) -> None:
    assert read_document(given).root == _rich_document()


@pytest.mark.parametrize(
    ("root", "kind"),
    [
        (_feature({"type": "Point", "coordinates": [1, 2]}), Kind.FEATURE),
        (
            {"type": "AnyCrsLineString", "coordinates": [[1, 2], [3, 4]]},
            Kind.LINE_STRING,
        ),
        ({"type": "GeometryCollection", "geometries": []}, Kind.GEOMETRY_COLLECTION),
    ],
)
def test_a_feature_or_a_geometry_can_be_the_root(
    root: dict[str, Any], kind: Kind
) -> None:
    document = read_document(root)
    assert document.kind is kind
    assert document.root == root


def test_numbers_of_other_types_are_read_as_floats() -> None:
    document = read_document(
        {
            "type": "Point",
            "coordinates": (np.float32(1.5), np.int64(2), Decimal("3.25")),
        }
    )
    assert document.root["coordinates"] == [1.5, 2.0, 3.25]
    assert all(type(value) is float for value in document.root["coordinates"])


def test_empty_geometries_and_collections_are_kept() -> None:
    source = _collection(
        _feature({"type": "Polygon", "coordinates": []}),
        _feature({"type": "Point", "coordinates": []}),
    )
    document = read_document(source)
    assert document.root == source
    assert [geometry.count for geometry in document.geometries] == [0, 0]
    assert document.dimensions == frozenset()
    assert read_document(_collection()).geometries == ()


def _point(coordinates: Any) -> dict[str, Any]:
    return _collection(_feature({"type": "Point", "coordinates": coordinates}))


@pytest.mark.parametrize(
    ("document", "path", "reason"),
    [
        ("[]", "$", "must be a JSON object"),
        ("{", "$", "not JSON"),
        ({"features": []}, "$", "has no type"),
        ({"type": 4}, "$.type", "must be a string"),
        ({"type": "Pointy"}, "$.type", "not a GeoJSON type"),
        ({"type": "FeatureCollection"}, "$", "has no features"),
        ({"type": "FeatureCollection", "features": {}}, "$.features", "must be an array"),
        ({"type": "FeatureCollection", "features": [[]]}, "$.features[0]", "must be a JSON object"),
        (_collection({"type": "Point", "coordinates": [1, 2]}), "$.features[0].type", "a feature cannot"),
        (_collection(_feature(_feature(None))), "$.features[0].geometry.type", "a geometry cannot"),
        (_collection(_feature({"type": "Point"})), "$.features[0].geometry", "has no coordinates"),
        ({"type": "GeometryCollection"}, "$", "has no geometries"),
        (_point("1, 2"), "$.features[0].geometry.coordinates", "must be an array"),
        (_point([1]), "$.features[0].geometry.coordinates", "at least two numbers"),
        (_point([1, True]), "$.features[0].geometry.coordinates[1]", "is not a number"),
        (_point([1, "2"]), "$.features[0].geometry.coordinates[1]", "is not a number"),
        (_point([1, None]), "$.features[0].geometry.coordinates[1]", "is not a number"),
        (_point([1, float("nan")]), "$.features[0].geometry.coordinates[1]", "not a finite"),
        (_point([1, 10**400]), "$.features[0].geometry.coordinates[1]", "64-bit float"),
        ({"type": "LineString", "coordinates": [[1, 2]]}, "$.coordinates", "at least two positions"),
        ({"type": "MultiLineString", "coordinates": [[]]}, "$.coordinates[0]", "at least two positions"),
        ({"type": "Polygon", "coordinates": [[[0, 0], [1, 0], [0, 0]]]}, "$.coordinates[0]", "at least four"),
        ({"type": "Polygon", "coordinates": [[[0, 0], [1, 0], [1, 1], [0, 1]]]}, "$.coordinates[0]", "ends at the position"),
        ({"type": "Polygon", "coordinates": [[[0, 0, 1], [1, 0, 1], [1, 1, 1], [0, 0, 2]]]}, "$.coordinates[0]", "ends at the position"),
        ({"type": "MultiPolygon", "coordinates": [[]]}, "$.coordinates[0]", "at least one linear ring"),
        ({"type": "MultiPoint", "coordinates": [[1, 2], [1, 2, 3]]}, "$.coordinates[1]", "has 3 values"),
        ({"type": "Polygon", "coordinates": [SQUARE, 7]}, "$.coordinates[1]", "must be an array"),
        ({"type": "Polygon", "coordinates": [SQUARE, [7]]}, "$.coordinates[1][0]", "array of numbers"),
        ({"type": "Point", "coordinates": [1, 2], "bbox": [1, 2, 3]}, "$.bbox", "2n numbers"),
        ({"type": "Point", "coordinates": [1, 2], "bbox": [1, 2]}, "$.bbox", "at least four"),
        ({"type": "Point", "coordinates": [1, 2], "bbox": [1, 2, "3", 4]}, "$.bbox[2]", "not a number"),
        (_collection(_anycrs(_feature(None))), "$.features[0].type", "would be 'Feature'"),
        ({"type": "AnyCrsFeatureCollection", "features": [_feature(None)]}, "$.features[0].type", "would be 'AnyCrsFeature'"),
        ({"type": "AnyCrsFeatureCollection", "features": [_anycrs(_feature(None)) | {"geometry": {"type": "Point", "coordinates": [1, 2]}}]}, "$.features[0].geometry.type", "would be 'AnyCrsPoint'"),
    ],
)  # fmt: skip
def test_invalid_documents_are_refused_where_they_are_wrong(
    document: Any, path: str, reason: str
) -> None:
    with pytest.raises(InvalidGeoJsonError, match=reason) as raised:
        read_document(document)
    assert raised.value.path == path
    assert str(raised.value).startswith(f"{path}: ")


@pytest.mark.parametrize(
    ("document", "path", "reason"),
    [
        (_point([1, 2, 3, 4]), "$.features[0].geometry.coordinates", "4 numbers"),
        (
            _collection(conformsTo=["http://www.opengis.net/spec/json-fg-1/1.0/conf/core"]),
            "$.conformsTo",
            "JSON-FG",
        ),
        (_collection(coordRefSys="http://www.opengis.net/def/crs/EPSG/0/25832"), "$.coordRefSys", "coordRefSys"),
        (_collection(_feature(None, place={"type": "Point", "coordinates": [1, 2]})), "$.features[0].place", "place"),
        (_collection(_feature({"type": "Point", "coordinates": [1, 2], "measures": {"enabled": True}})), "$.features[0].geometry.measures", "measures"),
        ({"type": "CircularString", "coordinates": []}, "$.type", "JSON-FG geometry type"),
        (_collection(_feature(None, persistableReferenceCrs="{}")), "$.features[0].persistableReferenceCrs", "only on the document's root"),
        (_collection(_feature({"type": "Point", "coordinates": [1, 2], "crs": {"type": "name"}})), "$.features[0].geometry.crs", "only on the document's root"),
    ],
)  # fmt: skip
def test_what_is_not_converted_yet_is_refused(
    document: Any, path: str, reason: str
) -> None:
    with pytest.raises(UnsupportedGeoJsonError, match=reason) as raised:
        read_document(document)
    assert raised.value.path == path


def test_harmless_foreign_members_are_not_mistaken_for_json_fg() -> None:
    source = _collection(
        _feature(None, place=None, time={"date": "2020-01-01"}),
        conformsTo=["http://example.com/profile"],
        crs=None,
    )
    assert read_document(source).root == source


def test_a_non_document_is_a_type_error() -> None:
    with pytest.raises(TypeError, match="__geo_interface__"):
        read_document(42)


def test_errors_carry_their_path_and_survive_pickling() -> None:
    error = InvalidGeoJsonError("$.features[1]", "a feature cannot be a 'Point'")
    restored = pickle.loads(pickle.dumps(error))
    assert type(restored) is InvalidGeoJsonError
    assert (restored.path, restored.reason, str(restored)) == (
        error.path,
        error.reason,
        str(error),
    )
    assert isinstance(error, GeoJsonError)
    assert isinstance(error, GeodeticEngineError)
