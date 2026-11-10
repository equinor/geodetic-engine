"""Listing datums, ellipsoids, prime meridians and units, against pyproj."""

from __future__ import annotations

import math
from typing import Any

import pytest
from pyproj.crs import Datum, Ellipsoid, PrimeMeridian
from pyproj.database import get_codes, get_units_map
from pyproj.enums import PJType

from geodetic_engine import catalog
from geodetic_engine.catalog import DatumKind, InvalidFilterError, UnitKind


def _codes(listing: catalog.Listing[Any]) -> set[str]:
    return {entry.authority_code for entry in listing}


def test_geodetic_and_vertical_datums_are_pyprojs() -> None:
    expected = {
        f"EPSG:{code}"
        for kind in (
            PJType.GEODETIC_REFERENCE_FRAME,
            PJType.DYNAMIC_GEODETIC_REFERENCE_FRAME,
            PJType.VERTICAL_REFERENCE_FRAME,
            PJType.DYNAMIC_VERTICAL_REFERENCE_FRAME,
            PJType.DATUM_ENSEMBLE,
        )
        for code in get_codes("EPSG", kind, allow_deprecated=True)
    }
    listed = catalog.datums(
        authority="EPSG", kind=["geodetic", "vertical"], deprecated=None
    )
    assert _codes(listed) == expected


def test_projjson_type_is_pyprojs() -> None:
    sample = [
        *catalog.datums(authority="EPSG", ensemble=True),
        *catalog.datums(authority="EPSG", dynamic=True, limit=3),
        *catalog.datums(authority="EPSG", kind="vertical", limit=3),
        *catalog.datums(authority="EPSG", kind="engineering", limit=3),
        *catalog.datums(authority="EPSG", kind="geodetic", dynamic=False, limit=3),
    ]
    for entry in sample:
        assert entry.projjson_type == entry.to_json_dict()["type"], entry


def test_an_ensemble_lists_its_members() -> None:
    etrs89 = catalog.datums(code="EPSG:6258").one()
    assert etrs89.is_ensemble
    assert etrs89.ensemble_accuracy == 0.1
    assert etrs89.members[0].authority_code == "EPSG:1178"
    assert etrs89.ellipsoid is not None and etrs89.ellipsoid.name == "GRS 1980"
    members = catalog.datums(member_of="ETRS89")
    assert _codes(members) <= {m.authority_code for m in etrs89.members}
    assert "EPSG:1178" in _codes(members)


def test_dynamic_frames_and_epochs() -> None:
    found = catalog.datums(authority="EPSG", frame_reference_epoch=(2010, 2010))
    assert found
    assert all(e.is_dynamic and e.frame_reference_epoch == 2010 for e in found)
    static = catalog.datums(authority="EPSG", dynamic=False, kind="geodetic")
    assert all(e.frame_reference_epoch is None for e in static)


def test_ellipsoid_and_prime_meridian_filters() -> None:
    on_grs80 = catalog.datums(authority="EPSG", ellipsoid="GRS 1980")
    assert "EPSG:6258" in _codes(on_grs80)
    assert {e.ellipsoid.authority_code for e in on_grs80 if e.ellipsoid} == {
        "EPSG:7019"
    }
    paris = catalog.datums(authority="EPSG", prime_meridian="Paris")
    assert paris
    assert all(e.prime_meridian and e.prime_meridian.name == "Paris" for e in paris)


def test_a_datum_filter_refuses_an_ellipsoid_it_cannot_find() -> None:
    with pytest.raises(InvalidFilterError, match="no ellipsoid named"):
        catalog.datums(ellipsoid="Potato")


def test_datum_kinds() -> None:
    assert catalog.datums(code="EPSG:5215").one().kind is DatumKind.VERTICAL
    engineering = catalog.datums(authority="EPSG", kind="engineering")
    assert engineering
    assert {e.projjson_type for e in engineering} == {"EngineeringDatum"}


def test_datum_area_of_interest() -> None:
    found = catalog.datums(authority="EPSG", area_of_interest=(10, 59, 11, 60))
    assert "EPSG:6258" in _codes(found)
    assert "EPSG:6267" not in _codes(found)  # NAD27


