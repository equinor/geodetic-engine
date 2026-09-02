"""The wrapper's declared axis metadata and its value order are both correct.

Two separate claims are checked here, and keeping them apart is the point:

* The **declared** axis order and units are what the EPSG dataset says, and are
  not altered by this package's ``xy`` value convention.
* The **value** order matches what PROJ itself does under ``always_xy``, which
  is measured rather than assumed. Getting this wrong transposes coordinates
  without any error being raised, so it is checked against awkward CRSs rather
  than only the easy ones.
"""

from __future__ import annotations

import pytest
from pyproj import CRS, Transformer

from geodetic_engine.geodesy import CoordinateReferenceSystem

# CRSs whose axis order is not the obvious one. The polar pair matters most:
# both of their axes carry the same direction, so direction alone cannot say
# which is the easting and the abbreviation has to settle it.
AWKWARD = [
    "EPSG:4326",  # geographic, latitude first
    "EPSG:4979",  # geographic 3D, latitude first
    "EPSG:25832",  # projected, easting first
    "EPSG:2207",  # Gauss-Kruger, X is the northing
    "EPSG:3035",  # LAEA Europe, Y is the northing
    "EPSG:32661",  # UPS North, both axes point south
    "EPSG:32761",  # UPS South, both axes point north
    "EPSG:3031",  # Antarctic Polar Stereographic, both axes point north
    "EPSG:3032",  # Australian Antarctic Polar Stereographic
    "EPSG:2049",  # Hartebeesthoek94 Lo29, west and south
    "EPSG:22277",  # Cape Lo27, west and south
    "EPSG:4896",  # geocentric, X/Y/Z
]


@pytest.mark.parametrize("code", AWKWARD)
def test_value_order_matches_proj(code: str) -> None:
    """The reported value order is the one PROJ uses under always_xy.

    Transforms the same point with ``always_xy`` on and off. PROJ's two answers
    differ exactly when it reorders the axes, which is what
    ``value_axis_order`` must predict.
    """
    crs = CoordinateReferenceSystem.from_user_input(code)
    latitude, longitude = _probe_point(code)
    # A 3D target needs a height to come back with three components to reorder.
    origin = CRS("EPSG:4979" if crs.dimension > 2 else "EPSG:4326")
    xy_input = (
        (longitude, latitude, 0.0) if crs.dimension > 2 else (longitude, latitude)
    )
    declared_input = (
        (latitude, longitude, 0.0) if crs.dimension > 2 else (latitude, longitude)
    )

    normalized = Transformer.from_crs(origin, crs.crs, always_xy=True).transform(
        *xy_input
    )
    declared = Transformer.from_crs(origin, crs.crs, always_xy=False).transform(
        *declared_input
    )

    reordered = tuple(declared[index] for index in crs.value_axis_order)
    assert reordered == pytest.approx(normalized), (
        f"{code} declares {crs.axis_abbreviations} and this package reports "
        f"value order {crs.value_axis_order}, which does not reproduce PROJ's "
        "always_xy output"
    )


def test_declared_order_is_epsg_not_value_order() -> None:
    """Declared axis metadata stays as EPSG defines it, whatever the values do."""
    crs = CoordinateReferenceSystem.from_user_input("EPSG:4326")
    assert crs.axis_abbreviations == ("Lat", "Lon")
    assert crs.axis_units == ("degree", "degree")
    assert crs.value_axis_order == (1, 0)
    assert crs.value_axis_abbreviations == ("Lon", "Lat")


def test_easting_first_crs_needs_no_reordering() -> None:
    """A CRS already in xy order reports the identity permutation."""
    crs = CoordinateReferenceSystem.from_user_input("EPSG:25832")
    assert crs.axis_abbreviations == ("E", "N")
    assert crs.value_axis_order == (0, 1)


def test_polar_crs_is_resolved_by_abbreviation() -> None:
    """Both UPS North axes point south, so the abbreviation decides."""
    crs = CoordinateReferenceSystem.from_user_input("EPSG:32661")
    assert [axis.direction for axis in crs.axes] == ["south", "south"]
    assert crs.axis_abbreviations == ("N", "E")
    assert crs.value_axis_order == (1, 0)


def test_vertical_crs_keeps_its_single_axis() -> None:
    """A one-axis CRS has nothing to reorder."""
    crs = CoordinateReferenceSystem.from_user_input("EPSG:3855")
    assert crs.axis_abbreviations == ("H",)
    assert crs.value_axis_order == (0,)


def test_geocentric_crs_keeps_declared_order() -> None:
    """Geocentric X/Y/Z is not an east/north pair and is left alone."""
    crs = CoordinateReferenceSystem.from_user_input("EPSG:4896")
    assert crs.axis_abbreviations == ("X", "Y", "Z")
    assert crs.value_axis_order == (0, 1, 2)


def _probe_point(code: str) -> tuple[float, float]:
    """A latitude and longitude inside the CRS's area of use."""
    if code == "EPSG:32661":
        return 85.0, 20.0
    if code in {"EPSG:32761", "EPSG:3031", "EPSG:3032"}:
        return -75.0, 20.0
    if code in {"EPSG:2049", "EPSG:22277"}:
        return -29.0, 29.0
    if code == "EPSG:2207":
        return 36.0, 29.0
    return 52.0, 9.0
