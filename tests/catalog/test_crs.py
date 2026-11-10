"""Listing CRSs: agreement with pyproj, and every filter."""

from __future__ import annotations

import re
from typing import Any

import pyproj
import pytest
from pyproj.aoi import AreaOfInterest
from pyproj.database import query_crs_info

from geodetic_engine import catalog
from geodetic_engine.catalog import (
    CrsKind,
    InvalidFilterError,
    ObjectRef,
    UnknownAuthorityError,
)
from geodetic_engine.geodesy import CoordinateReferenceSystem


def _codes(listing: catalog.Listing[Any]) -> set[str]:
    return {entry.authority_code for entry in listing}


def test_lists_every_crs_pyproj_lists_once() -> None:
    expected = {
        f"{info.auth_name}:{info.code}"
        for info in query_crs_info(allow_deprecated=True)
    }
    listed = catalog.crs(deprecated=None)
    assert _codes(listed) == expected
    assert len(listed) == len(expected)


def test_leaves_deprecated_out_as_pyproj_does() -> None:
    expected = {
        f"{info.auth_name}:{info.code}" for info in query_crs_info(auth_name="EPSG")
    }
    assert _codes(catalog.crs(authority="EPSG")) == expected


def test_lists_only_deprecated_on_request_with_replacements() -> None:
    deprecated = catalog.crs(authority="EPSG", deprecated=True)
    assert deprecated
    assert all(entry.deprecated for entry in deprecated)
    replaced = catalog.crs(code="EPSG:2156", deprecated=True).one()
    assert "EPSG:2195" in replaced.replaced_by


def test_is_ordered_by_authority_then_numeric_code() -> None:
    codes = [int(e.code) for e in catalog.crs(authority="EPSG", limit=500)]
    assert codes == sorted(codes)


@pytest.mark.parametrize(
    ("code", "kind", "projjson_type"),
    [
        ("EPSG:4326", CrsKind.GEOGRAPHIC_2D, "GeographicCRS"),
        ("EPSG:4979", CrsKind.GEOGRAPHIC_3D, "GeographicCRS"),
        ("EPSG:4978", CrsKind.GEOCENTRIC, "GeodeticCRS"),
        ("EPSG:25832", CrsKind.PROJECTED, "ProjectedCRS"),
        ("EPSG:5941", CrsKind.VERTICAL, "VerticalCRS"),
        ("EPSG:9518", CrsKind.COMPOUND, "CompoundCRS"),
        ("EPSG:5800", CrsKind.ENGINEERING, "EngineeringCRS"),
    ],
)
def test_reports_the_kind_and_the_projjson_type(
    code: str, kind: CrsKind, projjson_type: str
) -> None:
    entry = catalog.crs(code=code).one()
    assert entry.kind is kind
    assert entry.projjson_type == projjson_type
    assert entry.to_json_dict()["type"] == projjson_type


def test_finds_engineering_crss_pyproj_reports_as_plain_crs() -> None:
    # pyproj's own engineering filter finds none.
    assert not query_crs_info(pj_types=pyproj.enums.PJType.ENGINEERING_CRS)
    found = catalog.crs(authority="EPSG", kind="engineering")
    assert "EPSG:5800" in _codes(found)
    assert {entry.kind for entry in found} == {CrsKind.ENGINEERING}


def test_kind_accepts_several_and_any_case() -> None:
    found = catalog.crs(authority="EPSG", kind=["Geographic_2D", CrsKind.VERTICAL])
    assert {e.kind for e in found} == {CrsKind.GEOGRAPHIC_2D, CrsKind.VERTICAL}


def test_an_unknown_kind_names_the_choices() -> None:
    with pytest.raises(InvalidFilterError, match="'projected'"):
        catalog.crs(kind="map")


@pytest.mark.parametrize("datum", ["EPSG:6258", "ETRS89", 6258])
def test_datum_by_code_name_or_alias(datum: object) -> None:
    found = catalog.crs(authority="EPSG", kind="projected", datum=datum)
    assert "EPSG:25832" in _codes(found)
    assert "EPSG:32632" not in _codes(found)
    # EPSG also records "ETRS89" as an alias of ETRS89-IRE [ETRF2000].
    named = {"EPSG:6258", "EPSG:6173"}
    assert all(any(d.authority_code in named for d in e.datums) for e in found)


def test_datum_refuses_a_code_that_is_no_datum() -> None:
    with pytest.raises(InvalidFilterError, match="EPSG:4326 is not a datum"):
        catalog.crs(datum="EPSG:4326")


def test_datum_refuses_an_unknown_name() -> None:
    with pytest.raises(InvalidFilterError, match="no datum named"):
        catalog.crs(datum="Not a datum")


def test_a_compound_crs_reports_both_datums_and_every_axis_unit() -> None:
    entry = catalog.crs(code="EPSG:9518").one()
    assert [d.authority_code for d in entry.datums] == ["EPSG:6326", "EPSG:1027"]
    assert entry.coordinate_system is None
    assert [u.name for u in entry.axis_units][-1] == "metre"
    assert len(entry.axis_units) == 3


def test_datums_agree_with_pyproj() -> None:
    sample = catalog.crs(
        authority="EPSG", kind=["geographic_2d", "projected", "vertical"]
    )[::97]
    for entry in sample:
        datum = pyproj.CRS.from_authority(entry.auth_name, entry.code).datum
        if not entry.datums or datum is None:
            continue
        assert entry.datums[0].name == datum.name, entry


