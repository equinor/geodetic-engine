"""Geometries in a geographic CRS, cut at the antimeridian and closed through a pole."""

from __future__ import annotations

import numpy as np
import pytest
import shapely

from geodetic_engine.geodesy import GeometryError, Transformation

# UTM zone 60N has its central meridian at 177E: 800 km east of it, at 63N,
# is east of the antimeridian.
LINE = "LINESTRING (600000 7000000, 800000 7000000)"
STRIP = (
    "POLYGON ((600000 6990000, 800000 6990000, 800000 7010000, "
    "600000 7010000, 600000 6990000))"
)
# A square round the North Pole in NSIDC Polar Stereographic North.
ROUND_THE_POLE = (
    "POLYGON ((-1000000 -1000000, 1000000 -1000000, 1000000 1000000, "
    "-1000000 1000000, -1000000 -1000000))"
)


@pytest.fixture(scope="module")
def utm60() -> Transformation:
    return Transformation("EPSG:32660", "OGC:CRS84")


def _rows(geometry: shapely.Geometry) -> set[tuple[float, ...]]:
    return {tuple(row) for row in shapely.get_coordinates(geometry).tolist()}


def test_a_line_crossing_the_antimeridian_is_cut_there(utm60: Transformation) -> None:
    cut = utm60.transform_geometry(LINE).geometry
    assert cut.geom_type == "MultiLineString"
    west, east = (shapely.get_coordinates(part) for part in cut.geoms)
    assert (west[-1, 0], east[0, 0]) == (180.0, -180.0)
    assert west[-1, 1] == east[0, 1]
    assert cut.length == pytest.approx(3.95, abs=0.01)


def test_a_polygon_is_cut_into_parts_keeping_every_position(
    utm60: Transformation,
) -> None:
    dense = shapely.segmentize(shapely.from_wkt(STRIP), 10000)
    cut = utm60.transform_geometry(STRIP, densify=10000).geometry
    assert cut.geom_type == "MultiPolygon" and cut.is_valid
    expected = utm60.transform_arrays(shapely.get_coordinates(dense)).to_numpy()
    on_the_cut = {row for row in _rows(cut) if abs(row[0]) == 180.0}
    assert _rows(cut) - on_the_cut <= {tuple(row) for row in expected.tolist()}
    unwrapped = utm60.transform_geometry(STRIP, densify=10000, antimeridian="unwrap")
    assert unwrapped.geometry.geom_type == "Polygon"
    assert unwrapped.geometry.bounds[2] > 180.0
    assert cut.area == pytest.approx(unwrapped.geometry.area, rel=1e-12)


@pytest.mark.parametrize("ccw", [True, False])
def test_the_parts_are_wound_as_the_polygon_was(
    utm60: Transformation, ccw: bool
) -> None:
    polygon = shapely.from_wkt(STRIP)
    wound = shapely.orient_polygons(polygon, exterior_cw=not ccw)
    cut = utm60.transform_geometry(wound, densify=10000).geometry
    assert [part.exterior.is_ccw for part in cut.geoms] == [ccw, ccw]


def test_none_keeps_the_long_way_round(utm60: Transformation) -> None:
    kept = utm60.transform_geometry(STRIP, antimeridian=None).geometry
    assert kept.geom_type == "Polygon" and kept.area > 60


def test_a_segment_that_goes_the_long_way_round_is_kept() -> None:
    # In longitude and latitude this polygon is the band from 80S to 80N, not
    # a sliver across the antimeridian.
    world = "POLYGON ((-180 -80, 180 -80, 180 80, -180 80, -180 -80))"
    band = Transformation("OGC:CRS84", "EPSG:4326").transform_geometry(world)
    assert band.geometry.geom_type == "Polygon"
    assert band.geometry.area == pytest.approx(360 * 160)


def test_a_ring_round_the_north_pole_is_closed_through_it() -> None:
    cap = Transformation("EPSG:3413", "OGC:CRS84").transform_geometry(
        ROUND_THE_POLE, densify=20000
    )
    polygon = cap.geometry
    assert polygon.geom_type == "Polygon" and polygon.is_valid
    assert polygon.bounds[0] == -180.0 and polygon.bounds[2:] == (180.0, 90.0)
    assert polygon.contains(shapely.Point(0, 89.9))
    assert polygon.contains(shapely.Point(179.9, 84))
    assert not polygon.contains(shapely.Point(0, 70))


def test_a_hole_round_the_pole_leaves_a_band() -> None:
    annulus = (
        "POLYGON ((-2000000 -2000000, 2000000 -2000000, 2000000 2000000, "
        "-2000000 2000000, -2000000 -2000000), (-500000 -500000, 500000 -500000, "
        "500000 500000, -500000 500000, -500000 -500000))"
    )
    band = (
        Transformation("EPSG:3413", "OGC:CRS84")
        .transform_geometry(annulus, densify=20000)
        .geometry
    )
    assert band.is_valid
    assert not band.contains(shapely.Point(0, 89.9))
    assert band.contains(shapely.Point(10, 80))


def test_a_ring_round_the_south_pole_is_closed_through_it() -> None:
    cap = (
        Transformation("EPSG:3031", "OGC:CRS84")
        .transform_geometry(ROUND_THE_POLE, densify=20000)
        .geometry
    )
    assert cap.is_valid and cap.bounds[1] == -90.0
    assert cap.contains(shapely.Point(0, -89.9))


def test_a_batch_cuts_only_what_crosses(utm60: Transformation) -> None:
    near = "LINESTRING (600000 7000000, 650000 7000000)"
    results = utm60.transform_geometries([near, LINE, None])
    assert [g.geom_type if g else None for g in results.geometries] == [
        "LineString",
        "MultiLineString",
        None,
    ]


def test_every_encoding_is_written_from_the_cut_geometry() -> None:
    cut = Transformation("EPSG:32660", "EPSG:4326").transform_geometry(STRIP)
    assert cut.wkt(decimals=1).startswith("MULTIPOLYGON (((")
    assert cut.ewkt(decimals=1).startswith("SRID=4326;MULTIPOLYGON (((")
    assert shapely.from_wkb(cut.ewkb()).geom_type == "MultiPolygon"
    assert cut.geojson()["type"] == "MultiPolygon"
    assert len(cut.esrijson()["rings"]) == 2
    lat_first = Transformation("EPSG:32660", "EPSG:4326").transform_geometry(
        LINE, axis_order="crs"
    )
    west, east = shapely.from_wkt(lat_first.wkt()).geoms
    assert (west.coords[-1][1], east.coords[0][1]) == (180.0, -180.0)


def test_m_values_cannot_be_cut(utm60: Transformation) -> None:
    measured = "LINESTRING M (600000 7000000 1, 800000 7000000 2)"
    with pytest.raises(GeometryError, match="antimeridian=None"):
        utm60.transform_geometry(measured)
    kept = utm60.transform_geometry(measured, antimeridian=None)
    assert shapely.has_m(kept.geometry)


def test_a_projected_target_is_never_cut() -> None:
    to_utm = Transformation("OGC:CRS84", "EPSG:32660")
    cut = to_utm.transform_geometry(
        "MULTILINESTRING ((179 63, 180 63), (-180 63, -179 63))"
    )
    assert cut.geometry.geom_type == "MultiLineString"
    rows = shapely.get_coordinates(cut.geometry)
    assert np.ptp(rows[:, 0]) < 110_000


def test_antimeridian_is_one_of_its_choices(utm60: Transformation) -> None:
    with pytest.raises(ValueError, match="antimeridian"):
        utm60.transform_geometry(LINE, antimeridian="wrap")  # type: ignore[arg-type]
