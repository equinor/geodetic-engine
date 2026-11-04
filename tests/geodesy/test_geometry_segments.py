"""Segments of a geometry as straight lines or geodesics, densified before transforming."""

from __future__ import annotations

import numpy as np
import pytest
import shapely
from pyproj import Geod, Transformer

from geodetic_engine.geodesy import (
    CoordinateReferenceSystem,
    GeometryError,
    Transformation,
)
from geodetic_engine.geodesy._segments import Segments, densify_rows

# ED50's International 1924 ellipsoid, on which boundaries of the Norwegian
# continental shelf are drawn as geodesics.
INTERNATIONAL = Geod(ellps="intl")


def _geodesic(
    start: tuple[float, float], end: tuple[float, float]
) -> shapely.LineString:
    """A geodesic in ED50 longitude and latitude, as a line of 1000 vertices."""
    middle = INTERNATIONAL.npts(*start, *end, 998)
    return shapely.LineString([start, *middle, end])


def test_a_geodesic_is_drawn_on_the_source_ellipsoid() -> None:
    to_utm = Transformation("EPSG:4230", "EPSG:23032")
    expected = to_utm.transform_geometry(_geodesic((5.0, 62.0), (10.0, 66.0))).geometry
    drawn = to_utm.transform_geometry(
        "LINESTRING (5 62, 10 66)", densify=1000, segments="geodesic"
    ).geometry
    assert drawn.hausdorff_distance(expected) < 0.01
    # Straight in longitude and latitude, the same edge is kilometres off.
    straight = to_utm.transform_geometry("LINESTRING (5 62, 10 66)", densify=0.01)
    assert straight.geometry.hausdorff_distance(expected) > 8000


def test_the_given_vertices_are_kept_bit_for_bit() -> None:
    to_utm = Transformation("EPSG:4230", "EPSG:23032")
    polygon = "POLYGON Z ((2 60 1, 8 60 2, 8 62 3, 2 62 4, 2 60 1))"
    drawn = to_utm.transform_geometry(polygon, densify=5000, segments="geodesic")
    corners = to_utm.transform_arrays(
        shapely.get_coordinates(shapely.from_wkt(polygon), include_z=True)
    ).to_numpy()
    rows = {
        tuple(row)
        for row in shapely.get_coordinates(drawn.geometry, include_z=True).tolist()
    }
    assert {tuple(row) for row in corners.tolist()} <= rows
    assert len(rows) > 40
    heights = shapely.get_coordinates(drawn.geometry, include_z=True)[:, 2]
    assert heights.min() >= 1 and heights.max() <= 4


def test_a_projected_source_draws_geodesics_on_its_geographic_crs() -> None:
    # Two ED50 UTM 32N positions; the geodesic between them, in ED50 degrees.
    forward = Transformer.from_crs("EPSG:4230", "EPSG:23032", always_xy=True)
    ends = [forward.transform(5.0, 62.0), forward.transform(10.0, 66.0)]
    back = Transformation("EPSG:23032", "EPSG:4230")
    drawn = back.transform_geometry(
        shapely.LineString(ends), densify=1000, segments="geodesic"
    ).geometry
    rows = shapely.get_coordinates(drawn)[1:]
    # On the geodesic from the start exactly where it sets off towards the end.
    azimuth, _, _ = INTERNATIONAL.inv(5.0, 62.0, 10.0, 66.0)
    towards, _, _ = INTERNATIONAL.inv(
        np.full(len(rows), 5.0), np.full(len(rows), 62.0), rows[:, 0], rows[:, 1]
    )
    assert len(rows) > 500
    assert np.abs(np.asarray(towards) - azimuth).max() < 1e-8


def test_a_geodesic_across_the_antimeridian_takes_the_short_way() -> None:
    to_utm = Transformation("OGC:CRS84", "EPSG:32660")
    line = "LINESTRING (179 63, -179 63)"
    short = to_utm.transform_geometry(line, densify=5000, segments="geodesic")
    assert short.geometry.bounds[2] - short.geometry.bounds[0] < 110_000
    # Straight in longitude and latitude, the segment goes the long way round.
    long = to_utm.transform_geometry(line, densify=1)
    assert long.geometry.bounds[2] - long.geometry.bounds[0] > 5_000_000


def test_points_and_short_segments_are_kept() -> None:
    to_utm = Transformation("EPSG:4230", "EPSG:23032")
    multipoint = "MULTIPOINT ((5 62), (10 66))"
    assert to_utm.transform_geometry(
        multipoint, densify=1000, segments="geodesic"
    ).geometry.equals(to_utm.transform_geometry(multipoint).geometry)
    short = "LINESTRING (5 62, 5.001 62)"
    assert (
        shapely.get_num_coordinates(
            to_utm.transform_geometry(short, densify=1000, segments="geodesic").geometry
        )
        == 2
    )


