"""Engineering CRSs honour the ``xy`` contract, in spite of PROJ.

PROJ's ``always_xy`` never normalises an engineering CRS, and it reads the
evaluation point of a Similarity transformation in the target CRS's declared
axis order even where the authority stated it easting-first. Either leaves the
values at an engineering end transposed with no error raised; the workaround
in ``geodetic_engine/geodesy/transformation.py`` (``_correct_engineering_axes``,
see the block comment there) undoes both. These tests pin the behaviour down
against evidence that does not come from PROJ's own affine step: the EPSG
Guidance Note 7-2 formula worked by hand, where a plant grid's origin lies on
the map, and a register's reference points.

Two of these tests are canaries. ``test_proj_still_leaves_engineering_axes_alone``
fails the day PROJ normalises engineering CRSs itself, and
``test_epsg_1035_states_its_evaluation_point_easting_first`` fails the day EPSG
restates that operation in declared order. Either failure means the workaround
must be retired or narrowed, not adapted.
"""

from __future__ import annotations

import math

import numpy as np
import pytest
from pyproj import CRS, Geod, Transformer
from pyproj.crs import CoordinateOperation

from geodetic_engine.geodesy import (
    CoordinateReferenceSystem,
    OperationNotAvailableError,
    OperationRoute,
    Transformation,
    transform,
)

# EPSG's one Similarity transformation out of an engineering CRS with no
# easting: Astra Minas Grid declares X north and Y west, into Campo Inchauspe /
# Argentina 2, which declares X northing and Y easting.
ASTRA_MINAS = "EPSG:5800"
ARGENTINA_2 = "EPSG:22192"
CAMPO_INCHAUSPE = "EPSG:4221"
ASTRA_TO_ARGENTINA_2 = "EPSG:1035"

# Values in the grid's declared order, X (north) then Y (west): a grid with no
# easting and northing to order is left in declared order, which is what its
# value_axis_order reports.
GRID_POINT = (10000.0, 20000.0)

# A plant grid declared northing-first, as the registers of plant grids
# commonly do, with the parameters of a real Norwegian site: its origin and
# its 10 degree twist relative to NGO 1948 zone II, stated as EPSG method 9621
# with the ordinates in the target's declared order (northing first).
SITE_GRID_NORTH_EAST = (
    'ENGCRS["Plant grid (N,E)",EDATUM["Plant origin"],CS[Cartesian,2],'
    'AXIS["northing (N)",north,ORDER[1]],AXIS["easting (E)",east,ORDER[2]],'
    'LENGTHUNIT["metre",1]]'
)
NGO_ZONE_II = "EPSG:27392"
NGO_ZONE_I = "EPSG:27391"
NGO_1948_OSLO = "EPSG:4817"  # the zones' own geographic CRS, counted from Oslo
SITE_ORIGIN_NORTHING = 593890.8085
SITE_ORIGIN_EASTING = 8321.883
SITE_ROTATION_DEGREES = 10.0

# The register's own reference points for that site, xy (easting, northing)
# both sides. The first point is symmetric, so it agrees whichever way the
# axes are read; the other two do not.
SITE_POINTS_XY = [(7000.0, 7000.0), (7700.0, 7500.0), (7300.0, 7400.0)]
SITE_EXPECTED_XY = [
    (14000.0, 602000.0),
    (14602.54134, 602613.9576),
    (14225.98305, 602446.0175),
]


def _similarity(
    source: str,
    target: str,
    ordinate_1: float,
    ordinate_2: float,
    rotation_degrees: float,
) -> CoordinateOperation:
    """EPSG method 9621 between two CRSs, stated outright as WKT."""
    return CoordinateOperation.from_string(
        f'COORDINATEOPERATION["Plant grid to {target}",'
        f"SOURCECRS[{CRS.from_user_input(source).to_wkt()}],"
        f"TARGETCRS[{CRS.from_user_input(target).to_wkt()}],"
        'METHOD["Similarity transformation",ID["EPSG",9621]],'
        'PARAMETER["Ordinate 1 of evaluation point in target CRS",'
        f'{ordinate_1},LENGTHUNIT["metre",1],ID["EPSG",8621]],'
        'PARAMETER["Ordinate 2 of evaluation point in target CRS",'
        f'{ordinate_2},LENGTHUNIT["metre",1],ID["EPSG",8622]],'
        'PARAMETER["Scale factor for source CRS axes",1.0,'
        'SCALEUNIT["unity",1],ID["EPSG",1061]],'
        'PARAMETER["Rotation angle of source CRS axes",'
        f'{rotation_degrees},ANGLEUNIT["degree",0.0174532925199433],'
        'ID["EPSG",8614]],'
        "OPERATIONACCURACY[0.01]]"
    )