def test_coordinate_system_and_units_agree_with_pyproj() -> None:
    for code in ("EPSG:25832", "EPSG:2263", "EPSG:4979", "EPSG:5941"):
        entry = catalog.crs(code=code).one()
        crs = pyproj.CRS.from_user_input(code)
        assert entry.coordinate_system is not None
        assert entry.coordinate_system.code == str(
            crs.coordinate_system.to_json_dict()["id"]["code"]
        )
        assert [u.code for u in entry.axis_units] == [
            a.unit_code for a in crs.axis_info
        ]


def test_unit_filter_finds_crss_in_us_survey_feet() -> None:
    found = catalog.crs(authority="EPSG", unit="US survey foot")
    assert "EPSG:2263" in _codes(found)
    assert all(
        any(u.authority_code == "EPSG:9003" for u in entry.axis_units)
        for entry in found
    )


def test_coordinate_system_kind_filter() -> None:
    found = catalog.crs(authority="EPSG", coordinate_system_kind="ellipsoidal")
    assert {e.kind for e in found} <= {CrsKind.GEOGRAPHIC_2D, CrsKind.GEOGRAPHIC_3D}


def test_projection_method_filter() -> None:
    found = catalog.crs(
        authority="EPSG", projection_method=re.compile("^Transverse Mercator$")
    )
    assert "EPSG:25832" in _codes(found)
    assert {e.projection_method for e in found} == {"Transverse Mercator"}


def test_name_matches_aliases_and_patterns() -> None:
    assert "EPSG:3044" in _codes(catalog.crs(authority="EPSG", name="ETRS89-TM32"))
    found = catalog.crs(authority="EPSG", name=re.compile(r"UTM zone 3[12]N$"))
    assert {"EPSG:25831", "EPSG:25832", "EPSG:32631"} <= _codes(found)


@pytest.mark.parametrize(
    "bounds",
    [
        (10.0, 59.0, 11.0, 60.0),
        (-10.0, 50.0, 30.0, 72.0),
        (170.0, -50.0, -170.0, -30.0),
        (-180.0, -90.0, 180.0, 90.0),
    ],
)
@pytest.mark.parametrize("contains", [False, True])
def test_area_of_interest_is_pyprojs(
    bounds: tuple[float, float, float, float], contains: bool
) -> None:
    expected = {
        f"{info.auth_name}:{info.code}"
        for info in query_crs_info(
            auth_name="EPSG",
            area_of_interest=AreaOfInterest(*bounds),
            contains=contains,
        )
    }
    found = catalog.crs(authority="EPSG", area_of_interest=bounds, contains=contains)
    assert _codes(found) == expected


def test_area_name_filter() -> None:
    found = catalog.crs(authority="EPSG", area_name="Norway", kind="projected")
    assert "EPSG:25832" in _codes(found)
    assert all(any("Norway" in a.name for a in e.areas_of_use) for e in found)


def test_bound_is_false_in_a_stock_database() -> None:
    entry = catalog.crs(code="EPSG:4230").one()
    assert entry.bound is False
    assert entry.projjson_type == "GeographicCRS"


@pytest.mark.parametrize(
    "code",
    [
        "EPSG:25832",
        ["EPSG:25832"],
        ObjectRef("EPSG", "25832", "ETRS89 / UTM zone 32N"),
        (c for c in ["epsg:25832"]),
    ],
)
def test_code_forms(code: object) -> None:
    assert _codes(catalog.crs(code=code)) == {"EPSG:25832"}  # type: ignore[arg-type]


def test_a_bare_code_matches_any_authority() -> None:
    found = catalog.crs(code=4326)
    assert "EPSG:4326" in _codes(found)
    assert {e.code for e in found} == {"4326"}


def test_an_entry_is_a_code() -> None:
    entry = catalog.crs(code="EPSG:25832").one()
    assert catalog.crs(code=entry).one() == entry


def test_code_outside_the_authority_filter_is_refused() -> None:
    with pytest.raises(InvalidFilterError, match="outside authority"):
        catalog.crs(authority="ESRI", code="EPSG:4326")


def test_an_unknown_authority_lists_the_known_ones() -> None:
    with pytest.raises(UnknownAuthorityError, match="EPSG"):
        catalog.crs(authority="EPSGG")


def test_authority_is_matched_without_case() -> None:
    assert catalog.crs(authority="epsg", code="4326").one().auth_name == "EPSG"


@pytest.mark.parametrize(
    "bounds",
    [(0, 0, 1), (0, 10, 1, 5), (0, 0, 200, 1), (0, -95, 1, 1), ("a", 0, 1, 1)],
)
def test_a_malformed_area_of_interest_is_refused(bounds: object) -> None:
    with pytest.raises(InvalidFilterError, match="area_of_interest"):
        catalog.crs(area_of_interest=bounds)  # type: ignore[arg-type]


def test_resolve_builds_the_crs_geodesy_would() -> None:
    entry = catalog.crs(code="EPSG:25832").one()
    resolved = entry.resolve()
    assert isinstance(resolved, CoordinateReferenceSystem)
    assert resolved.authority_code == "EPSG:25832"


def test_to_json_dict_is_pyprojs_projjson(projjson_validator: Any) -> None:
    entry = catalog.crs(code="EPSG:25832").one()
    definition = entry.to_json_dict()
    assert definition == pyproj.CRS.from_epsg(25832).to_json_dict()
    projjson_validator.validate(definition)
