"""Reading, writing and rounding geometries in every encoding."""

from __future__ import annotations

import json
import warnings
from typing import Any

import numpy as np
import pytest
import shapely
from hypothesis import given
from hypothesis import strategies as st

from geodetic_engine.geodesy import (
    CoordinateReferenceSystem,
    GeometryError,
    GeometryValidityWarning,
)
from geodetic_engine.geodesy._wkb import replace_coordinates
from geodetic_engine.geodesy.geometry import (
    FORMATS,
    read_geometry,
    round_geometry,
    write_geometry,
)

POSTGIS_EWKB = "0101000020E6100000C7726EBA353F24405B39F0746B7E4E40"
POSTGIS_POINT = (10.123456789, 60.987654321)

# Coordinates of each geometry type, as nested lists of (x, y) pairs.
_SQUARE = [(0, 0), (10, 0), (10, 10), (0, 10), (0, 0)]
_HOLE = [(2, 2), (2, 4), (4, 4), (4, 2), (2, 2)]
_SHAPES: dict[str, Any] = {
    "POINT": (1.5, 2.5),
    "LINESTRING": [(1, 2), (3, 4), (5, 6)],
    "POLYGON": [_SQUARE, _HOLE],
    "MULTIPOINT": [(1, 2), (3, 4)],
    "MULTILINESTRING": [[(1, 2), (3, 4)], [(5, 6), (7, 8), (9, 10)]],
    "MULTIPOLYGON": [[_SQUARE, _HOLE], [[(20, 20), (30, 20), (30, 30), (20, 20)]]],
}
_DIMENSIONS = ("", " Z", " M", " ZM")


def _position(pair: tuple[float, float], dimension: str) -> str:
    x, y = pair
    extra = {"": [], " Z": [x + y], " M": [x * 2], " ZM": [x + y, x * 2]}[dimension]
    return " ".join(repr(float(value)) for value in (x, y, *extra))


def _body(value: Any, dimension: str) -> str:
    if isinstance(value, tuple):
        return _position(value, dimension)
    parts = [_body(item, dimension) for item in value]
    if value and isinstance(value[0], tuple):
        return ", ".join(parts)
    return ", ".join(f"({part})" for part in parts)


def _wkt(kind: str, dimension: str) -> str:
    shape = _SHAPES[kind]
    if kind == "POINT":
        return f"POINT{dimension} ({_position(shape, dimension)})"
    if kind == "MULTIPOINT":
        points = ", ".join(f"({_position(p, dimension)})" for p in shape)
        return f"MULTIPOINT{dimension} ({points})"
    return f"{kind}{dimension} ({_body(shape, dimension)})"


def _geometries() -> list[Any]:
    cases = []
    for dimension in _DIMENSIONS:
        for kind in _SHAPES:
            cases.append(pytest.param(_wkt(kind, dimension), id=f"{kind}{dimension}"))
            cases.append(
                pytest.param(f"{kind}{dimension} EMPTY", id=f"{kind}{dimension} EMPTY")
            )
        members = ", ".join(
            _wkt(kind, dimension) for kind in ("POINT", "LINESTRING", "POLYGON")
        )
        cases.append(
            pytest.param(
                f"GEOMETRYCOLLECTION{dimension} ({members})",
                id=f"GEOMETRYCOLLECTION{dimension}",
            )
        )
        cases.append(
            pytest.param(
                f"GEOMETRYCOLLECTION{dimension} EMPTY",
                id=f"GEOMETRYCOLLECTION{dimension} EMPTY",
            )
        )
    return cases


def _same(first: Any, second: Any) -> bool:
    return bool(shapely.equals_identical(first, second))


@pytest.mark.parametrize("wkt", _geometries())
@pytest.mark.parametrize(
    "format", ["wkt", "wkb", "wkb_hex", "ewkt", "ewkb", "ewkb_hex"]
)
def test_every_type_and_dimension_round_trips(wkt: str, format: str) -> None:
    geometry = shapely.from_wkt(wkt)
    for byte_order in ("little", "big"):
        written = write_geometry(
            geometry, format, crs="EPSG:4326", byte_order=byte_order
        )  # type: ignore[arg-type]
        read, crs = read_geometry(written)
        assert _same(read, geometry), (written, read)
        expected = "EPSG:4326" if format.startswith("e") else None
        assert (crs.authority_code if crs else None) == expected


@pytest.mark.parametrize(
    "wkt", [p for p in _geometries() if " M" not in p.id and " ZM" not in p.id]
)
def test_geojson_round_trips_without_measures(wkt: str) -> None:
    geometry = shapely.from_wkt(wkt)
    written = write_geometry(geometry, "geojson")
    json.dumps(written, allow_nan=False)
    read, crs = read_geometry(written)
    assert crs is None
    assert shapely.equals_identical(read, geometry) or (
        read.is_empty and geometry.is_empty
    )