def _grid_offsets(
    source: str, target: str, easting: float, northing: float
) -> CoordinateOperation:
    """EPSG method 9656 between two CRSs, stated outright as WKT."""
    return CoordinateOperation.from_string(
        f'COORDINATEOPERATION["Plant grid to {target} by offsets",'
        f"SOURCECRS[{CRS.from_user_input(source).to_wkt()}],"
        f"TARGETCRS[{CRS.from_user_input(target).to_wkt()}],"
        'METHOD["Cartesian Grid Offsets",ID["EPSG",9656]],'
        f'PARAMETER["Easting offset",{easting},LENGTHUNIT["metre",1],'
        'ID["EPSG",8728]],'
        f'PARAMETER["Northing offset",{northing},LENGTHUNIT["metre",1],'
        'ID["EPSG",8729]],'
        "OPERATIONACCURACY[0.01]]"
    )


def _site_to_ngo_ii() -> CoordinateOperation:
    return _similarity(
        SITE_GRID_NORTH_EAST,
        NGO_ZONE_II,
        SITE_ORIGIN_NORTHING,
        SITE_ORIGIN_EASTING,
        SITE_ROTATION_DEGREES,
    )


def _similarity_by_hand(
    first: float, second: float, parameters: dict[str, float]
) -> tuple[float, float]:
    """EPSG Guidance Note 7-2's Similarity transformation, worked by hand.

    ``XT = XT0 + XS M cos θ + YS M sin θ`` and ``YT = YT0 - XS M sin θ + YS M
    cos θ``, with the source ordinates in the order the source CRS declares
    them and the result in the order the operation's ordinates are stated.
    """
    theta = math.radians(parameters["Rotation angle of source CRS axes"])
    scale = parameters["Scale factor for source CRS axes"]
    return (
        parameters["Ordinate 1 of evaluation point in target CRS"]
        + first * scale * math.cos(theta)
        + second * scale * math.sin(theta),
        parameters["Ordinate 2 of evaluation point in target CRS"]
        - first * scale * math.sin(theta)
        + second * scale * math.cos(theta),
    )


def _registry_parameters(code: str) -> dict[str, float]:
    authority, number = code.split(":")
    operation = CoordinateOperation.from_authority(authority, number)
    return {parameter.name: float(parameter.value) for parameter in operation.params}


def _inside(bounds: tuple[float, float, float, float], lon: float, lat: float) -> bool:
    west, south, east, north = bounds
    return west <= lon <= east and south <= lat <= north


def _site_area() -> tuple[float, float, float, float]:
    area = CoordinateOperation.from_authority("EPSG", "1035").area_of_use
    assert area is not None
    return area.bounds


def test_epsg_1035_states_its_evaluation_point_easting_first() -> None:
    """The registry data behind the workaround's second part, as a fact.

    EPSG:1035 states the grid origin as (2610200.48, 4905282.73) in a CRS
    declared (northing, easting). Read in declared order the origin lies far
    outside the operation's area of use; read easting-first it lies inside.
    PROJ takes the declared reading. If this test ever fails, EPSG has
    restated the operation and ``_drop_spurious_ordinate_swap`` must be
    revisited.
    """
    parameters = _registry_parameters(ASTRA_TO_ARGENTINA_2)
    first = parameters["Ordinate 1 of evaluation point in target CRS"]
    second = parameters["Ordinate 2 of evaluation point in target CRS"]
    to_geographic = Transformer.from_crs(ARGENTINA_2, CAMPO_INCHAUSPE, always_xy=True)

    assert not _inside(_site_area(), *to_geographic.transform(second, first))
    assert _inside(_site_area(), *to_geographic.transform(first, second))


