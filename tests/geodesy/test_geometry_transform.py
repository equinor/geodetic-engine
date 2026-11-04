"""Transforming geometries with the guarantees points have."""

from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd
import pytest
import shapely
from hypothesis import given
from hypothesis import strategies as st
from pyproj import CRS
from pyproj.crs import BoundCRS, CoordinateOperation

from geodetic_engine.geodesy import (
    AmbiguousOperationError,
    CoordinateOutOfRangeError,
    GeometryError,
    MissingCoordinateEpochError,
    Transformation,
    TransformationFailedError,
)
from geodetic_engine.geodesy import geometry as geometry_module
from geodetic_engine.geodesy.geometry import GeometryResult, GeometryResults

POLYGON = (
    "POLYGON ((2 60, 3 60, 3 61, 2 61, 2 60), (2.2 60.2, 2.2 60.4, 2.4 60.4, 2.2 60.2))"
)


@pytest.fixture(scope="module")
def ed50_to_utm() -> Transformation:
    return Transformation("EPSG:4230", "EPSG:32631", operation="EPSG:1612")


def _flat(geometry: Any, include_z: bool = False) -> np.ndarray:
    return shapely.get_coordinates(geometry, include_z=include_z)


@pytest.mark.parametrize(
    "wkt",
    [
        "POINT (2.5 63.5)",
        "LINESTRING (2.5 63.5, 3 64, 3.5 64.5)",
        POLYGON,
        "MULTIPOLYGON (((2 60, 3 60, 3 61, 2 60)), ((4 62, 5 62, 5 63, 4 62)))",
        "GEOMETRYCOLLECTION (POINT (2 60), LINESTRING (2 61, 3 62))",
    ],
)
def test_coordinates_are_bitwise_those_of_transform_arrays(
    ed50_to_utm: Transformation, wkt: str
) -> None:
    geometry = shapely.from_wkt(wkt)
    result = ed50_to_utm.transform_geometry(wkt)
    expected = ed50_to_utm.transform_arrays(_flat(geometry)).to_numpy()
    assert np.array_equal(_flat(result.geometry), expected)
    assert result.geometry.geom_type == geometry.geom_type


def test_ed50_to_wgs84_utm_with_a_named_operation(ed50_to_utm: Transformation) -> None:
    result = ed50_to_utm.transform_geometry("SRID=4230;POINT (2.5 63.5)")
    assert result.wkt(decimals=2) == "POINT (475017.58 7041354.6)"
    assert result.ewkt(decimals=2) == "SRID=32631;POINT (475017.58 7041354.6)"
    assert "EPSG:1612" in str(result.transformation)
    assert isinstance(result, GeometryResult)


def test_ed50_utm_and_wgs84_there_and_back() -> None:
    there = Transformation("EPSG:23031", "EPSG:4326", operation="EPSG:1612")
    back = Transformation("EPSG:4326", "EPSG:23031", operation="EPSG:1612")
    polygon = shapely.from_wkt(
        "POLYGON ((500000 6650000, 510000 6650000, 510000 6660000, 500000 6650000))"
    )
    returned = back.transform_geometry(there.transform_geometry(polygon).geometry)
    # A Helmert reversed by negating its parameters returns within 0.1 mm.
    assert np.allclose(_flat(returned.geometry), _flat(polygon), rtol=0, atol=1e-3)


def test_an_early_bound_crs_brings_its_operation() -> None:
    bound = BoundCRS(
        CRS.from_epsg(23031),
        CRS.from_epsg(4326),
        CoordinateOperation.from_authority("EPSG", 1612),
    )
    result = Transformation(bound, "EPSG:4326").transform_geometry(
        "POINT (500000 6650000)"
    )
    expected = Transformation(
        "EPSG:23031", "EPSG:4326", operation="EPSG:1612"
    ).transform((500000.0, 6650000.0))
    assert result.geometry.coords[0] == expected.coordinates[0]


def test_heights_change_vertical_datum_in_a_compound_crs() -> None:
    transformation = Transformation("EPSG:9518", "EPSG:4979", operation="EPSG:3858")
    result = transformation.transform_geometry("LINESTRING Z (10 60 40, 11 61 50)")
    expected = transformation.transform([(10.0, 60.0, 40.0), (11.0, 61.0, 50.0)])
    assert _flat(result.geometry, include_z=True).tolist() == [
        list(p) for p in expected.coordinates
    ]
    assert result.geometry.has_z