def test_geojson_has_no_place_for_measures() -> None:
    with pytest.raises(GeometryError, match="M values"):
        write_geometry(shapely.from_wkt("POINT M (1 2 3)"), "geojson")


def test_wkb_is_iso_and_ewkb_extended() -> None:
    point = shapely.from_wkt("POINT Z (1 2 3)")
    assert write_geometry(point, "wkb_hex").startswith("01E9030000")
    assert write_geometry(point, "wkb_hex", byte_order="big").startswith("00000003E9")
    assert write_geometry(point, "ewkb_hex", crs="EPSG:4326").startswith(
        "01010000A0E6100000"
    )
    assert isinstance(write_geometry(point, "wkb"), bytes)


def test_ewkb_matches_postgis() -> None:
    point = shapely.Point(*POSTGIS_POINT)
    assert write_geometry(point, "ewkb_hex", crs="EPSG:4326") == POSTGIS_EWKB
    for text in (POSTGIS_EWKB, POSTGIS_EWKB.lower(), bytes.fromhex(POSTGIS_EWKB)):
        geometry, crs = read_geometry(text)
        assert geometry.coords[0] == POSTGIS_POINT
        assert crs == CoordinateReferenceSystem.from_user_input("EPSG:4326")


def test_ewkt_states_and_reads_the_srid() -> None:
    point = shapely.Point(1, 2)
    assert write_geometry(point, "ewkt", crs="EPSG:23031") == "SRID=23031;POINT (1 2)"
    geometry, crs = read_geometry(" srid=23031; POINT (1 2)")
    assert crs.authority_code == "EPSG:23031"
    assert _same(geometry, point)
    assert read_geometry("SRID=0;POINT (1 2)")[1] is None


@pytest.mark.parametrize(
    ("authority", "srid", "code"),
    [("ESRI", 102100, "ESRI:102100"), ("IAU_2015", 49900, "IAU_2015:49900")],
)
def test_an_srid_is_a_code_of_any_authority(
    authority: str, srid: int, code: str
) -> None:
    geometry, crs = read_geometry(f"SRID={srid};POINT (1 2)", srid_authority=authority)
    assert crs.authority_code == code
    assert write_geometry(geometry, "ewkt", crs=crs) == f"SRID={srid};POINT (1 2)"


def test_a_crs_without_an_integer_code_has_no_srid() -> None:
    bound = "+proj=utm +zone=31 +ellps=intl +towgs84=-87,-98,-121 +units=m +no_defs +type=crs"
    with pytest.raises(GeometryError, match="no integer code"):
        write_geometry(shapely.Point(1, 2), "ewkb_hex", crs=bound)
    with pytest.raises(GeometryError, match="give the crs"):
        write_geometry(shapely.Point(1, 2), "ewkt")


@pytest.mark.parametrize(
    "value",
    [
        "CIRCULARSTRING (0 0, 1 1, 2 0)",
        "010800000003000000" + "0" * 96,
        "POINT (1",
        {"type": "Feature", "geometry": None, "properties": {}},
        {"paths": [], "curvePaths": []},
    ],
)
def test_what_cannot_be_read_is_a_geometry_error(value: object) -> None:
    with pytest.raises(GeometryError):
        read_geometry(value)


def test_a_feature_is_sent_to_convert_geojson() -> None:
    with pytest.raises(GeometryError, match="convert_geojson"):
        read_geometry({"type": "Feature", "geometry": None, "properties": {}})


def test_the_format_can_be_named() -> None:
    assert read_geometry("POINT (1 2)", format="wkt")[0].wkt == "POINT (1 2)"
    with pytest.raises(GeometryError, match="format='ewkt'"):
        read_geometry("SRID=4326;POINT (1 2)", format="wkt")
    with pytest.raises(TypeError):
        read_geometry("0101", format="wkb")
    with pytest.raises(ValueError, match="format must be one of"):
        read_geometry("POINT (1 2)", format="kml")  # type: ignore[arg-type]
    assert set(FORMATS) >= {"wkt", "ewkb_hex", "esrijson"}


def test_geo_interface_objects_are_read() -> None:
    class Located:
        @property
        def __geo_interface__(self) -> dict[str, Any]:
            return {"type": "Point", "coordinates": [1.0, 2.0]}

    assert read_geometry(Located())[0].wkt == "POINT (1 2)"
    with pytest.raises(TypeError, match="__geo_interface__"):
        read_geometry(42)


