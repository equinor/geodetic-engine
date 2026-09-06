"""Worked examples and edge cases that seemed worth pinning down
permanently. Add to this file whenever a real usage pattern, a
surprising edge case, or an example deserves a permanent regression
test. There is no scheme to follow beyond the pattern already here:

Keep each test self-contained (no shared fixtures beyond what is imported
below) so this file reads as a flat catalogue, not a suite that has to be
understood as a whole.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path

import pyproj
import pytest

from geodetic_engine.geodesy import (
    OperationRequest,
    Transformation,
    TransformationFailedError,
    available_operations,
)

ED50_geog2D = "EPSG:4230"  # ED50 Geographic 2D CRS.
WGS84_geog2D = "EPSG:4326"  # WGS 84 Geographic 2D CRS.


def _find_build_proj_db() -> Path | None:
    """This repo's own built proj.db, which registers OSDU: CRSs, if present."""
    for parent in Path(__file__).resolve().parents:
        candidate = parent / "build" / "proj.db"
        if candidate.is_file():
            return candidate.parent
    return None


@pytest.fixture
def osdu_registered() -> Iterator[None]:
    """
    OSDU CRSs live in this repo's own built database, not the stock PROJ one,
    so it is searched first for the duration of the test and the search path
    is restored afterwards. Skips rather than fails when that database has
    not been built yet (see ``scripts/build-projdb.sh``).
    """
    build_dir = _find_build_proj_db()
    if build_dir is None:
        pytest.skip("build/proj.db not found; run scripts/build-projdb.sh first")

    previous = pyproj.datadir.get_data_dir()
    pyproj.datadir.set_data_dir(f"{build_dir}{os.pathsep}{previous}")
    _clear_crs_cache()
    try:
        yield
    finally:
        pyproj.datadir.set_data_dir(previous)
        _clear_crs_cache()


def _clear_crs_cache() -> None:
    from geodetic_engine.geodesy import crs as crs_module

    crs_module._cached.cache_clear()


def test_1_1_ed50_to_wgs84_via_explicit_operation() -> None:
    """A single named operation, no datum ambiguity possible."""
    transformation = Transformation(
        source_crs=ED50_geog2D, target_crs=WGS84_geog2D, operation="EPSG:1612"
    )

    result = transformation.transform(10, 60, 100)

    assert result.operation.authority_code == "EPSG:1612"
    assert result.coordinates[0] == pytest.approx(
        (9.9986067850383, 59.99955456615287, 100.0), abs=1e-9
    )


def test_1_2_osdu_bound_crs_round_trip_through_utm(osdu_registered: None) -> None:
    """An OSDU bound CRS's own declared datum shift, round-tripped."""
    utm32n = "EPSG:32632"

    forward = Transformation(source_crs="OSDU:4230023", target_crs=utm32n)
    east, north, height = forward.transform(10, 60, 100).coordinates[0]
    assert (east, north, height) == pytest.approx(
        (555699.3111382048, 6651781.958444249, 100.0), abs=1e-6
    )

    reverse = Transformation(source_crs=utm32n, target_crs="OSDU:4230023")
    lon, lat, height_back = reverse.transform(east, north, height).coordinates[0]
    assert (lon, lat, height_back) == pytest.approx((10.0, 60.0, 100.0), abs=1e-6)


def test_1_2_an_unregistered_chain_names_every_operation_it_applies(
    osdu_registered: None,
) -> None:
    """A chain PROJ assembled itself must not claim one step's code as its own.

    A bound CRS on both sides of the pair leaves PROJ no published operation
    spanning it: it applies the source's shift to the WGS 84 hub and then the
    inverse of the target's. Reporting the first step's code as the
    candidate's own would understate that by exactly one datum shift, so an
    unregistered chain carries no code and names both steps instead.
    """
    candidates = available_operations("OSDU:4230024", "OSDU:4258001")
    unregistered = [c for c in candidates if c.is_chained and c.authority_code is None]
    assert unregistered, "expected an unregistered chain between two bound CRSs"

    for candidate in unregistered:
        assert candidate.method_name is None
        assert len(candidate.references) == len(candidate.steps) > 1
        assert candidate.references == tuple(
            step.authority_code for step in candidate.steps
        )
        assert candidate.name == " + ".join(step.name for step in candidate.steps)


