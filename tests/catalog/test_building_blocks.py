"""Listing methods, coordinate systems and grid files."""

from __future__ import annotations

import math
import os
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pyproj
import pytest

from geodetic_engine import catalog
from geodetic_engine.catalog import (
    CoordinateSystemKind,
    GridIndexUnavailableError,
    GridKind,
    InvalidFilterError,
    MethodKind,
    _grids,
)
from geodetic_engine.catalog._coordinate_systems import _users, direction
from geodetic_engine.catalog._registry import identity


def _codes(listing: catalog.Listing[Any]) -> set[str]:
    return {entry.authority_code for entry in listing}


def test_methods_name_their_parameters() -> None:
    tm = catalog.methods(code="EPSG:9807").one()
    assert tm.kind is MethodKind.CONVERSION
    assert "EPSG:8801" in {p.authority_code for p in tm.parameters}
    ntv2 = catalog.methods(code="EPSG:9615").one()
    assert ntv2.kind is MethodKind.TRANSFORMATION
    assert ntv2.operation_count > 0


def test_method_filters() -> None:
    by_parameter = catalog.methods(parameter="Latitude of natural origin")
    assert "EPSG:9807" in _codes(by_parameter)
    assert _codes(catalog.methods(parameter="EPSG:8801")) == _codes(by_parameter)
    assert {e.kind for e in catalog.methods(kind="conversion")} == {
        MethodKind.CONVERSION
    }
    assert all(e.operation_count == 0 for e in catalog.methods(used=False))


def test_method_counts_match_the_operation_listing() -> None:
    for method in catalog.methods(authority="EPSG")[::15]:
        listed = catalog.operations(method=method, deprecated=None)
        assert len(listed) == method.operation_count, method


def test_method_projjson(projjson_definition: Any) -> None:
    definition = catalog.methods(code="EPSG:9615").one().to_json_dict()
    assert definition["name"] == "NTv2"
    projjson_definition(definition, "method")


@pytest.mark.parametrize(
    ("orientation", "expected"),
    [
        ("east", ("east", None)),
        ("North along 90°E", ("north", 90.0)),
        ("South along 180°W", ("south", -180.0)),
        ("east-south-east", ("eastSouthEast", None)),
        ("north north east", ("northNorthEast", None)),
        ("columnPositive", ("columnPositive", None)),
        ("Along receiver lines", ("unspecified", None)),
    ],
)
def test_axis_directions_read_as_proj_reads_them(
    orientation: str, expected: tuple[str, float | None]
) -> None:
    assert direction(orientation) == expected


def _same(found: object, expected: object) -> bool:
    if isinstance(found, float) or isinstance(expected, float):
        return math.isclose(float(found), float(expected), rel_tol=1e-12)  # type: ignore[arg-type]
    if isinstance(found, dict) and isinstance(expected, dict):
        return found.keys() == expected.keys() and all(
            _same(found[k], expected[k]) for k in found
        )
    if isinstance(found, list) and isinstance(expected, list):
        return len(found) == len(expected) and all(
            _same(a, b) for a, b in zip(found, expected, strict=True)
        )
    return found == expected


def test_assembled_coordinate_systems_match_what_proj_exports() -> None:
    users = _users(identity())
    checked = 0
    for entry in catalog.coordinate_systems():
        if (entry.auth_name, entry.code) not in users:
            continue
        assert _same(entry._assembled(), entry.to_json_dict()), entry
        checked += 1
    assert checked > 50


def test_every_coordinate_system_exports_valid_projjson(
    projjson_definition: Any,
) -> None:
    for entry in catalog.coordinate_systems():
        projjson_definition(entry.to_json_dict(), "coordinate_system")