def test_esri_json_geometries_are_read() -> None:
    point, crs = read_geometry(
        {
            "x": 1,
            "y": 2,
            "z": 3,
            "m": 4,
            "spatialReference": {"wkid": 102100, "latestWkid": 3857},
        }
    )
    assert point.wkt == "POINT ZM (1 2 3 4)"
    assert crs.authority_code == "EPSG:3857"
    assert read_geometry({"x": None, "spatialReference": {"wkid": 4326}})[0].is_empty
    assert (
        read_geometry({"points": [[1, 2], [3, 4]]})[0].wkt
        == "MULTIPOINT ((1 2), (3 4))"
    )
    line, _ = read_geometry({"paths": [[[0, 0, 5], [1, 1, 6]]], "hasM": True})
    assert line.wkt == "LINESTRING M (0 0 5, 1 1 6)"
    assert (
        read_geometry({"paths": [[[0, 0], [1, 1]], [[2, 2], [3, 3]]]})[0].geom_type
        == "MultiLineString"
    )
    assert read_geometry({"xmin": 0, "ymin": 1, "xmax": 2, "ymax": 3})[0].bounds == (
        0,
        1,
        2,
        3,
    )


def test_esri_rings_are_exteriors_clockwise_and_holes_inside_them() -> None:
    clockwise = [[0, 0], [0, 10], [10, 10], [10, 0], [0, 0]]
    hole = [[2, 2], [4, 2], [4, 4], [2, 4], [2, 2]]
    other = [[20, 20], [20, 30], [30, 30], [20, 20]]
    geometry, _ = read_geometry({"rings": [clockwise, hole, other]})
    assert geometry.geom_type == "MultiPolygon"
    first, second = geometry.geoms
    assert len(first.interiors) == 1 and len(second.interiors) == 0
    with pytest.raises(GeometryError, match="inside no clockwise ring"):
        read_geometry({"rings": [clockwise, [[50, 50], [52, 50], [52, 52], [50, 50]]]})


def test_esri_json_is_written_with_its_spatial_reference() -> None:
    polygon = shapely.from_wkt(
        "POLYGON ((0 0, 10 0, 10 10, 0 10, 0 0), (2 2, 2 4, 4 4, 2 2))"
    )
    written = write_geometry(polygon, "esrijson", crs="EPSG:23031")
    assert written["spatialReference"] == {"wkid": 23031}
    exterior, interior = written["rings"]
    assert not shapely.LinearRing(exterior).is_ccw
    assert shapely.LinearRing(interior).is_ccw
    read, crs = read_geometry(written)
    assert read.equals(polygon) and crs.authority_code == "EPSG:23031"
    point = write_geometry(shapely.from_wkt("POINT ZM (1 2 3 4)"), "esrijson")
    assert point == {"x": 1.0, "y": 2.0, "z": 3.0, "m": 4.0}
    compound = write_geometry(shapely.Point(1, 2), "esrijson", crs="EPSG:4326+3855")
    assert compound["spatialReference"] in (
        {"wkid": 9518},
        {"wkid": 4326, "vcsWkid": 3855},
    )
    with pytest.raises(GeometryError, match="GeometryCollection"):
        write_geometry(shapely.from_wkt("GEOMETRYCOLLECTION (POINT (1 2))"), "esrijson")


def test_a_vertical_wkid_makes_the_crs_compound() -> None:
    _, crs = read_geometry(
        {"x": 1, "y": 2, "z": 3, "spatialReference": {"wkid": 4326, "vcsWkid": 3855}}
    )
    assert crs.crs.is_compound


def test_axis_order_crs_reads_and_writes_the_declared_order() -> None:
    geometry, crs = read_geometry("POINT (60 10)", axis_order="crs", crs="EPSG:4326")
    assert geometry.wkt == "POINT (10 60)"
    assert write_geometry(geometry, "wkt", axis_order="crs", crs=crs) == "POINT (60 10)"
    projected, _ = read_geometry(
        "POINT (500000 6600000)", axis_order="crs", crs="EPSG:23031"
    )
    assert projected.wkt == "POINT (500000 6600000)"
    with pytest.raises(ValueError, match="needs the CRS"):
        read_geometry("POINT (60 10)", axis_order="crs")
    with pytest.raises(GeometryError, match="not crs"):
        read_geometry("SRID=4326;POINT (60 10)", crs="EPSG:23031")


@pytest.mark.parametrize(
    ("wkt", "decimals", "expected"),
    [
        ("POINT (2.675 -0.125)", 2, "POINT (2.68 -0.13)"),
        ("POINT (123456.785 0.0001)", 2, "POINT (123456.79 0)"),
        ("POINT (2.5 1.5)", 0, "POINT (3 2)"),
        (
            "POINT Z (10.123456789 60.987654321 2.345)",
            (8, 8, 2),
            "POINT Z (10.12345679 60.98765432 2.35)",
        ),
        ("POINT M (1.25 2.25 3.25)", (1, 1, None, 0), "POINT M (1.3 2.3 3)"),
        ("POINT (597868.38123456 1e-7)", None, "POINT (597868.38123456 1e-7)"),
    ],
)
def test_rounding_gives_exactly_the_decimals_asked_for(
    wkt: str, decimals: Any, expected: str
) -> None:
    assert write_geometry(shapely.from_wkt(wkt), "wkt", decimals=decimals) == expected