def test_1_2_a_transformed_result_names_every_operation_it_applied(
    osdu_registered: None,
) -> None:
    """The applied operation must not claim one step's code either.

    The same understatement as the candidate case, on the result side: with a
    bound CRS on each side and nothing named by the caller, the source's own
    declared shift identifies the pipeline but a second shift is applied
    alongside it. Reporting EPSG:1613 here would describe half the
    transformation, and its WKT would look complete while computing something
    about 1.4 m away.
    """
    result = Transformation("OSDU:4230024", "Equinor:1100177").transform(10, 60)
    applied = result.operation

    assert applied.authority_code is None
    assert applied.method_name is None
    assert applied.name == ("ED50 to WGS 84 (24) + Inverse of ST_ETRS89_WGS84_T3000034")
    assert {"ED50 to WGS 84 (24)", "Inverse of ST_ETRS89_WGS84_T3000034"} <= set(
        applied.steps
    )
    # Both Helmerts really are applied, and the second one inverted.
    assert result.pipeline.count("proj=helmert") == 2
    assert "step inv proj=helmert" in result.pipeline


def test_1_2_a_registered_concatenated_operation_keeps_its_own_code() -> None:
    """EPSG:8047 publishes a two-step chain under one code, and must report it.

    The authority defines the concatenation itself, so unlike a chain PROJ
    assembled there is a single code that names the whole of it. Its steps are
    still listed, but naming EPSG:8047 alone pins the operation down and no
    method is claimed, since two are applied.
    """
    candidate = next(
        c for c in available_operations(ED50_geog2D, WGS84_geog2D) if c.code == "8047"
    )

    assert candidate.authority_code == "EPSG:8047"
    assert candidate.name == "ED50 to WGS 84 (15)"
    assert candidate.is_chained
    assert candidate.method_name is None
    assert candidate.references == ("EPSG:8047",)
    assert [step.authority_code for step in candidate.steps] == [
        "EPSG:1147",
        "EPSG:1146",
    ]


def test_1_3_chained_operations_match_their_collapsed_equivalent() -> None:
    """EPSG:8047 is a concatenated operation consisting of EPSG:1147 followed by EPSG:1146.

    EPSG:8047 ("ED50 to WGS 84 (15)") is the single, published, superseded
    concatenation of exactly these two steps (ED50 -> ED87 -> WGS 84). Naming
    the two steps individually and naming the one code they collapse into
    must land on the same coordinates, and the chained form must round-trip
    back to the original point in reverse.
    """
    point = [4.12789451, 63.58496782, 100]

    chained = Transformation(
        source_crs=ED50_geog2D,
        target_crs=WGS84_geog2D,
        operation=["EPSG:1147", "EPSG:1146"],
    )
    collapsed = Transformation(
        source_crs=ED50_geog2D, target_crs=WGS84_geog2D, operation="EPSG:8047"
    )

    forward_result = chained.transform(*point)
    assert forward_result.coordinates == collapsed.transform(*point).coordinates

    reverse = Transformation(
        source_crs=WGS84_geog2D,
        target_crs=ED50_geog2D,
        operation=["EPSG:1147", "EPSG:1146"],
    )
    round_tripped = reverse.transform(*forward_result.coordinates[0])
    assert round_tripped.coordinates[0] == pytest.approx(tuple(point), abs=1e-6)


def test_1_3_operation_list_order_does_not_matter() -> None:
    """Naming several operations is a set, not a sequence.

    ``operation=[...]`` only has to name every operation PROJ must apply; it
    does not have to name them in the order PROJ applies them in. Each entry
    is checked for independently against whatever pipeline PROJ built, so
    ``["EPSG:1147", "EPSG:1146"]`` and ``["EPSG:1146", "EPSG:1147"]`` resolve
    to the exact same pipeline and produce identical coordinates.
    """
    point = [4.12789451, 63.58496782, 100]

    forward_order = Transformation(
        source_crs=ED50_geog2D,
        target_crs=WGS84_geog2D,
        operation=["EPSG:1147", "EPSG:1146"],
    )
    reverse_order = Transformation(
        source_crs=ED50_geog2D,
        target_crs=WGS84_geog2D,
        operation=["EPSG:1146", "EPSG:1147"],
    )

    assert (
        forward_order.transform(*point).coordinates
        == reverse_order.transform(*point).coordinates
    )