def test_collections_keep_their_structure() -> None:
    to_utm = Transformation("EPSG:4230", "EPSG:23032")
    collection = (
        "GEOMETRYCOLLECTION (POINT (5 62), LINESTRING (5 62, 6 62), "
        "MULTIPOLYGON (((2 60, 3 60, 3 61, 2 60)), ((4 60, 5 60, 5 61, 4 60))))"
    )
    drawn = to_utm.transform_geometry(collection, densify=2000, segments="geodesic")
    kinds = [part.geom_type for part in drawn.geometry.geoms]
    assert kinds == ["Point", "LineString", "MultiPolygon"]
    assert all(shapely.is_valid(part) for part in drawn.geometry.geoms)


def test_geodesics_need_an_ellipsoid() -> None:
    geocentric = Transformation("EPSG:4978", "EPSG:4979")
    line = "LINESTRING Z (3000000 500000 5500000, 3100000 500000 5400000)"
    with pytest.raises(GeometryError, match="ellipsoid"):
        geocentric.transform_geometry(line, densify=1000, segments="geodesic")
    height = CoordinateReferenceSystem.from_user_input("EPSG:5941")
    rows = np.array([[0.0, 0.0, 1.0], [1.0, 1.0, 2.0]])
    with pytest.raises(ValueError, match="ellipsoid"):
        densify_rows(rows, np.zeros(2, dtype=np.intp), height, 1000.0, "geodesic")


def test_geodesics_need_a_length_and_segments_a_known_kind() -> None:
    to_utm = Transformation("EPSG:4230", "EPSG:23032")
    with pytest.raises(ValueError, match="densify"):
        to_utm.transform_geometry("LINESTRING (5 62, 10 66)", segments="geodesic")
    with pytest.raises(ValueError, match="segments"):
        to_utm.transform_geometry(
            "LINESTRING (5 62, 10 66)",
            densify=1000,
            segments="rhumb",  # type: ignore[arg-type]
        )
    with pytest.raises(ValueError, match="positive"):
        to_utm.transform_geometry("LINESTRING (5 62, 10 66)", densify=0)
    with pytest.raises(GeometryError, match="M values"):
        to_utm.transform_geometry(
            "LINESTRING M (5 62 1, 10 66 2)", densify=1000, segments="geodesic"
        )


def test_straight_segments_are_densified_as_before() -> None:
    to_utm = Transformation("EPSG:4230", "EPSG:23032")
    straight = to_utm.transform_geometry("LINESTRING (5 62, 6 62)", densify=0.25)
    assert shapely.get_num_coordinates(straight.geometry) == 5
    rows = shapely.get_coordinates(straight.geometry)
    expected = to_utm.transform_arrays(
        np.array([[5.0, 62.0], [5.25, 62.0], [5.5, 62.0], [5.75, 62.0], [6.0, 62.0]])
    ).to_numpy()
    assert np.array_equal(rows, expected)


@pytest.mark.parametrize(
    "wkt",
    [
        "MULTIPOLYGON (((2 60, 3 60, 3 61, 2 60)), EMPTY)",
        "MULTIPOLYGON (EMPTY, ((2 60, 3 60, 3 61, 2 60)))",
        "MULTILINESTRING ((5 62, 6 62), EMPTY)",
        "GEOMETRYCOLLECTION (POINT (5 62), LINESTRING EMPTY, LINESTRING (5 62, 6 62))",
    ],
)
@pytest.mark.parametrize(
    ("segments", "length"), [("straight", 0.25), ("geodesic", 20000.0)]
)
def test_an_empty_part_is_kept_as_the_others_are_densified(
    wkt: str, segments: Segments, length: float
) -> None:
    # GEOS's segmentize, which densified straight segments, crashed the
    # interpreter on these.
    to_utm = Transformation("EPSG:4230", "EPSG:23032")
    source = shapely.from_wkt(wkt)
    drawn = to_utm.transform_geometry(source, densify=length, segments=segments)
    parts = shapely.get_parts(drawn.geometry)
    assert drawn.geometry.geom_type == source.geom_type
    assert (
        shapely.is_empty(parts).tolist()
        == shapely.is_empty(shapely.get_parts(source)).tolist()
    )
    assert shapely.get_num_coordinates(drawn.geometry) > shapely.get_num_coordinates(
        source
    )


def test_an_invalid_polygon_is_densified_as_it_is() -> None:
    # A bow tie, its edges crossing. GEOS's segmentize buffered it into
    # another geometry; its vertices are kept, and those inserted added.
    to_utm = Transformation("EPSG:4230", "EPSG:23032")
    bow_tie = "POLYGON ((2 60, 3 61, 3 60, 2 61, 2 60))"
    drawn = to_utm.transform_geometry(bow_tie, densify=0.25).geometry
    assert drawn.geom_type == "Polygon" and not drawn.is_valid
    rows = shapely.get_coordinates(drawn)
    corners = to_utm.transform_arrays(
        shapely.get_coordinates(shapely.from_wkt(bow_tie))
    ).to_numpy()
    # 6, 4, 6 and 4 pieces to the four edges.
    assert len(rows) == 21
    assert np.array_equal(rows[[0, 6, 10, 16, 20]], corners)
