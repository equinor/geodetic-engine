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
    Transformation,
    TransformationFailedError,
    available_operations,
)

ED50_geog2D = "EPSG:4230" # ED50 Geographic 2D CRS.
WGS84_geog2D = "EPSG:4326" # WGS 84 Geographic 2D CRS.


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
    transformation = Transformation(source_crs=ED50_geog2D, target_crs=WGS84_geog2D, operation="EPSG:1612")

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
        source_crs=ED50_geog2D, target_crs=WGS84_geog2D, operation=["EPSG:1147", "EPSG:1146"]
    )
    collapsed = Transformation(source_crs=ED50_geog2D, target_crs=WGS84_geog2D, operation="EPSG:8047")

    forward_result = chained.transform(*point)
    assert forward_result.coordinates == collapsed.transform(*point).coordinates

    reverse = Transformation(
        source_crs=WGS84_geog2D, target_crs=ED50_geog2D, operation=["EPSG:1147", "EPSG:1146"]
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
        source_crs=ED50_geog2D, target_crs=WGS84_geog2D, operation=["EPSG:1147", "EPSG:1146"]
    )
    reverse_order = Transformation(
        source_crs=ED50_geog2D, target_crs=WGS84_geog2D, operation=["EPSG:1146", "EPSG:1147"]
    )

    assert forward_order.transform(*point).coordinates == reverse_order.transform(
        *point
    ).coordinates



def test_available_operations_area_of_use_carries_the_bounding_box() -> None:
    """area_of_use is the bounding box PROJ computed, not just its name.

    Matches pyproj's own ``TransformerGroup`` output for the same pair, since
    that is exactly where the box comes from.
    """
    from pyproj.transformer import TransformerGroup

    candidate = available_operations(ED50_geog2D, WGS84_geog2D)[0]
    expected = (
        TransformerGroup(ED50_geog2D, WGS84_geog2D).transformers[0].area_of_use
    )

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
    """Passing a candidate object matches naming its own reference explicitly.

    Every usable candidate ``available_operations()`` offers for a pair must
    resolve, and transform, identically whether given as the candidate object
    itself or as its own authority code -- or its name, for the rare
    candidate with no code, since passing the object is just shorthand that
    parses down to one of those two. A ballpark or grid-missing candidate is
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
            operation=candidate.authority_code or candidate.name,
        )
        assert by_candidate.operation.authority_code == by_reference.operation.authority_code
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