def test_available_operations_area_of_use_carries_the_bounding_box() -> None:
    """area_of_use is the bounding box PROJ computed, not just its name.

    Matches pyproj's own ``TransformerGroup`` output for the same pair, since
    that is exactly where the box comes from.
    """
    from pyproj.transformer import TransformerGroup

    candidate = available_operations(ED50_geog2D, WGS84_geog2D)[0]
    expected = TransformerGroup(ED50_geog2D, WGS84_geog2D).transformers[0].area_of_use

    area = candidate.area_of_use
    assert area is not None
    assert area.bounds == (
        expected.west,
        expected.south,
        expected.east,
        expected.north,
    )
    assert str(area) == expected.name


def test_available_operations() -> None:
    """Passing a candidate object matches naming its own references explicitly.

    Every usable candidate ``available_operations()`` offers for a pair must
    resolve, and transform, identically whether given as the candidate object
    itself or as its
    :attr:`~geodetic_engine.geodesy.operation.OperationCandidate.references`,
    since passing the object is just shorthand that expands to exactly those.
    A chained candidate has more than one, and no single code or name that
    could stand for the whole of it. A ballpark or grid-missing candidate is
    skipped rather than compared: ``usable`` is False for exactly the ones
    ``Transformation`` refuses outright, on either side, so there is nothing
    to compare there.
    """

    for candidate in available_operations(ED50_geog2D, WGS84_geog2D):
        if not candidate.usable or candidate.area_of_use is None:
            continue

        # The centre of the area of use, not a corner
        west, south, east, north = candidate.area_of_use.bounds
        point = ((west + east) / 2, (south + north) / 2, 100)

        by_candidate = Transformation(
            source_crs=ED50_geog2D, target_crs=WGS84_geog2D, operation=candidate
        )
        by_reference = Transformation(
            source_crs=ED50_geog2D,
            target_crs=WGS84_geog2D,
            operation=candidate.references,
        )
        assert (
            by_candidate.operation.authority_code
            == by_reference.operation.authority_code
        )
        assert by_candidate.operation.name == by_reference.operation.name

        # A regional operation refuses a point outside the area its grid
        # covers -- both sides run the identical pipeline, so they must
        # refuse identically rather than one succeeding.
        try:
            expected = by_reference.transform(*point).coordinates[0]
        except TransformationFailedError:
            with pytest.raises(TransformationFailedError):
                by_candidate.transform(*point)
            continue

        assert by_candidate.transform(*point).coordinates[0] == pytest.approx(
            expected, abs=1e-8
        )


def test_an_operation_authority_keeps_the_spelling_proj_registered_it_under() -> None:
    """A custom authority must not be uppercased on its way to PROJ.

    PROJ matches authority names case-sensitively, so rewriting a request for
    ``Equinor:3000034`` as ``EQUINOR::3000034`` makes the URN unresolvable and
    every operation published by an authority whose registered name is not
    uppercase unreachable. Known authorities resolve to the spelling proj.db
    stores; an unknown one is left exactly as the caller wrote it.
    """
    assert OperationRequest.parse("epsg:1612").auth_name == "EPSG"
    assert OperationRequest.parse("ESRI:1234").auth_name == "ESRI"
    assert OperationRequest.parse("MixedCase:1234").auth_name == "MixedCase"
    assert (
        OperationRequest.parse("MixedCase:1234").urn
        == "urn:ogc:def:coordinateOperation:MixedCase::1234"
    )


