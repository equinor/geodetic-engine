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