def test_proj_still_leaves_engineering_axes_alone() -> None:
    """Canary for the first part of the workaround.

    PROJ emits no ``axisswap`` for a northing-first engineering end under
    ``always_xy``, and exactly one for the northing-first projected end of
    EPSG:1035. When PROJ starts normalising engineering CRSs this fails, and
    ``_add_engineering_swaps`` must go rather than be adapted, or the swap
    would be applied twice.
    """
    utm = "EPSG:25832"  # declared (E, N): nothing for PROJ to swap
    into_utm = Transformer.from_pipeline(
        _similarity(SITE_GRID_NORTH_EAST, utm, 0.0, 0.0, 0.0).to_json(),
        always_xy=True,
    )
    assert "axisswap" not in into_utm.definition

    astra = Transformer.from_pipeline(
        f"urn:ogc:def:coordinateOperation:{ASTRA_TO_ARGENTINA_2.replace(':', '::')}",
        always_xy=True,
    )
    steps = astra.definition.split(" step ")
    assert steps[1].startswith("proj=affine")
    assert steps[2] == "proj=axisswap order=2,1"
    assert len(steps) == 3


def test_north_west_grid_into_north_east_projected_is_east_first() -> None:
    """The result is (easting, northing), verified by hand and on the map."""
    result = transform(
        ASTRA_MINAS, ARGENTINA_2, GRID_POINT, operation=ASTRA_TO_ARGENTINA_2
    )
    easting, northing = result.coordinates[0]

    # GN 7-2 by hand, with the ordinates taken easting-first as the registry
    # (see the fact test above) states them.
    assert (easting, northing) == pytest.approx(
        _similarity_by_hand(*GRID_POINT, _registry_parameters(ASTRA_TO_ARGENTINA_2)),
        abs=1e-3,
    )
    # And on the map: the point lies at the site, the transposed pair does not.
    to_geographic = Transformer.from_crs(ARGENTINA_2, CAMPO_INCHAUSPE, always_xy=True)
    assert _inside(_site_area(), *to_geographic.transform(easting, northing))
    assert not _inside(_site_area(), *to_geographic.transform(northing, easting))

    assert result.operation.authority_code == ASTRA_TO_ARGENTINA_2
    assert result.operation.route is OperationRoute.TRANSFORMER_GROUP
    assert result.target_crs.value_axis_abbreviations == ("Y", "X")
    frame = result.coordinates.to_dataframe()
    assert frame.loc[0, "Y"] == pytest.approx(easting)
    assert frame.loc[0, "X"] == pytest.approx(northing)


def test_north_west_grid_into_geographic_lands_on_site() -> None:
    """Chained through the projected CRS, the point still lands on the site.

    A grid point 10 km north and 20 km west of the origin lies about 22.36 km
    from it on the ground; Gauss-Kruger stretches that by well under a metre
    per kilometre this close to the central meridian.
    """
    result = transform(
        ASTRA_MINAS, CAMPO_INCHAUSPE, GRID_POINT, operation=ASTRA_TO_ARGENTINA_2
    )
    lon, lat = result.coordinates[0]
    assert result.operation.route is OperationRoute.CHAINED
    assert _inside(_site_area(), lon, lat)

    parameters = _registry_parameters(ASTRA_TO_ARGENTINA_2)
    origin_lon, origin_lat = Transformer.from_crs(
        ARGENTINA_2, CAMPO_INCHAUSPE, always_xy=True
    ).transform(
        parameters["Ordinate 1 of evaluation point in target CRS"],
        parameters["Ordinate 2 of evaluation point in target CRS"],
    )
    geod = CRS(CAMPO_INCHAUSPE).get_geod() or Geod(ellps="intl")
    _, _, distance = geod.inv(origin_lon, origin_lat, lon, lat)
    assert distance == pytest.approx(math.hypot(*GRID_POINT), abs=10.0)


@pytest.mark.parametrize("target", [ARGENTINA_2, CAMPO_INCHAUSPE])
def test_north_west_grid_round_trips(target: str) -> None:
    """Into the grid, PROJ's spurious swap is undone on the way in too."""
    forward = transform(ASTRA_MINAS, target, GRID_POINT, operation=ASTRA_TO_ARGENTINA_2)
    back = transform(
        target, ASTRA_MINAS, forward.coordinates[0], operation=ASTRA_TO_ARGENTINA_2
    )
    assert back.coordinates[0] == pytest.approx(GRID_POINT, abs=1e-6)