# A vertical CRS declares one axis, so a point in it is a bare height. The
# shift applied to that height is usually read at a horizontal position, which
# therefore has to travel with it even though the CRS declares no axis for it.
NZVD2009 = "EPSG:4440"  # NZVD2009 height.
AUCKLAND_1946 = "EPSG:5759"  # Auckland 1946 height.
NZVD2009_TO_AUCKLAND = "EPSG:4442"  # Vertical Offset of +0.34 m, no grid.


def test_a_vertical_crs_accepts_the_position_its_shift_is_read_at() -> None:
    """A height may be given with the horizontal position that locates it.

    A vertical datum shift is generally only defined where its grid is read,
    so refusing the position for being one value more than the CRS declares
    would make every vertical source unusable. The result is still the single
    axis the target declares.
    """
    transformation = Transformation(
        NZVD2009, AUCKLAND_1946, operation=NZVD2009_TO_AUCKLAND
    )

    result = transformation.transform([[174.76, -36.85, 25.0]])

    assert result.operation.authority_code == NZVD2009_TO_AUCKLAND
    assert result.target_axes == ("H",)
    assert result.coordinates[0] == pytest.approx((25.34,), abs=1e-9)


def test_a_constant_vertical_shift_never_reads_the_position_as_a_latitude() -> None:
    """A position a constant shift cannot read is carried, not interpreted.

    The offset here is the same everywhere, so the accompanying horizontal
    values are not part of the calculation and need not be geographic -- they
    are commonly engineering or projected coordinates. Offering them to PROJ
    as a latitude and longitude would have it reject a perfectly good point
    for an impossible latitude, so they are withheld instead, and the height
    comes back shifted by exactly the same offset either way.
    """
    transformation = Transformation(
        NZVD2009, AUCKLAND_1946, operation=NZVD2009_TO_AUCKLAND
    )

    geographic = transformation.transform([[174.76, -36.85, 25.0]])
    projected = transformation.transform([[1757000.0, 5921000.0, 25.0]])

    assert projected.coordinates == geographic.coordinates
    assert projected.coordinates[0] == pytest.approx((25.34,), abs=1e-9)


# Norway's NN54 height, and the two ETRS89 variants that reach it: plain
# ETRS89 and ETRS89-NOR, its Norway-only-extent restriction of the same datum.
# The grid this operation reads (href2008a.bin -> no_kv_href2008a.tif) ships
# with a stock PROJ install, unlike the grids in the two cases below it.
ETRS89_geog3D = "EPSG:4937"  # ETRS89 Geographic 3D.
ETRS89_NOR_geog3D = "EPSG:10874"  # ETRS89-NOR Geographic 3D.
NN54_HEIGHT = "EPSG:5776"  # NN54 height.
UTM32N_NN54_HEIGHT = "EPSG:6172"  # ETRS89-NOR / UTM zone 32N + NN54 height.
UTM32N_NN2000_HEIGHT = "EPSG:5972"  # ETRS89-NOR / UTM zone 32N + NN2000 height.
ETRS89_TO_NN54 = "EPSG:9484"  # ETRS89-NOR to NN54 height (1).


def test_a_named_vertical_operation_reaches_a_compound_target() -> None:
    """A vertical operation composes with the conversion PROJ adds for a compound target.

    EPSG:9484 reads the NN54 geoid grid at a geographic position; naming it
    against the *compound* UTM+height target still reaches it, with PROJ's own
    UTM conversion applied around it. The composite pipeline has no authority
    code of its own -- only the named vertical operation inside it does.
    """
    transformation = Transformation(
        ETRS89_geog3D, UTM32N_NN54_HEIGHT, operation=ETRS89_TO_NN54
    )

    result = transformation.transform([[11.12789451, 63.58496782, 100]])

    assert result.operation.authority_code is None
    assert result.coordinates[0] == pytest.approx(
        (605606.253, 7052523.904, 61.7415), abs=1e-3
    )