def test_an_epoch_dependent_operation_needs_the_epoch() -> None:
    transformation = Transformation("EPSG:4896", "EPSG:4938", operation="EPSG:6277")
    point = "POINT Z (-2593197.524 5656917.6189 -1394397.8828)"
    with pytest.raises(MissingCoordinateEpochError):
        transformation.transform_geometry(point)
    with pytest.raises(MissingCoordinateEpochError):
        transformation.transform_geometry("POINT EMPTY")
    early = transformation.transform_geometry(point, coordinate_epoch=1994.0)
    late = transformation.transform_geometry(point, coordinate_epoch=2024.0)
    assert early.geometry.coords[0] != late.geometry.coords[0]
    assert early.transformation["coordinate_epoch"] == 1994.0


def test_a_datum_change_needs_an_operation() -> None:
    with pytest.raises(AmbiguousOperationError):
        Transformation("EPSG:4230", "EPSG:4326").transform_geometry("POINT (2 60)")


def test_heights_are_carried_and_measures_kept(ed50_to_utm: Transformation) -> None:
    result = ed50_to_utm.transform_geometry("LINESTRING ZM (2 60 10 1, 3 61 20 2)")
    coordinates = shapely.get_coordinates(
        result.geometry, include_z=True, include_m=True
    )
    assert coordinates[:, 2:].tolist() == [[10.0, 1.0], [20.0, 2.0]]
    expected = ed50_to_utm.transform_arrays(
        [[2.0, 60.0, 10.0], [3.0, 61.0, 20.0]]
    ).to_numpy()
    assert np.array_equal(coordinates[:, :3], expected)
    measured = ed50_to_utm.transform_geometry("POINT M (2 60 7)")
    assert measured.geometry.m == 7.0 and not measured.geometry.has_z


def test_none_and_empty_geometries_pass_through(ed50_to_utm: Transformation) -> None:
    assert ed50_to_utm.transform_geometry(None).geometry is None
    assert ed50_to_utm.transform_geometry("POLYGON EMPTY").wkt() == "POLYGON EMPTY"


def test_the_provenance_is_written_out_when_read(ed50_to_utm: Transformation) -> None:
    # Writing it out takes longer than transforming a point, so a geometry
    # transformed alone does not pay for it until it is read.
    results = ed50_to_utm.transform_geometries(["POINT (2 60)", "POINT (3 61)"])
    expected = ed50_to_utm.transform_arrays(np.array([[2.0, 60.0]]))
    assert results.transformation == expected.metadata_json_dict()
    assert results[1].transformation == results.transformation
    assert results.transformation is not results.transformation
    assert all(not len(column) for column in results._provenance.columns)


@pytest.mark.parametrize(
    "value", [None, "POINT EMPTY", "POLYGON EMPTY", "GEOMETRYCOLLECTION EMPTY"]
)
def test_none_and_empty_geometries_need_no_height_for_a_3d_target(
    value: str | None,
) -> None:
    # Positions of two values cannot reach EPSG:4979, but these have none.
    to_3d = Transformation("EPSG:32631", "EPSG:4979")
    result = to_3d.transform_geometry(value)
    assert result.wkt() == value
    assert result.transformation["target_crs"] == "EPSG:4979"
    assert to_3d.transform_geometries([value, None]).wkt() == [value, None]


def test_an_srid_must_be_the_source_crs(ed50_to_utm: Transformation) -> None:
    with pytest.raises(GeometryError, match="EPSG:4326"):
        ed50_to_utm.transform_geometry("SRID=4326;POINT (2 60)")
    with pytest.raises(GeometryError, match="geometry 1"):
        ed50_to_utm.transform_geometries(["POINT (2 60)", "SRID=4326;POINT (2 60)"])
    assert ed50_to_utm.transform_geometry("SRID=0;POINT (2 60)").geometry is not None


def test_a_batch_reads_each_geometry_as_it_reads_one(
    ed50_to_utm: Transformation,
) -> None:
    # A batch is read with one Shapely call per encoding, in any order.
    point = shapely.Point(2, 60)
    items: list[Any] = [
        shapely.to_wkb(point),
        bytearray(shapely.to_wkb(point)),
        shapely.to_wkb(point, hex=True),
        " POINT (2 60) ",
        "SRID=4230;POINT (2 60)",
        shapely.to_wkb(shapely.set_srid(point, 4230), include_srid=True),
        point,
        None,
        {"type": "Point", "coordinates": [2, 60]},
        '{"x": 2, "y": 60}',
    ]
    alone = [
        None if item is None else ed50_to_utm.transform_geometry(item).ewkt()
        for item in items
    ]
    assert ed50_to_utm.transform_geometries(items).ewkt() == alone


def test_a_batch_names_the_first_geometry_that_fails(
    ed50_to_utm: Transformation,
) -> None:
    # The WKB is read before the WKT, but comes after it in the batch.
    with pytest.raises(GeometryError, match="geometry 1: not WKT"):
        ed50_to_utm.transform_geometries(["POINT (2 60)", "POINT (2", b"garbage"])
    with pytest.raises(GeometryError, match="geometry 1: an SRID is not negative"):
        ed50_to_utm.transform_geometries(
            [shapely.to_wkb(shapely.Point(2, 60)), "SRID=-1;POINT (2 60)", b"garbage"]
        )


