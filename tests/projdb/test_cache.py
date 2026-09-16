"""A cached build must be fast, honest about being cached, and never leak a token.

The cache sits under the API client as a transport, so these tests stack it over
``httpx.MockTransport`` and count what reaches the far side. That is the property
that matters: a cache is only useful if the second build does not make the
request, and only safe if certain requests are never stored at all.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import httpx
import pytest

from geodetic_engine.georepository.cache import (
    CacheMode,
    CachingTransport,
    ResponseCache,
    is_newer,
)

API = "https://register.example.com/api/v1"


@pytest.fixture
def cache(tmp_path: Path) -> ResponseCache:
    with ResponseCache(tmp_path / "proj.db.cache") as store:
        yield store


class CountingTransport(httpx.MockTransport):
    """A mock transport that records how many requests actually reached it."""

    def __init__(self, handler) -> None:
        self.requests: list[httpx.Request] = []

        def counting(request: httpx.Request) -> httpx.Response:
            self.requests.append(request)
            return handler(request)

        super().__init__(counting)


def _json_ok(payload: bytes = b'{"Results": []}'):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"url": str(request.url)})

    del payload
    return handler


def test_a_repeated_get_is_served_without_reaching_the_network(
    cache: ResponseCache,
) -> None:
    upstream = CountingTransport(_json_ok())
    client = httpx.Client(transport=CachingTransport(cache, wrapped=upstream))

    first = client.get(f"{API}/Unit", params={"page": 0})
    second = client.get(f"{API}/Unit", params={"page": 0})

    assert first.json() == second.json()
    assert len(upstream.requests) == 1
    assert (cache.stats.hits, cache.stats.misses) == (1, 1)


def test_the_query_string_is_part_of_the_key(cache: ResponseCache) -> None:
    upstream = CountingTransport(_json_ok())
    client = httpx.Client(transport=CachingTransport(cache, wrapped=upstream))

    client.get(f"{API}/Unit", params={"page": 0})
    client.get(f"{API}/Unit", params={"page": 1})

    assert len(upstream.requests) == 2


def test_the_accept_header_is_part_of_the_key(cache: ResponseCache) -> None:
    upstream = CountingTransport(_json_ok())
    client = httpx.Client(transport=CachingTransport(cache, wrapped=upstream))

    client.get(f"{API}/CoordRefSystem/1/export", headers={"Accept": "application/json"})
    client.get(f"{API}/CoordRefSystem/1/export", headers={"Accept": "text/plain"})

    assert len(upstream.requests) == 2


def test_a_post_is_never_stored(cache: ResponseCache, tmp_path: Path) -> None:
    """The credential shares this transport, and its token request is a POST."""
    upstream = CountingTransport(
        lambda request: httpx.Response(200, json={"access_token": "super-secret"})
    )
    client = httpx.Client(transport=CachingTransport(cache, wrapped=upstream))

    client.post(f"{API}/../../auth/connect/token", data={"grant_type": "x"})
    client.post(f"{API}/../../auth/connect/token", data={"grant_type": "x"})

    assert len(upstream.requests) == 2
    stored = sqlite3.connect(cache.path).execute("SELECT COUNT(*) FROM response")
    assert stored.fetchone()[0] == 0


def test_the_version_history_is_never_stored(cache: ResponseCache) -> None:
    """A cached answer to "has the register changed" can only confirm itself."""
    upstream = CountingTransport(_json_ok())
    client = httpx.Client(transport=CachingTransport(cache, wrapped=upstream))

    client.get(f"{API}/VersionHistory", params={"page": 0})
    client.get(f"{API}/VersionHistory", params={"page": 0})

    assert len(upstream.requests) == 2


def test_a_failed_response_is_not_stored(cache: ResponseCache) -> None:
    """A transient 502 the retry loop would recover from must not become the answer."""
    upstream = CountingTransport(lambda request: httpx.Response(502))
    client = httpx.Client(transport=CachingTransport(cache, wrapped=upstream))

    client.get(f"{API}/Unit")
    client.get(f"{API}/Unit")

    assert len(upstream.requests) == 2


def test_refresh_refetches_and_replaces(cache: ResponseCache) -> None:
    upstream = CountingTransport(_json_ok())
    httpx.Client(transport=CachingTransport(cache, wrapped=upstream)).get(f"{API}/Unit")

    refreshing = httpx.Client(
        transport=CachingTransport(cache, wrapped=upstream, refresh=True)
    )
    refreshing.get(f"{API}/Unit")

    assert len(upstream.requests) == 2
    assert cache.stats.stored == 2


def test_the_cache_survives_being_reopened(tmp_path: Path) -> None:
    upstream = CountingTransport(_json_ok())
    path = tmp_path / "proj.db.cache"
    with ResponseCache(path) as first:
        httpx.Client(transport=CachingTransport(first, wrapped=upstream)).get(
            f"{API}/Unit"
        )
    with ResponseCache(path) as second:
        httpx.Client(transport=CachingTransport(second, wrapped=upstream)).get(
            f"{API}/Unit"
        )
        assert second.stats.hits == 1

    assert len(upstream.requests) == 1


def test_versions_round_trip(cache: ResponseCache) -> None:
    cache.record_versions({"EPSG": "12.053", "Equinor": "1.103"})

    assert cache.versions() == {"EPSG": "12.053", "Equinor": "1.103"}


def test_transient_cache_does_not_create_files(tmp_path: Path) -> None:
    path = tmp_path / "absent" / "proj.db.cache"
    with ResponseCache(path, transient=True) as transient:
        transient.write(f"{API}/Unit", "", 200, "application/json", b"fresh")
        transient.record_versions({"Equinor": "1.104"})
        assert transient.read(f"{API}/Unit", "") == (200, "application/json", b"fresh")
    assert not path.parent.exists()


@pytest.mark.parametrize("refresh", [False, True])
def test_transient_cache_fetches_without_mutating_disk(
    tmp_path: Path, refresh: bool
) -> None:
    path = tmp_path / "proj.db.cache"
    with ResponseCache(path) as persistent:
        persistent.write(f"{API}/Unit", "*/*", 200, "text/plain", b"cached")
        persistent.record_versions({"Equinor": "1.103"})
    original = path.read_bytes()
    original_files = set(tmp_path.iterdir())
    upstream = CountingTransport(lambda request: httpx.Response(200, text="fresh"))

    with (
        ResponseCache(path, transient=True) as transient,
        httpx.Client(
            transport=CachingTransport(transient, wrapped=upstream, refresh=refresh)
        ) as client,
    ):
        assert transient.versions() == {}
        assert client.get(f"{API}/Unit").text == "fresh"
        assert client.get(f"{API}/Unit").text == "fresh"
        client.get(f"{API}/Datum")
        transient.record_versions({"Equinor": "1.104"})

    assert len(upstream.requests) == (3 if refresh else 2)
    assert path.read_bytes() == original
    assert set(tmp_path.iterdir()) == original_files


def test_clear_drops_responses_but_keeps_versions(cache: ResponseCache) -> None:
    upstream = CountingTransport(_json_ok())
    httpx.Client(transport=CachingTransport(cache, wrapped=upstream)).get(f"{API}/Unit")
    cache.record_versions({"Equinor": "1.103"})

    cache.clear()

    assert cache.versions() == {"Equinor": "1.103"}
    stored = sqlite3.connect(cache.path).execute("SELECT COUNT(*) FROM response")
    assert stored.fetchone()[0] == 0


@pytest.mark.parametrize(
    ("live", "cached", "expected"),
    [
        ("v1.103", "v1.102", True),
        ("1.103", "1.103", False),
        ("1.9", "1.10", False),
        ("12.053", "12.29", True),
        ("v1.2", "v1.2.1", False),
        ("2024-Q2", "2024-Q1", True),
        ("same", "same", False),
    ],
)
def test_is_newer(live: str, cached: str, expected: bool) -> None:
    assert is_newer(live, cached) is expected


def test_cache_mode_values() -> None:
    assert [mode.value for mode in CacheMode] == ["use", "refresh", "off"]


def test_stats_report_the_hit_rate(cache: ResponseCache) -> None:
    upstream = CountingTransport(_json_ok())
    client = httpx.Client(transport=CachingTransport(cache, wrapped=upstream))
    client.get(f"{API}/Unit")
    client.get(f"{API}/Unit")

    summary = cache.stats.as_dict()

    assert summary["hits"] == 1
    assert summary["misses"] == 1
    assert summary["hit_rate"] == 0.5