def test_ellipsoids_agree_with_pyproj() -> None:
    for entry in catalog.ellipsoids(authority="EPSG", deprecated=None):
        ellipsoid = Ellipsoid.from_authority(entry.auth_name, entry.code)
        assert math.isclose(entry.semi_major_axis, ellipsoid.semi_major_metre)
        assert math.isclose(entry.semi_minor_axis, ellipsoid.semi_minor_metre)
        if entry.is_sphere:
            assert ellipsoid.inverse_flattening == 0
        else:
            assert entry.inverse_flattening is not None
            assert math.isclose(
                entry.inverse_flattening, ellipsoid.inverse_flattening, rel_tol=1e-9
            )


def test_ellipsoid_filters() -> None:
    assert catalog.ellipsoids(code="EPSG:7035", deprecated=None).one().is_sphere
    spheres = catalog.ellipsoids(authority="EPSG", sphere=True)
    assert spheres
    assert all(e.inverse_flattening is None for e in spheres)
    flattening = catalog.ellipsoids(inverse_flattening=(298.25, 298.26))
    assert "EPSG:7019" in _codes(flattening)
    earth = catalog.ellipsoids(celestial_body="earth", authority="EPSG")
    assert "EPSG:7030" in _codes(earth)


def test_ellipsoid_ranges_are_checked() -> None:
    with pytest.raises(InvalidFilterError, match="wrong way round"):
        catalog.ellipsoids(semi_major_axis=(7e6, 6e6))


def test_prime_meridians_agree_with_pyproj() -> None:
    for entry in catalog.prime_meridians(deprecated=None):
        meridian = PrimeMeridian.from_authority(entry.auth_name, entry.code)
        assert entry.longitude == meridian.longitude
        assert entry.unit == meridian.unit_name


def test_prime_meridian_longitudes_in_degrees() -> None:
    oslo = catalog.prime_meridians(authority="EPSG", name="Oslo").one()
    assert oslo.longitude_degrees == pytest.approx(10 + 43 / 60 + 22.5 / 3600)
    paris = catalog.prime_meridians(code="EPSG:8903").one()
    assert paris.unit == "grad"
    assert paris.longitude_degrees == pytest.approx(2.5969213 * 0.9)
    east = catalog.prime_meridians(authority="EPSG", longitude=(100, None))
    assert {e.name for e in east} == {"Jakarta"}
    assert _codes(catalog.prime_meridians(unit="grad")) >= {"EPSG:8903"}


def test_units_are_pyprojs() -> None:
    for authority in ("EPSG", "ESRI", "PROJ"):
        expected = {
            f"{u.auth_name}:{u.code}"
            for u in get_units_map(auth_name=authority, allow_deprecated=True).values()
        }
        listed = catalog.units(authority=authority, deprecated=None)
        assert _codes(listed) == expected


def test_unit_filters() -> None:
    feet = catalog.units(authority="EPSG", kind="linear", name="foot")
    assert "EPSG:9003" in _codes(feet)
    assert {e.kind for e in feet} == {UnitKind.LINEAR}
    metre = catalog.units(authority="EPSG", kind="linear", conversion_factor=1.0)
    assert "EPSG:9001" in _codes(metre)


def test_unit_projjson(projjson_definition: Any) -> None:
    foot = catalog.units(code="EPSG:9003").one()
    definition = foot.to_json_dict()
    assert definition["type"] == "LinearUnit"
    projjson_definition(definition, "unit")
    for unit in catalog.units(deprecated=None):
        projjson_definition(unit.to_json_dict(), "unit")


def test_datum_and_ellipsoid_projjson_validates(projjson_validator: Any) -> None:
    projjson_validator.validate(catalog.datums(code="EPSG:6258").one().to_json_dict())
    projjson_validator.validate(
        catalog.ellipsoids(code="EPSG:7019").one().to_json_dict()
    )
    projjson_validator.validate(
        catalog.prime_meridians(code="EPSG:8903").one().to_json_dict()
    )
    assert (
        catalog.datums(code="EPSG:6258").one().to_json_dict()
        == Datum.from_epsg(6258).to_json_dict()
    )