def test_coordinate_system_filters() -> None:
    found = catalog.coordinate_systems(authority="EPSG", axes=("e", "n"), unit="metre")
    assert "EPSG:4400" in _codes(found)
    assert all(e.axis_abbreviations == ("E", "N") for e in found)
    ellipsoidal = catalog.coordinate_systems(kind="ellipsoidal", dimension=3)
    assert ellipsoidal
    assert all(
        e.kind is CoordinateSystemKind.ELLIPSOIDAL and e.dimension == 3
        for e in ellipsoidal
    )
    cs = catalog.coordinate_systems(code="EPSG:4400").one()
    assert cs.name.startswith("Cartesian 2D CS. Axes: easting, northing (E,N).")
    with pytest.raises(InvalidFilterError, match="dimension"):
        catalog.coordinate_systems(dimension=0)
    with pytest.raises(InvalidFilterError, match="axes"):
        catalog.coordinate_systems(axes="EN")


def test_a_crs_points_at_its_coordinate_system() -> None:
    crs = catalog.crs(code="EPSG:25832").one()
    assert crs.coordinate_system is not None
    cs = catalog.coordinate_systems(code=crs.coordinate_system).one()
    assert cs.name == crs.coordinate_system.name


def test_a_nadcon_pair_is_one_grid_file() -> None:
    conus = catalog.grids(operation="EPSG:15851").one()
    assert conus.name == "us_noaa_conus.tif"
    assert {"conus.las", "conus.los"} <= set(conus.original_names)
    assert conus.kind is GridKind.HORIZONTAL
    assert "EPSG:15851" in conus.used_by


def test_grid_kinds() -> None:
    assert catalog.grids(name="us_nga_egm08_25").first().kind is GridKind.VERTICAL  # type: ignore[union-attr]
    assert all(g.kind is GridKind.VERTICAL for g in catalog.grids(kind="vertical"))


def test_grid_filters_by_authority_and_area() -> None:
    found = catalog.grids(authority="EPSG", area_name="Norway")
    assert found
    assert all(any(c.startswith("EPSG:") for c in g.used_by) for g in found)
    unused = catalog.grids(used=False)
    assert all(not g.used_by for g in unused)


@pytest.fixture
def extra_grid_directory(tmp_path: Path) -> Iterator[Path]:
    previous = pyproj.datadir.get_data_dir()
    pyproj.datadir.set_data_dir(f"{previous}{os.pathsep}{tmp_path}")
    try:
        yield tmp_path
    finally:
        pyproj.datadir.set_data_dir(previous)


def test_installed_reads_the_search_path(extra_grid_directory: Path) -> None:
    missing = catalog.grids(installed=False, used=True).first()
    assert missing is not None
    (extra_grid_directory / missing.name).write_bytes(b"")
    found = catalog.grids(name=missing.name).to_list()
    assert any(g.name == missing.name and g.installed for g in found)


def test_check_remote_reads_the_cdn_list_once(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = []

    def published(**kwargs: object) -> tuple[dict[str, Any], ...]:
        calls.append(kwargs)
        return ({"properties": {"name": "us_noaa_conus.tif"}},)

    monkeypatch.setattr(_grids, "get_transform_grid_list", published)
    _grids.remote_names.cache_clear()
    try:
        assert catalog.grids(operation="EPSG:15851").one().remote_available is None
        remote = catalog.grids(operation="EPSG:15851", check_remote=True).one()
        assert remote.remote_available is True
        published_only = catalog.grids(remote_available=True)
        assert [g.name for g in published_only] == ["us_noaa_conus.tif"]
        assert len(calls) == 1
    finally:
        _grids.remote_names.cache_clear()


def test_check_remote_failure_is_reported(monkeypatch: pytest.MonkeyPatch) -> None:
    def unreachable(**kwargs: object) -> tuple[object, ...]:
        raise OSError("no network")

    monkeypatch.setattr(_grids, "get_transform_grid_list", unreachable)
    _grids.remote_names.cache_clear()
    try:
        listing = catalog.grids(check_remote=True)
        with pytest.raises(GridIndexUnavailableError, match="no network"):
            len(listing)
    finally:
        _grids.remote_names.cache_clear()
