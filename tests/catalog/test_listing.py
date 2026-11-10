"""The listing container, its exports, and the shared filter rules."""

from __future__ import annotations

import json
import os
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pyproj
import pytest
from hypothesis import given, settings
from hypothesis import strategies as st
from pyproj.aoi import AreaOfInterest
from pyproj.database import get_database_metadata, query_crs_info

from geodetic_engine import catalog
from geodetic_engine.catalog import (
    CatalogError,
    InvalidFilterError,
    NotFoundError,
    NotUniqueError,
    _crs,
    _filters,
)
from geodetic_engine.geodesy import transform

ALL_KINDS = {
    "crs": lambda: catalog.crs(authority="EPSG", name="ETRS89", deprecated=None),
    "operations": lambda: catalog.operations(source_crs="EPSG:4230", deprecated=None),
    "datums": lambda: catalog.datums(authority="EPSG", limit=40, deprecated=None),
    "ellipsoids": lambda: catalog.ellipsoids(limit=40),
    "prime_meridians": lambda: catalog.prime_meridians(),
    "units": lambda: catalog.units(deprecated=None),
    "methods": lambda: catalog.methods(limit=40),
    "coordinate_systems": lambda: catalog.coordinate_systems(),
    "grids": lambda: catalog.grids(limit=40),
}


