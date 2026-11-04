"""Transformations shared by conversions that resolve the same one again."""

from __future__ import annotations

from typing import Any

from pyproj import CRS
from pyproj.crs import CoordinateOperation

from geodetic_engine.geodesy import CoordinateReferenceSystem
from geodetic_engine.geodesy._shared import shared_transformation


def _crs(value: Any) -> CoordinateReferenceSystem:
    return CoordinateReferenceSystem.from_user_input(value)


def test_the_same_crss_and_operation_are_resolved_once() -> None:
    ed50, utm = _crs("EPSG:4230"), _crs("EPSG:32631")
    first = shared_transformation(ed50, utm, "EPSG:1612")
    assert first.operation.authority_code == "EPSG:1612"
    assert shared_transformation(ed50, utm, ["EPSG:1612"]) is first
    assert shared_transformation(_crs("EPSG:4230"), utm, "EPSG:1612") is first
    assert shared_transformation(ed50, _crs("EPSG:32632"), "EPSG:1612") is not first


def test_crss_of_one_definition_are_told_apart_by_what_they_are() -> None:
    # A CRS built rather than read, by compound() say, is defined by its name.
    zone_31 = CoordinateReferenceSystem(CRS.from_epsg(32631), "UTM")
    zone_32 = CoordinateReferenceSystem(CRS.from_epsg(32632), "UTM")
    to_31 = shared_transformation(_crs("OGC:CRS84"), zone_31)
    to_32 = shared_transformation(_crs("OGC:CRS84"), zone_32)
    assert to_31 is not to_32
    assert to_32.target_crs.crs.to_epsg() == 32632


def test_a_stated_operation_is_resolved_every_time() -> None:
    stated = CoordinateOperation.from_authority("EPSG", 1612)
    ed50, utm = _crs("EPSG:4230"), _crs("EPSG:32631")
    first = shared_transformation(ed50, utm, stated)
    assert shared_transformation(ed50, utm, stated) is not first
