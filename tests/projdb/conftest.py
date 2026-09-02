"""Shared fixtures for the projdb tests.

The fake Georepository is driven by hand written JSON rather than by responses
captured from a live instance, so no internal CRS definition or hostname ends up
in the repository.
"""

from __future__ import annotations

import shutil
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import httpx
import pyproj
import pytest

from geodetic_engine.georepository.config import GeorepositoryConfig
from geodetic_engine.projdb.config import ProjDbBuildConfig

API = "https://georepo.example.test"
AUTHORITY = "Example"

# Real EPSG codes present in the proj.db shipped with PROJ, so custom objects
# can reference them the way an actual authority's objects do.
EPSG_WGS84_DATUM = "6326"
EPSG_ELLIPSOIDAL_2D_CS = "6422"
EPSG_WORLD_EXTENT = "1262"
EPSG_SCOPE = "1026"


@pytest.fixture(autouse=True)
def isolated_cwd(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Run each test in an empty directory.

    Config file discovery looks in the working directory, so a stray
    geodetic-projdb.toml in the repository would otherwise leak into tests.
    """
    working = tmp_path / "cwd"
    working.mkdir()
    monkeypatch.chdir(working)


@pytest.fixture
def base_proj_db() -> Path:
    """Path to the official proj.db of the linked PROJ."""
    return Path(pyproj.datadir.get_data_dir()) / "proj.db"


@pytest.fixture
def output_db(tmp_path: Path) -> Path:
    return tmp_path / "custom" / "proj.db"


def make_georepository_config(**overrides: Any) -> GeorepositoryConfig:
    """Connection settings pointing at the fake instance."""
    defaults: dict[str, Any] = {
        "api_url": API,
        "client_id": "test-client",
        "client_secret": "test-secret",
        "page_size": 2,
    }
    return GeorepositoryConfig(**(defaults | overrides))


def make_config(
    base_proj_db: Path, output_db: Path, **overrides: Any
) -> ProjDbBuildConfig:
    """Build a configuration pointing at the fake instance."""
    georepository = overrides.pop("georepository", None) or make_georepository_config()
    defaults: dict[str, Any] = {
        "georepository": georepository,
        "authorities": frozenset({AUTHORITY}),
        "base_proj_db": base_proj_db,
        "output_db": output_db,
    }
    return ProjDbBuildConfig(**(defaults | overrides))


@pytest.fixture
def config(base_proj_db: Path, output_db: Path) -> ProjDbBuildConfig:
    return make_config(base_proj_db, output_db)


@pytest.fixture
def georepository_config() -> GeorepositoryConfig:
    return make_georepository_config()


class FakeGeorepository:
    """An httpx transport that serves a scripted Georepository instance.

    Collections are paged exactly as the real API documents: a 0-based ``page``
    parameter, a ``pageSize``, and a ``TotalResults`` count in every response.
    """

    def __init__(self, collections: dict[str, list[dict[str, Any]]]) -> None:
        self.collections = collections
        self.objects: dict[str, dict[str, Any]] = {}
        self.aliases: dict[str, list[dict[str, Any]]] = {}
        self.requests: list[httpx.URL] = []
        self.ignore_page = False
        self.understate_total_by = 0

    def add_object(self, path: str, payload: dict[str, Any]) -> str:
        """Register a detail object and return its absolute URL."""
        url = f"{API}{path}"
        self.objects[url] = payload
        return url

    def add_aliases(self, path: str, records: list[dict[str, Any]]) -> None:
        """Register the response of an object's ``/alias`` endpoint."""
        self.aliases[f"{API}{path}/alias"] = records

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request.url)
        url = str(request.url)

        if url.endswith("/auth/connect/token"):
            return httpx.Response(
                200,
                json={
                    "access_token": "fake-token",
                    "token_type": "Bearer",
                    "expires_in": 3600,
                },
            )

        base = url.split("?")[0]
        if base in self.aliases:
            return httpx.Response(200, json=self.aliases[base])
        if base.endswith("/alias"):
            return httpx.Response(200, json=[])
        if base in self.objects:
            return httpx.Response(200, json=self.objects[base])

        name = base.rsplit("/", 1)[-1]
        if name not in self.collections:
            return httpx.Response(200, json={"Results": [], "TotalResults": 0})

        items = self.collections[name]
        page = 0 if self.ignore_page else int(request.url.params.get("page", 0))
        size = int(request.url.params.get("pageSize", 10))
        start = page * size
        return httpx.Response(
            200,
            json={
                "Results": items[start : start + size],
                "Count": len(items[start : start + size]),
                "Page": page,
                "PageSize": size,
                "TotalResults": len(items) - self.understate_total_by,
            },
        )

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self.handler)


@pytest.fixture
def copy_of_proj_db(base_proj_db: Path, tmp_path: Path) -> Iterator[Path]:
    """A writable copy of the official proj.db."""
    target = tmp_path / "copy.db"
    shutil.copyfile(base_proj_db, target)
    yield target