def test_nothing_is_read_until_the_listing_is_used(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = []
    real = _crs.entries

    def counted(databases: Any) -> Any:
        calls.append(databases)
        return real(databases)

    monkeypatch.setattr(_crs, "entries", counted)
    listing = catalog.crs(authority="EPSG", kind="projected")
    assert calls == []
    assert "unread" in repr(listing)
    assert len(listing) > 1000
    list(listing)
    listing.to_list()
    assert len(calls) == 1


def test_limit_and_offset_page_through_the_filtered_entries() -> None:
    whole = catalog.crs(authority="EPSG", kind="vertical")
    pages = [
        entry
        for offset in range(0, len(whole), 100)
        for entry in catalog.crs(
            authority="EPSG", kind="vertical", limit=100, offset=offset
        )
    ]
    assert pages == whole.to_list()
    assert len(catalog.crs(limit=0)) == 0


@pytest.mark.parametrize(("limit", "offset"), [(-1, 0), (1, -1), (1.5, 0), (True, 0)])
def test_malformed_paging_is_refused(limit: object, offset: object) -> None:
    with pytest.raises(InvalidFilterError):
        catalog.crs(limit=limit, offset=offset)  # type: ignore[arg-type]


def test_one_and_first() -> None:
    assert catalog.crs(code="EPSG:4326").one().name == "WGS 84"
    assert catalog.crs(code="EPSG:999999999").first() is None
    with pytest.raises(NotFoundError):
        catalog.crs(code="EPSG:999999999").one()
    with pytest.raises(NotUniqueError, match=r"\d+ crs entries match"):
        catalog.crs(authority="EPSG", name="ETRS89 / UTM").one()


def test_a_listing_is_a_sequence() -> None:
    listing = catalog.crs(authority="EPSG", kind="geocentric")
    assert listing[0] == listing.first()
    assert listing[:2] == tuple(listing)[:2]
    assert listing[-1] in listing
    assert bool(listing)
    assert not catalog.crs(code="EPSG:999999999")


@pytest.mark.parametrize("kind", sorted(ALL_KINDS))
def test_dataframe_columns_are_the_same_whatever_the_rows(kind: str) -> None:
    listing = ALL_KINDS[kind]()
    frame = listing.to_dataframe()
    assert len(frame) == len(listing)
    empty = type(listing)(
        listing.kind, {}, lambda: iter(()), columns=listing._columns
    ).to_dataframe()
    assert list(frame.columns) == list(empty.columns)


@pytest.mark.parametrize("kind", sorted(ALL_KINDS))
def test_every_export_validates(kind: str, listing_validator: Any) -> None:
    listing = ALL_KINDS[kind]()
    document = listing.to_json_dict(include_definitions=True)
    assert document["kind"] == kind
    assert document["count"] == len(listing) > 0
    listing_validator.validate(document)
    json.dumps(document)


def test_the_export_echoes_the_filters_as_json() -> None:
    listing = catalog.crs(
        authority=["epsg"],
        name=__import__("re").compile("UTM"),
        code=(c for c in ["EPSG:25832", 3044]),
        area_of_interest=AreaOfInterest(10, 59, 11, 60),
        limit=5,
    )
    assert listing.filters == {
        "authority": ["EPSG"],
        "name": {"pattern": "UTM"},
        "code": ["EPSG:25832", 3044],
        "area_of_interest": [10.0, 59.0, 11.0, 60.0],
        "contains": False,
        "deprecated": False,
        "limit": 5,
    }
    assert {e.authority_code for e in listing} == {"EPSG:25832", "EPSG:3044"}
    json.dumps(listing.to_json_dict())


def test_the_export_names_the_database_that_answered() -> None:
    info = catalog.database()
    assert info.epsg_version == get_database_metadata("EPSG.VERSION")
    assert info.proj_version == pyproj.proj_version_str
    result = transform("EPSG:4230", "EPSG:4326", (2.5, 63.5), operation="EPSG:1612")
    assert info.fingerprints == result.database_fingerprints
    document = catalog.crs(code="EPSG:4326").to_json_dict()
    assert document["database"] == info.to_json_dict()


def test_authorities_carry_their_versions() -> None:
    found = {authority.name: authority for authority in catalog.authorities()}
    assert found["EPSG"].version == get_database_metadata("EPSG.VERSION")
    assert str(found["EPSG"]).startswith("EPSG v")


@pytest.fixture
def switched_database(tmp_path: Path) -> Iterator[None]:
    previous = pyproj.datadir.get_data_dir()
    yield
    pyproj.datadir.set_data_dir(previous)


def test_a_listing_refuses_a_database_switched_under_it(
    switched_database: None, tmp_path: Path
) -> None:
    listing = catalog.crs(authority="EPSG")
    previous = pyproj.datadir.get_data_dir()
    pyproj.datadir.set_data_dir(f"{previous}{os.pathsep}{tmp_path}")
    with pytest.raises(CatalogError, match="database changed"):
        len(listing)


_LONGITUDES = st.floats(-180, 180, allow_nan=False)
_LATITUDES = st.floats(-90, 90, allow_nan=False)


@settings(max_examples=25)
@given(
    west=_LONGITUDES,
    east=_LONGITUDES,
    south=_LATITUDES,
    north=_LATITUDES,
    whole=st.booleans(),
)
def test_the_area_predicate_other_listings_use_is_projs(
    west: float, east: float, south: float, north: float, whole: bool
) -> None:
    south, north = min(south, north), max(south, north)
    bounds = (west, south, east, north)
    expected = {
        (info.auth_name, info.code)
        for info in query_crs_info(
            auth_name="EPSG",
            area_of_interest=AreaOfInterest(*bounds),
            contains=whole,
            allow_deprecated=True,
        )
    }
    found = {
        (entry.auth_name, entry.code)
        for entry in _crs.entries(_crs._registry.identity())
        if entry.auth_name == "EPSG"
        and _filters.located(entry.areas_of_use, bounds, whole)
    }
    assert found == expected


@pytest.mark.parametrize(
    ("value", "message"),
    [
        ("x", "number or"),
        ((1, 2, 3), "number or"),
        ((2, 1), "wrong way round"),
        ((None, "a"), "no number"),
        (float("nan"), "not a number"),
    ],
)
def test_malformed_ranges_are_refused(value: object, message: str) -> None:
    with pytest.raises(InvalidFilterError, match=message):
        catalog.units(conversion_factor=value)  # type: ignore[arg-type]


@pytest.mark.parametrize("value", [1, "yes"])
def test_flags_must_be_booleans(value: object) -> None:
    with pytest.raises(InvalidFilterError, match="deprecated"):
        catalog.units(deprecated=value)  # type: ignore[arg-type]


def test_an_empty_text_filter_is_refused() -> None:
    with pytest.raises(InvalidFilterError, match="name"):
        catalog.crs(name="  ")