def test_north_east_site_grid_is_read_east_first() -> None:
    """A northing-first plant grid takes xy values, as every other CRS does.

    PROJ reads this grid in declared order; the package must not. The
    expected values are the register's reference points, and they also
    follow from GN 7-2 worked in declared order at both ends, which is the
    reading PROJ takes and the plausibility test confirms for this site.
    """
    operation = _site_to_ngo_ii()
    result = transform(
        SITE_GRID_NORTH_EAST, NGO_ZONE_II, SITE_POINTS_XY, operation=operation
    )

    assert result.coordinates.to_numpy() == pytest.approx(
        np.array(SITE_EXPECTED_XY), abs=1e-3
    )
    parameters = {
        parameter.name: float(parameter.value) for parameter in operation.params
    }
    for (easting, northing), (expected_e, expected_n) in zip(
        SITE_POINTS_XY, SITE_EXPECTED_XY, strict=True
    ):
        by_hand_n, by_hand_e = _similarity_by_hand(northing, easting, parameters)
        assert (by_hand_e, by_hand_n) == pytest.approx(
            (expected_e, expected_n), abs=1e-3
        )
    assert result.source_crs.value_axis_abbreviations == ("E", "N")


def test_north_east_site_grid_is_written_east_first() -> None:
    """Into the plant grid, the result comes back xy as well."""
    result = transform(
        NGO_ZONE_II, SITE_GRID_NORTH_EAST, SITE_EXPECTED_XY, operation=_site_to_ngo_ii()
    )
    assert result.coordinates.to_numpy() == pytest.approx(
        np.array(SITE_POINTS_XY), abs=1e-3
    )
    assert result.coordinate_order == "xy"
    assert list(result.coordinates.to_dataframe().columns) == ["E", "N"]


def test_north_east_site_grid_chained_from_geographic() -> None:
    """The correction survives being chained behind a map projection."""
    to_geographic = Transformer.from_crs(NGO_ZONE_II, NGO_1948_OSLO, always_xy=True)
    geographic = [to_geographic.transform(*point) for point in SITE_EXPECTED_XY]

    result = transform(
        NGO_1948_OSLO,
        SITE_GRID_NORTH_EAST,
        geographic,
        operation=_site_to_ngo_ii(),
    )

    assert result.operation.route is OperationRoute.CHAINED
    assert result.coordinates.to_numpy() == pytest.approx(
        np.array(SITE_POINTS_XY), abs=1e-3
    )


def test_grid_offsets_at_a_north_east_site_grid_stay_east_first() -> None:
    """Cartesian Grid Offsets need no correction, and must not get one.

    PROJ renders EPSG:9656 as an east-first affine and does not adapt an
    engineering end to it, so the xy values this package hands over are
    already what the step consumes. Adding a swap here would break the
    grids that work today.
    """
    result = transform(
        SITE_GRID_NORTH_EAST,
        NGO_ZONE_I,
        (1932.010, -5497.267),
        operation=_grid_offsets(SITE_GRID_NORTH_EAST, NGO_ZONE_I, -60000.0, 290000.0),
    )
    assert result.coordinates[0] == pytest.approx((-58067.990, 284502.733), abs=1e-6)


