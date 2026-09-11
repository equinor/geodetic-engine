"""A CRS reports the axis roles and units the EPSG dataset declares for it."""

from __future__ import annotations

import pytest

from geodetic_engine.geodesy import CoordinateReferenceSystem, UnresolvableCRSError


def test_epsg_4326_is_latitude_first() -> None:
    """EPSG declares latitude before longitude, and that is what is reported."""
    crs = CoordinateReferenceSystem.from_user_input("EPSG:4326")
    assert crs.axis_abbreviations == ("Lat", "Lon")
    assert crs.axis_units == ("degree", "degree")
    assert crs.dimension == 2
    assert crs.authority_code == "EPSG:4326"


def test_axis_roles_are_described_in_full() -> None:
    """Each axis carries its name, direction, unit and conversion factor."""
    crs = CoordinateReferenceSystem.from_user_input("EPSG:4326")
    latitude = crs.axes[0]
    assert latitude.name == "Geodetic latitude"
    assert latitude.abbrev == "Lat"
    assert latitude.direction == "north"
    assert latitude.unit_name == "degree"
    assert latitude.unit_conversion_factor == pytest.approx(0.017453292519943295)


def test_projected_crs_reports_metres() -> None:
    """A projected CRS reports linear axes and their unit."""
    crs = CoordinateReferenceSystem.from_user_input("EPSG:25832")
    assert crs.axis_abbreviations == ("E", "N")
    assert crs.axis_units == ("metre", "metre")


def test_non_metre_unit_is_reported_as_declared() -> None:
    """A CRS in feet says so rather than being normalised to metres."""
    crs = CoordinateReferenceSystem.from_user_input("EPSG:2225")
    assert crs.axis_units[0] != "metre"
    assert crs.axes[0].unit_conversion_factor != 1.0


def test_vertical_crs_has_one_axis() -> None:
    """A vertical CRS declares a single height axis."""
    crs = CoordinateReferenceSystem.from_user_input("EPSG:3855")
    assert crs.dimension == 1
    assert crs.axis_abbreviations == ("H",)
    assert crs.axes[0].direction == "up"


def test_compound_crs_reports_all_axes() -> None:
    """A compound CRS reports its horizontal and vertical axes together."""
    crs = CoordinateReferenceSystem.from_user_input("EPSG:7415")
    assert crs.dimension == 3
    assert crs.axis_units[2] == "metre"


def test_dynamic_frame_is_recognised() -> None:
    """A dynamic reference frame is distinguished from a static one."""
    assert CoordinateReferenceSystem.from_user_input("EPSG:4896").is_dynamic
    assert not CoordinateReferenceSystem.from_user_input("EPSG:4326").is_dynamic


def test_resolution_is_cached() -> None:
    """The same definition resolves to the same object rather than rebuilding."""
    first = CoordinateReferenceSystem.from_user_input("EPSG:4326")
    second = CoordinateReferenceSystem.from_user_input("EPSG:4326")
    assert first is second


def test_accepts_an_integer_code() -> None:
    """A bare EPSG code is accepted and normalised."""
    assert CoordinateReferenceSystem.from_user_input(4326).authority_code == "EPSG:4326"


def test_accepts_wkt() -> None:
    """A CRS can be given as WKT rather than an authority code."""
    wkt = CoordinateReferenceSystem.from_user_input("EPSG:4326").crs.to_wkt()
    assert CoordinateReferenceSystem.from_user_input(wkt).axis_abbreviations == (
        "Lat",
        "Lon",
    )


def test_bad_definition_raises_our_own_error() -> None:
    """PROJ's error is wrapped rather than leaking through unannotated."""
    with pytest.raises(UnresolvableCRSError, match="could not resolve"):
        CoordinateReferenceSystem.from_user_input("EPSG:this-is-not-a-crs")