def test_every_encoding_holds_the_same_rounded_values() -> None:
    geometry = shapely.from_wkt("LINESTRING (2.675 -0.125, 123456.785 1.005)")
    rounded = round_geometry(geometry, 2)
    for format in ("wkt", "wkb", "wkb_hex", "geojson"):
        read, _ = read_geometry(write_geometry(geometry, format, decimals=2))  # type: ignore[arg-type]
        assert _same(read, rounded)
    assert _same(round_geometry(rounded, 2), rounded)


def test_minus_zero_is_written_as_zero() -> None:
    small = write_geometry(shapely.Point(-0.001, 1), "wkb_hex", decimals=2)
    assert small == write_geometry(shapely.Point(0, 1), "wkb_hex")


def test_collection_members_are_rounded_in_their_own_dimensions() -> None:
    mixed = shapely.from_wkt(
        "GEOMETRYCOLLECTION (POINT (1.234 2.345), POINT M (1.234 2.345 3.456))"
    )
    assert write_geometry(mixed, "wkt", decimals=1) == (
        "GEOMETRYCOLLECTION M (POINT (1.2 2.3), POINT M (1.2 2.3 3.5))"
    )
    rounded = round_geometry(np.array([None, mixed], dtype=object), 1)
    assert rounded[0] is None
    assert write_geometry(rounded[1], "wkt") == write_geometry(mixed, "wkt", decimals=1)


def test_wkb_rewriting_never_writes_past_a_position() -> None:
    mixed = shapely.from_wkt("GEOMETRYCOLLECTION (POINT (1 2), POINT M (3 4 5))")
    wkb = shapely.to_wkb(mixed, flavor="iso", output_dimension=4)
    rows = np.array([[10.0, 20.0, 0.0], [30.0, 40.0, 5.0]])
    with pytest.raises(ValueError, match="differ in z or m"):
        replace_coordinates(wkb, rows)


@pytest.mark.parametrize(
    "wkt",
    [
        "GEOMETRYCOLLECTION (POINT EMPTY, LINESTRING EMPTY)",
        "GEOMETRYCOLLECTION (GEOMETRYCOLLECTION EMPTY)",
        "MULTIPOINT (EMPTY, (1 2))",
        "MULTIPOLYGON (EMPTY)",
    ],
)
def test_collections_of_empty_members_keep_them(wkt: str) -> None:
    assert write_geometry(shapely.from_wkt(wkt), "wkt") == wkt


def test_rounding_reports_a_geometry_it_made_invalid() -> None:
    sliver = shapely.from_wkt("POLYGON ((0 0, 10 0, 10 1, 5 0.004, 0 1, 0 0))")
    assert sliver.is_valid
    with pytest.warns(GeometryValidityWarning, match="invalid"):
        round_geometry(sliver, 2)
    with pytest.raises(GeometryError, match="grid_size"):
        round_geometry(sliver, 2, validate="raise")
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        round_geometry(sliver, 2, validate="off")
        snapped = round_geometry(sliver, grid_size=0.01)
    assert snapped.is_valid and not snapped.is_empty


def test_rounding_options_are_checked() -> None:
    point = shapely.Point(1, 2)
    with pytest.raises(ValueError, match="not both"):
        round_geometry(point, 2, grid_size=0.1)
    with pytest.raises(GeometryError, match="M values"):
        round_geometry(shapely.from_wkt("POINT M (1 2 3)"), grid_size=0.1)
    with pytest.raises(ValueError, match="m value"):
        round_geometry(shapely.from_wkt("POINT M (1 2 3)"), (2, 2, 2))
    assert round_geometry(None, 2) is None
    rounded = round_geometry([point, None], 0)
    assert isinstance(rounded, np.ndarray) and rounded[1] is None


_FINITE = st.floats(allow_nan=False, allow_infinity=False, width=64)


@given(st.lists(st.tuples(_FINITE, _FINITE, _FINITE), min_size=2, max_size=20))
def test_wkt_wkb_and_hex_are_lossless(
    positions: list[tuple[float, float, float]],
) -> None:
    line = shapely.LineString(positions)
    for format in ("wkt", "wkb", "wkb_hex", "ewkb_hex"):
        read, _ = read_geometry(write_geometry(line, format, crs="EPSG:4979"))  # type: ignore[arg-type]
        assert _same(read, line)