def test_a_batch_reads_each_srid_once(
    ed50_to_utm: Transformation, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Reading an SRID looks at the database files: once per geometry was most
    # of the time a batch of EWKB took. SRID 0 states no CRS, and is not read.
    read: list[int] = []
    original = geometry_module._srid_crs

    def counting(srid: int, authority: str) -> Any:
        read.append(srid)
        return original(srid, authority)

    monkeypatch.setattr(geometry_module, "_srid_crs", counting)
    batch = [
        *["SRID=4230;POINT (2 60)"] * 5,
        "SRID=0;POINT (2 61)",
        shapely.Point(2, 62),
    ]
    assert len(ed50_to_utm.transform_geometries(batch).wkt()) == 7
    assert sorted(read) == [4230]


def test_a_target_without_an_integer_code_writes_no_srid() -> None:
    bound = BoundCRS(
        CRS.from_epsg(23031),
        CRS.from_epsg(4326),
        CoordinateOperation.from_authority("EPSG", 1612),
    )
    result = Transformation("EPSG:4326", bound).transform_geometry("POINT (3 60)")
    assert result.wkt() is not None
    with pytest.raises(GeometryError, match="no integer code"):
        result.ewkt()


def test_a_failing_position_names_its_geometry_part_and_vertex(
    ed50_to_utm: Transformation,
) -> None:
    with pytest.raises(
        CoordinateOutOfRangeError, match="geometry 1, part 1, vertex 1 "
    ):
        ed50_to_utm.transform_geometries(
            ["POINT (2 60)", "MULTILINESTRING ((2 60, 3 61), (2 60, 3 95))"]
        )
    with pytest.raises(
        CoordinateOutOfRangeError, match="geometry 0, ring 1, vertex 2 "
    ):
        ed50_to_utm.transform_geometry(
            "POLYGON ((2 60, 3 60, 3 61, 2 60), (2.1 60.1, 2.2 60.1, 2.2 95, 2.1 60.1))"
        )


def test_a_failure_proj_does_not_place_is_found(ed50_to_utm: Transformation) -> None:
    utm = Transformation("EPSG:4326", "EPSG:32631")
    with pytest.raises(TransformationFailedError, match="geometry 0, part 2, vertex 0"):
        utm.transform_geometry("MULTIPOINT ((3 60), (4 61), (93 0))")


def test_a_failure_no_position_is_to_blame_for_names_none() -> None:
    # EPSG:4979 has a height that two values per position do not give, whichever.
    to_3d = Transformation("EPSG:32631", "EPSG:4979")
    with pytest.raises(TransformationFailedError, match="declares 3 axes") as raised:
        to_3d.transform_geometries(
            ["POINT (500000 6600000)", "LINESTRING (500000 6600000, 500100 6600100)"]
        )
    assert "geometry" not in str(raised.value)
    assert "vertex" not in str(raised.value)


def test_collection_members_keep_their_own_dimensions(
    ed50_to_utm: Transformation,
) -> None:
    result = ed50_to_utm.transform_geometry(
        "GEOMETRYCOLLECTION (POINT (2.5 63.5), POINT M (3 64 7), POINT Z (3 64 10))"
    )
    assert result.wkt(decimals=1) == (
        "GEOMETRYCOLLECTION ZM (POINT (475017.6 7041354.6), "
        "POINT M (499910.8 7096974.1 7), POINT Z (499910.8 7096974.1 10))"
    )
    with pytest.raises(
        CoordinateOutOfRangeError, match="geometry 1, part 1, vertex 1 "
    ):
        ed50_to_utm.transform_geometries(
            [
                "POINT (2 60)",
                "GEOMETRYCOLLECTION (POINT (2 60), LINESTRING Z (2 60 1, 3 95 2))",
            ]
        )


def test_a_split_collection_after_a_missing_geometry_keeps_its_members(
    ed50_to_utm: Transformation,
) -> None:
    mixed = "GEOMETRYCOLLECTION (POINT (2 60), POINT Z (3 61 10))"
    batch = ed50_to_utm.transform_geometries([None, mixed, "POINT (2 60)"])
    assert batch.wkt() == [
        None,
        ed50_to_utm.transform_geometry(mixed).wkt(),
        ed50_to_utm.transform_geometry("POINT (2 60)").wkt(),
    ]


def test_an_empty_member_beside_measures_is_kept(ed50_to_utm: Transformation) -> None:
    result = ed50_to_utm.transform_geometry(
        "GEOMETRYCOLLECTION (POINT ZM (2 60 1 2), POINT EMPTY)"
    )
    assert result.wkt(decimals=1) == (
        "GEOMETRYCOLLECTION ZM (POINT ZM (444131.2 6651774.9 1 2), POINT EMPTY)"
    )
    with pytest.raises(ValueError, match="geometry 0"):
        Transformation("EPSG:4979", "EPSG:4978").transform_geometry("POINT (10 60)")


def test_orientation_is_kept_unless_asked_for() -> None:
    utm = Transformation("EPSG:4326", "EPSG:32631")
    # Counterclockwise as written, latitude first; clockwise once read xy.
    declared = "POLYGON ((60 2, 61 2, 61 3, 60 3, 60 2))"
    kept = utm.transform_geometry(declared, axis_order="crs")
    assert not kept.geometry.exterior.is_ccw
    assert utm.transform_geometry(
        declared, axis_order="crs", orient="ccw"
    ).geometry.exterior.is_ccw
    assert not utm.transform_geometry(
        declared, axis_order="crs", orient="cw"
    ).geometry.exterior.is_ccw


def test_axis_order_crs_is_read_and_written() -> None:
    transformation = Transformation("EPSG:4326", "EPSG:4258", operation="EPSG:1149")
    result = transformation.transform_geometry("POINT (60 10)", axis_order="crs")
    assert result.geometry.coords[0] == pytest.approx((10.0, 60.0))
    assert result.wkt(decimals=6) == "POINT (60 10)"


def test_densify_adds_vertices_in_source_units(ed50_to_utm: Transformation) -> None:
    result = ed50_to_utm.transform_geometry("LINESTRING (2 60, 3 60)", densify=0.25)
    assert len(result.geometry.coords) == 5
    with pytest.raises(ValueError, match="densify"):
        ed50_to_utm.transform_geometry("LINESTRING (2 60, 3 60)", densify=0)


def test_a_batch_comes_back_in_its_container(ed50_to_utm: Transformation) -> None:
    values = ["POINT (2 60)", None, "LINESTRING (2 60, 3 61)"]
    listed = ed50_to_utm.transform_geometries(values)
    assert isinstance(listed, GeometryResults)
    assert len(listed) == 3 and listed[1].geometry is None
    assert isinstance(listed.wkt(), list)
    arrayed = ed50_to_utm.transform_geometries(np.array(values, dtype=object))
    assert isinstance(arrayed.geometries, np.ndarray)
    hexes = arrayed.wkb_hex()
    assert isinstance(hexes, np.ndarray) and hexes[1] is None
    assert hexes[0] == listed.wkb_hex()[0]
    series = pd.Series(values, index=["a", "b", "c"], name="shape")
    serial = ed50_to_utm.transform_geometries(series)
    written = serial.wkt(decimals=0)
    assert isinstance(written, pd.Series) and list(written.index) == ["a", "b", "c"]
    assert written["a"] == listed.wkt(decimals=0)[0]
    assert [r.wkt(0) for r in listed] == listed.wkt(decimals=0)
    with pytest.raises(TypeError, match="transform_geometry"):
        ed50_to_utm.transform_geometries("POINT (2 60)")


def test_a_geoseries_is_checked_and_comes_back_with_the_target_crs(
    ed50_to_utm: Transformation,
) -> None:
    geopandas = pytest.importorskip("geopandas")
    series = geopandas.GeoSeries.from_wkt(["POINT (2 60)"], crs="EPSG:4230")
    result = ed50_to_utm.transform_geometries(series)
    assert result.geometries.crs == CRS.from_epsg(32631)
    with pytest.raises(GeometryError, match="EPSG:4326"):
        ed50_to_utm.transform_geometries(
            series.set_crs("EPSG:4326", allow_override=True)
        )
    stating_another = shapely.set_srid(shapely.Point(2, 60), 4326)
    with pytest.raises(GeometryError, match="geometry 0 is in EPSG:4326"):
        ed50_to_utm.transform_geometries(
            geopandas.GeoSeries([stating_another], crs="EPSG:4230")
        )


_LONGITUDE = st.floats(min_value=0.5, max_value=5.5)
_LATITUDE = st.floats(min_value=56.0, max_value=71.0)


@given(st.lists(st.tuples(_LONGITUDE, _LATITUDE), min_size=2, max_size=10))
def test_there_and_back_stays_within_a_millimetre(
    points: list[tuple[float, float]],
) -> None:
    there = Transformation("EPSG:4230", "EPSG:32631", operation="EPSG:1612")
    back = Transformation("EPSG:32631", "EPSG:4230", operation="EPSG:1612")
    line = shapely.LineString(points)
    returned = back.transform_geometry(there.transform_geometry(line).geometry).geometry
    assert np.allclose(_flat(returned), _flat(line), rtol=0, atol=1e-8)
