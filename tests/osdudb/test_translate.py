"""Reading OSDU records: the envelope, the cross references and the aliases."""

from __future__ import annotations

import pytest

from geodetic_engine.osdudb import translate as tr


def test_authority_code_reads_a_cross_reference() -> None:
    assert tr.authority_code(
        {"AuthorityCode": {"Authority": "EPSG", "Code": 4326}}
    ) == (
        "EPSG",
        "4326",
    )


@pytest.mark.parametrize("link", [None, {}, {"AuthorityCode": {}}, {"Name": "x"}])
def test_authority_code_of_an_absent_reference(link: dict | None) -> None:
    assert tr.authority_code(link) == (None, None)


def test_code_prefers_the_authoritative_spelling() -> None:
    # CodeAsNumber loses a leading zero, so Code wins where both are present.
    assert tr.code({"Code": "04326", "CodeAsNumber": 4326}) == "04326"
    assert tr.code({"CodeAsNumber": 4326}) == "4326"
    assert tr.code({}) is None


@pytest.mark.parametrize("key", ["OGCWellKnownText2", "Wkt2Ogc"])
def test_wkt_is_read_under_either_published_key(key: str) -> None:
    assert tr.wkt({key: 'GEOGCRS["x"]'}) == 'GEOGCRS["x"]'


def test_a_record_without_wkt_states_no_definition() -> None:
    assert tr.wkt({"Name": "no definition here"}) is None


def test_usages_falls_back_to_the_preferred_usage() -> None:
    preferred = {"Scope": {"Name": "Geodesy."}}
    assert tr.usages({"PreferredUsage": preferred}) == [preferred]
    assert tr.usages({"Usages": [], "PreferredUsage": preferred}) == [preferred]


def test_usages_prefers_the_full_list() -> None:
    listed = [{"Scope": {"Name": "a"}}, {"Scope": {"Name": "b"}}]
    record = {"Usages": listed, "PreferredUsage": listed[0]}
    assert tr.usages(record) == listed


def test_inactive_records_are_deprecated() -> None:
    assert tr.deprecated_flag({"InactiveIndicator": True}) == 1
    assert tr.deprecated_flag({"InactiveIndicator": False}) == 0
    assert tr.deprecated_flag({}) == 0


class TestScopeAndExtent:
    def test_a_coded_extent_keeps_its_own_identity(self) -> None:
        extent = tr.extent_of(
            {
                "Extent": {
                    "AuthorityCode": {"Authority": "EPSG", "Code": 1262},
                    "Name": "World",
                    "BoundingBoxSouthBoundLatitude": -90.0,
                    "BoundingBoxNorthBoundLatitude": 90.0,
                    "BoundingBoxWestBoundLongitude": -180.0,
                    "BoundingBoxEastBoundLongitude": 180.0,
                }
            },
            derived=("OSDU", "geodetic_crs_1000_1"),
        )
        assert extent is not None
        assert (extent.auth_name, extent.code) == ("EPSG", "1262")

    def test_an_extent_osdu_computed_is_kept_under_the_derived_code(self) -> None:
        # OSDU intersects the CRS and transformation extents for a bound CRS and
        # gives the result no code. Dropping it would leave the bound CRS
        # looking valid everywhere its base CRS is.
        extent = tr.extent_of(
            {
                "Extent": {
                    "Name": "Extent intersection between CRS and CT",
                    "BoundingBoxSouthBoundLatitude": 50.2,
                    "BoundingBoxNorthBoundLatitude": 54.74,
                    "BoundingBoxWestBoundLongitude": 9.92,
                    "BoundingBoxEastBoundLongitude": 13.84,
                }
            },
            derived=("OSDU", "geodetic_crs_1000_1"),
        )
        assert extent is not None
        assert (extent.auth_name, extent.code) == ("OSDU", "geodetic_crs_1000_1")
        assert (extent.south_lat, extent.north_lat) == (50.2, 54.74)
        assert (extent.west_lon, extent.east_lon) == (9.92, 13.84)

    def test_an_extent_with_no_box_is_not_invented(self) -> None:
        assert (
            tr.extent_of({"Extent": {"Name": "nowhere"}}, derived=("OSDU", "x")) is None
        )

    def test_an_uncoded_extent_is_dropped_without_a_derived_code(self) -> None:
        usage = {
            "Extent": {
                "Name": "somewhere",
                "BoundingBoxSouthBoundLatitude": 1.0,
                "BoundingBoxNorthBoundLatitude": 2.0,
                "BoundingBoxWestBoundLongitude": 3.0,
                "BoundingBoxEastBoundLongitude": 4.0,
            }
        }
        assert tr.extent_of(usage) is None

    def test_a_scope_osdu_computed_is_kept_under_the_derived_code(self) -> None:
        scope = tr.scope_of(
            {"Scope": {"Name": "Geodesy."}}, derived=("OSDU", "geodetic_crs_1000_1")
        )
        assert scope is not None
        assert (scope.auth_name, scope.code) == ("OSDU", "geodetic_crs_1000_1")
        assert scope.scope == "Geodesy."

    def test_a_scope_with_no_text_is_not_invented(self) -> None:
        assert tr.scope_of({"Scope": {}}, derived=("OSDU", "x")) is None


class TestAliases:
    def test_the_naming_system_comes_from_the_alias_type(self) -> None:
        record = {
            "Name": "ED50",
            "NameAlias": [
                {
                    "AliasName": "European Datum 1950",
                    "AliasNameTypeID": "ns:reference-data--AliasNameType:ESRI:",
                }
            ],
        }
        assert list(tr.aliases(record)) == [("European Datum 1950", "ESRI")]

    def test_an_alias_equal_to_the_name_carries_nothing(self) -> None:
        record = {
            "Name": "ED50",
            "NameAlias": [
                {"AliasName": "ED50", "AliasNameTypeID": "x:AliasNameType:A:"}
            ],
        }
        assert list(tr.aliases(record)) == []

    @pytest.mark.parametrize(
        ("alias_type_id", "expected"),
        [
            ("ns:reference-data--AliasNameType:EPSGname:", "EPSGname"),
            ("ns:reference-data--AliasNameType:Equinor:", "Equinor"),
            ("nonsense", ""),
            (None, ""),
        ],
    )
    def test_naming_system_parsing(
        self, alias_type_id: str | None, expected: str
    ) -> None:
        assert tr.naming_system(alias_type_id) == expected