def test_the_same_operation_against_a_vertical_only_target_agrees_on_height() -> None:
    """The height alone, from the same operation, matches the compound case above.

    Confirms that height is not an artefact of the UTM conversion above: asked
    for the vertical CRS alone through the same operation, it agrees to the
    operation's own precision.
    """
    transformation = Transformation(
        ETRS89_geog3D, NN54_HEIGHT, operation=ETRS89_TO_NN54
    )

    result = transformation.transform([[10.65894583, 60.93562145, 62.458]])

    assert result.operation.authority_code == ETRS89_TO_NN54
    assert result.coordinates[0] == pytest.approx((23.5988,), abs=1e-3)


def test_etrs89_nor_reaches_the_same_operation_as_plain_etrs89() -> None:
    """ETRS89-NOR is ETRS89 restricted to Norway's extent, not a different datum.

    The compound-to-geographic-3D reverse of the first case above, but named
    from ETRS89-NOR: the same operation applies, and round-trips the original
    point back out.
    """
    transformation = Transformation(
        UTM32N_NN54_HEIGHT, ETRS89_NOR_geog3D, operation=ETRS89_TO_NN54
    )

    result = transformation.transform([[605606.253, 7052523.904, 61.742]])

    assert result.coordinates[0] == pytest.approx(
        (11.1278945, 63.5849678, 100.0005), abs=1e-3
    )


def test_proj_finds_its_own_path_when_two_named_operations_cannot_be_chained() -> None:
    """Letting PROJ choose succeeds exactly where naming both operations by hand fails.

    NN2000-height compound to NN54-height compound has no single registered
    operation spanning it; naming both EPSG:9485 and EPSG:9484 explicitly is
    refused, because chaining two named operations by hand is not supported
    (see ``proj_issues.md`` and ``tests/local_tests/failing_local_test.md``,
    cause C). Left to search freely, PROJ finds its own equivalent composite
    path and produces the same number that naming both operations would have.
    """
    transformation = Transformation(
        UTM32N_NN2000_HEIGHT, UTM32N_NN54_HEIGHT, allow_any_operation=True
    )

    result = transformation.transform([[621786.686, 7049822.720, 88.454]])

    assert result.operation.authority_code is None
    assert result.coordinates[0] == pytest.approx(
        (621786.686, 7049822.720, 88.2847), abs=1e-3
    )


# WGS 84 to EGM2008 height: the 2.5' grid, which ships with a stock PROJ
# install. The 1' grid (EPSG:3859) needs the opt-in grid file in local_grids/
# (see scripts/patch-grid-alternatives.sh) and is deliberately not exercised
# here, so this file runs unchanged in an environment that never set that up.
WGS84_geog3D = "EPSG:4979"  # WGS 84 Geographic 3D.
WGS84_EGM2008_HEIGHT = "EPSG:9518"  # WGS 84 + EGM2008 height.
WGS84_TO_EGM2008_25 = "EPSG:3858"  # WGS 84 to EGM2008 height (1), 2.5' grid.


def test_egm2008_25_minute_operation_agrees_forward_reverse_and_vertical_only() -> None:
    """Compound forward, compound reverse, and the vertical-only target, all agree.

    Three ways of asking the same 2.5' EGM2008 operation for the same height
    must produce the same number: forward through the compound CRS, backward
    from the same expected point, and forward again against the vertical CRS
    alone rather than the compound one.
    """
    point = (-33.246545678, 56.41950283, 167.467)
    expected_height = 106.0259

    forward = Transformation(
        WGS84_geog3D, WGS84_EGM2008_HEIGHT, operation=WGS84_TO_EGM2008_25
    )
    assert forward.transform([point]).coordinates[0] == pytest.approx(
        (point[0], point[1], expected_height), abs=1e-3
    )

    reverse = Transformation(
        WGS84_EGM2008_HEIGHT, WGS84_geog3D, operation=WGS84_TO_EGM2008_25
    )
    reverse_point = (point[0], point[1], expected_height)
    assert reverse.transform([reverse_point]).coordinates[0] == pytest.approx(
        point, abs=1e-3
    )

    vertical_only = Transformation(
        WGS84_geog3D, "EPSG:3855", operation=WGS84_TO_EGM2008_25
    )
    assert vertical_only.transform([point]).coordinates[0] == pytest.approx(
        (expected_height,), abs=1e-3
    )
