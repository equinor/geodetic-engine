"""Pagination and authority filtering in the Georepository client."""

from __future__ import annotations

from typing import Any

import pytest

from geodetic_engine.georepository.client import GeorepositoryClient
from geodetic_engine.georepository.config import GeorepositoryConfig
from geodetic_engine.georepository.errors import PaginationTruncatedError
from tests.projdb.conftest import FakeGeorepository


def _items(count: int, data_source: str = "Example") -> list[dict[str, Any]]:
    return [{"Code": index, "DataSource": data_source} for index in range(count)]


def _client(
    config: GeorepositoryConfig, fake: FakeGeorepository
) -> GeorepositoryClient:
    return GeorepositoryClient(config, transport=fake.transport())


def test_every_page_is_followed(georepository_config: GeorepositoryConfig) -> None:
    """page_size is 2 in the fixture, so five results span three pages."""
    fake = FakeGeorepository({"Ellipsoid": _items(5)})
    with _client(georepository_config, fake) as client:
        collected = list(client.iter_collection("Ellipsoid"))
    assert [item["Code"] for item in collected] == [0, 1, 2, 3, 4]


def test_truncated_response_is_rejected(
    georepository_config: GeorepositoryConfig,
) -> None:
    """A server that runs out of results early must not look like success."""
    fake = FakeGeorepository({"Ellipsoid": _items(3)})
    fake.understate_total_by = -4  # advertise 7 results but only serve 3
    with (
        _client(georepository_config, fake) as client,
        pytest.raises(PaginationTruncatedError),
    ):
        list(client.iter_collection("Ellipsoid"))


def test_server_ignoring_the_page_parameter_is_detected(
    georepository_config: GeorepositoryConfig,
) -> None:
    """The reference implementation would have looped forever or truncated."""
    fake = FakeGeorepository({"Ellipsoid": _items(5)})
    fake.ignore_page = True
    with (
        _client(georepository_config, fake) as client,
        pytest.raises(PaginationTruncatedError, match="ignore the 'page' parameter"),
    ):
        list(client.iter_collection("Ellipsoid"))


def test_authority_filter_is_applied_client_side(
    georepository_config: GeorepositoryConfig,
) -> None:
    """The API offers no DataSource filter, so it has to happen here."""
    fake = FakeGeorepository({"Ellipsoid": _items(2, "Example") + _items(2, "EPSG")})
    with _client(georepository_config, fake) as client:
        collected = list(
            client.iter_collection("Ellipsoid", authorities=frozenset({"Example"}))
        )
    assert {item["DataSource"] for item in collected} == {"Example"}


def test_detail_objects_are_fetched_once(
    georepository_config: GeorepositoryConfig,
) -> None:
    """Scope and extent are referenced by many parents; refetching is wasteful."""
    fake = FakeGeorepository({})
    url = fake.add_object("/api/v1/Scope/1026", {"Code": 1026, "DataSource": "EPSG"})
    with _client(georepository_config, fake) as client:
        first = client.get_object(url)
        second = client.get_object(url)
    assert first == second
    assert sum(1 for request in fake.requests if str(request).startswith(url)) == 1


def test_paging_uses_zero_based_page_numbers(
    georepository_config: GeorepositoryConfig,
) -> None:
    fake = FakeGeorepository({"Ellipsoid": _items(3)})
    with _client(georepository_config, fake) as client:
        list(client.iter_collection("Ellipsoid"))
    pages = [
        request.params.get("page")
        for request in fake.requests
        if "Ellipsoid" in str(request)
    ]
    assert pages[0] == "0"


def test_wkt_is_exported_as_text(georepository_config: GeorepositoryConfig) -> None:
    """The export endpoint answers with a WKT body, not JSON.

    No formatVersion is sent. The parameter is a small enum rather than a year:
    its default is the WKT2 rendering, 1 means WKT1, and passing 2019 makes the
    register answer HTTP 500.
    """
    fake = FakeGeorepository({})
    url = fake.add_object("/api/v1/CoordRefSystem/4230", {"Code": 4230})
    fake.add_export("/api/v1/CoordRefSystem/4230", 'GEOGCRS["ED50",...]')

    with _client(georepository_config, fake) as client:
        wkt = client.wkt(client.get_object(url))

    assert wkt == 'GEOGCRS["ED50",...]'
    exported = [r for r in fake.requests if str(r).split("?")[0].endswith("/export")]
    assert exported[0].params["format"] == "WKT"
    assert "formatVersion" not in exported[0].params


def test_crs_export_uses_the_generic_collection(
    georepository_config: GeorepositoryConfig,
) -> None:
    """Only CoordRefSystem implements export; the per-kind ones answer 404."""
    fake = FakeGeorepository({})
    url = fake.add_object("/api/v1/GeodeticCoordRefSystem/4230", {"Code": 4230})
    fake.add_export("/api/v1/CoordRefSystem/4230", 'GEOGCRS["ED50",...]')

    with _client(georepository_config, fake) as client:
        assert client.wkt(client.get_object(url)) == 'GEOGCRS["ED50",...]'


def test_non_crs_export_keeps_its_own_collection(
    georepository_config: GeorepositoryConfig,
) -> None:
    """A transformation exports from its own collection, not the CRS one."""
    fake = FakeGeorepository({})
    url = fake.add_object("/api/v1/Transformation/1133", {"Code": 1133})
    fake.add_export("/api/v1/Transformation/1133", "COORDINATEOPERATION[...]")

    with _client(georepository_config, fake) as client:
        assert client.wkt(client.get_object(url)) == "COORDINATEOPERATION[...]"


def test_wkt_unwraps_a_json_quoted_body(
    georepository_config: GeorepositoryConfig,
) -> None:
    """Some deployments return the WKT as a JSON string; both must work."""
    fake = FakeGeorepository({})
    url = fake.add_object("/api/v1/Transformation/1133", {"Code": 1133})
    fake.add_export("/api/v1/Transformation/1133", '"COORDINATEOPERATION[\\"a\\"]"')

    with _client(georepository_config, fake) as client:
        assert client.wkt(client.get_object(url)) == 'COORDINATEOPERATION["a"]'


def test_wkt_is_none_when_the_body_is_empty(
    georepository_config: GeorepositoryConfig,
) -> None:
    """An object with no exportable definition must not become an empty string."""
    fake = FakeGeorepository({})
    url = fake.add_object("/api/v1/CoordRefSystem/1", {"Code": 1})
    fake.add_export("/api/v1/CoordRefSystem/1", "   ")

    with _client(georepository_config, fake) as client:
        assert client.wkt(client.get_object(url)) is None