def test_east_north_site_grid_needs_no_correction() -> None:
    """A plant grid declared (E, N) is read as PROJ reads it: unchanged."""
    grid = SITE_GRID_NORTH_EAST.replace(
        '"Plant grid (N,E)"', '"Plant grid (E,N)"'
    ).replace(
        'AXIS["northing (N)",north,ORDER[1]],AXIS["easting (E)",east,ORDER[2]]',
        'AXIS["easting (E)",east,ORDER[1]],AXIS["northing (N)",north,ORDER[2]]',
    )
    operation = _similarity(
        grid,
        NGO_ZONE_II,
        SITE_ORIGIN_NORTHING,
        SITE_ORIGIN_EASTING,
        SITE_ROTATION_DEGREES,
    )
    parameters = {
        parameter.name: float(parameter.value) for parameter in operation.params
    }
    # PROJ reads the source in declared order, here (E, N), so GN 7-2 by hand
    # takes the easting as its first ordinate.
    by_hand_n, by_hand_e = _similarity_by_hand(7700.0, 7500.0, parameters)

    result = transform(grid, NGO_ZONE_II, (7700.0, 7500.0), operation=operation)

    assert result.coordinates[0] == pytest.approx((by_hand_e, by_hand_n), abs=1e-6)
    # No swap was added at the grid end; the one PROJ appends for the
    # northing-first projected end is legitimately still there.
    assert result.pipeline is not None
    assert not result.pipeline.startswith("proj=pipeline step proj=axisswap")


@pytest.mark.parametrize(
    ("source", "target", "points"),
    [
        (ASTRA_MINAS, ARGENTINA_2, [GRID_POINT]),
        (ARGENTINA_2, ASTRA_MINAS, [(2590394.630, 4915661.955)]),
        (SITE_GRID_NORTH_EAST, NGO_ZONE_II, SITE_POINTS_XY),
        (NGO_ZONE_II, SITE_GRID_NORTH_EAST, SITE_EXPECTED_XY),
    ],
)
def test_reported_pipeline_replays_from_xy_values(
    source: str, target: str, points: list[tuple[float, float]]
) -> None:
    """The corrected pipeline is what ran, so replaying it reproduces the result."""
    operation = (
        ASTRA_TO_ARGENTINA_2 if ASTRA_MINAS in (source, target) else _site_to_ngo_ii()
    )
    result = transform(source, target, points, operation=operation)
    assert result.pipeline is not None

    replayed = Transformer.from_pipeline(result.pipeline).transform(
        [point[0] for point in points], [point[1] for point in points]
    )
    assert np.column_stack(replayed) == pytest.approx(
        result.coordinates.to_numpy(), abs=1e-6
    )


def test_undecidable_ordinate_order_is_refused() -> None:
    """When neither reading of the ordinates is plausible, no number is given.

    An evaluation point at the false origin of Argentina 2 is nowhere near
    Comodoro Rivadavia whichever axis each ordinate is taken to be, so the
    convention cannot be established from the data and the operation is
    refused rather than run under a guess.
    """
    operation = _similarity(ASTRA_MINAS, ARGENTINA_2, 0.0, 0.0, 0.0)
    with pytest.raises(OperationNotAvailableError, match="evaluation point"):
        Transformation(ASTRA_MINAS, ARGENTINA_2, operation=operation)


def test_similarity_into_an_east_first_projected_crs_is_untouched() -> None:
    """Nothing to decide when the projected end declares its easting first.

    EPSG:15747 takes the Tombak plant grid, whose axes are north-east and
    north-west, into UTM zone 39N. Neither end has a swap to make or to
    remove, so the answer is PROJ's own, and the grid origin lands on the
    stated evaluation point.
    """
    parameters = _registry_parameters("EPSG:15747")
    result = transform("EPSG:5817", "EPSG:3307", (0.0, 0.0), operation="EPSG:15747")
    assert result.coordinates[0] == pytest.approx(
        (
            parameters["Ordinate 1 of evaluation point in target CRS"],
            parameters["Ordinate 2 of evaluation point in target CRS"],
        ),
        abs=1e-6,
    )


@pytest.mark.parametrize(
    ("code", "abbreviations"),
    [
        ("EPSG:5800", ("X", "Y")),  # north, west
        ("EPSG:5817", ("x", "y")),  # north-east, north-west
        ("EPSG:5513", ("X", "Y")),  # south, west (Krovak)
        ("EPSG:2065", ("X", "Y")),  # south, west (Krovak, Ferro)
    ],
)
def test_a_crs_without_an_easting_keeps_its_declared_order(
    code: str, abbreviations: tuple[str, str]
) -> None:
    """No east/north pair to order means declared order, and says so."""
    crs = CoordinateReferenceSystem.from_user_input(code)
    assert crs.value_axis_order == (0, 1)
    assert crs.value_axis_abbreviations == abbreviations == crs.axis_abbreviations
