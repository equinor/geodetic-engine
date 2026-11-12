"""Listing methods, coordinate systems and grid files."""

from __future__ import annotations

import math
import os
import shutil
import sqlite3
from collections.abc import Iterator
from contextlib import closing
from dataclasses import replace
from pathlib import Path
from typing import Any

import pyproj
import pytest
from pyproj import Transformer
from pyproj.exceptions import ProjError

from geodetic_engine import catalog
from geodetic_engine.catalog import (
    CoordinateSystemKind,
    GridEntry,
    GridIndexUnavailableError,
    GridKind,
    InvalidFilterError,
    MethodKind,
    _grids,
)
from geodetic_engine.catalog._coordinate_systems import _users, direction
from geodetic_engine.catalog._registry import identity, reading
from tests.support import installed_proj_db


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
    assert conus.original_names == ("conus.las", "conus.los")
    assert conus.kind is GridKind.HORIZONTAL
    assert "EPSG:15851" in conus.used_by


def test_a_chain_does_not_lend_its_grids_to_another_file() -> None:
    # NAD27 to NAD83(HARN) by way of NAD83: two NADCON steps, four names.
    chain = catalog.operations(code="EPSG:8460").one()
    assert set(chain.grid_names) == {
        "conus.las",
        "conus.los",
        "alhpgn.las",
        "alhpgn.los",
    }
    listed = {grid.name: grid for grid in catalog.grids(operation=chain)}
    assert set(listed) == {"us_noaa_conus.tif", "us_noaa_alhpgn.tif"}
    assert listed["us_noaa_conus.tif"].original_names == ("conus.las", "conus.los")
    assert listed["us_noaa_alhpgn.tif"].original_names == ("alhpgn.las", "alhpgn.los")


def test_a_grid_takes_in_only_the_names_proj_maps_to_it() -> None:
    # Stated independently of the index: a name is listed under another file
    # where grid_alternatives says so, or where it is the second file of a
    # pair whose first file grid_alternatives maps there.
    with reading(identity()) as connection:
        proj_name = {
            str(original): str(proj)
            for original, proj in connection.execute(
                "SELECT original_grid_name, proj_grid_name FROM grid_alternatives"
            )
        }
        pairs = [
            (str(first), str(second))
            for first, second in connection.execute(
                "SELECT DISTINCT grid_name, grid2_name FROM grid_transformation "
                "WHERE grid2_name IS NOT NULL"
            )
        ]
    taken_in = 0
    for grid in catalog.grids():
        for stated in grid.original_names:
            taken_in += 1
            if proj_name.get(stated) == grid.name:
                continue
            firsts = {first for first, second in pairs if second == stated}
            assert firsts, (grid.name, stated)
            assert {proj_name.get(first) for first in firsts} == {grid.name}, (
                grid.name,
                stated,
            )
    assert taken_in > 300


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


_PIPELINE = (
    "+proj=pipeline +step +proj=axisswap +order=2,1 "
    "+step +proj=unitconvert +xy_in=deg +xy_out=rad "
    "+step +proj=hgridshift +grids={grids} "
    "+step +proj=unitconvert +xy_in=rad +xy_out=deg +step +proj=axisswap +order=2,1"
)
# PROJ-string operations from NAD27 to NAD83, and the grids each states.
_STATED = {
    "NEEDED": "test_regional.gsb",
    "OPTIONAL": "@test_regional.gsb",
    "OPTIONAL_THEN_NULL": "@test_regional.gsb,null",
    "NULL": "null",
    "NEEDED_THEN_NULL": "test_regional.gsb,null",
    "OPTIONAL_AND_NEEDED": "@test_regional.gsb,test_national.gsb",
}
# The same from NAD83 on to NAD83(HARN), to chain after one of those.
_ONWARD = {"ONWARD_NEEDED": "test_regional.gsb", "ONWARD_OPTIONAL": "@test_other.gsb"}
_CHAINS = {
    "CHAIN_OPTIONAL": ("OPTIONAL", "ONWARD_OPTIONAL"),
    "CHAIN_NEEDED": ("OPTIONAL", "ONWARD_NEEDED"),
}


def _insert(connection: sqlite3.Connection, table: str, row: dict[str, Any]) -> None:
    marks = ", ".join("?" for _ in row)
    connection.execute(
        f"INSERT INTO {table} ({', '.join(row)}) VALUES ({marks})",
        tuple(row.values()),
    )


def _register_stated_grids(database: Path) -> None:
    """Register PROJ-string operations stating optional grids, and chains of them."""
    with closing(sqlite3.connect(database)) as connection:
        connection.row_factory = sqlite3.Row
        single = dict(
            connection.execute(
                "SELECT * FROM other_transformation WHERE method_auth_name = 'PROJ' "
                "AND method_code = 'PROJString' LIMIT 1"
            ).fetchone()
        )
        for stated, source, target in ((_STATED, 4267, 4269), (_ONWARD, 4269, 4152)):
            for code, grids in stated.items():
                row = {
                    **single,
                    "auth_name": "PROJ",
                    "code": code,
                    "name": code,
                    "method_name": _PIPELINE.format(grids=grids),
                    "source_crs_auth_name": "EPSG",
                    "source_crs_code": source,
                    "target_crs_auth_name": "EPSG",
                    "target_crs_code": target,
                }
                _insert(connection, "other_transformation", row)
        # EPSG:8460 goes from NAD27 to NAD83(HARN) by way of NAD83.
        chain = dict(
            connection.execute(
                "SELECT * FROM concatenated_operation "
                "WHERE auth_name = 'EPSG' AND code = 8460"
            ).fetchone()
        )
        for code, steps in _CHAINS.items():
            row = {**chain, "auth_name": "PROJ", "code": code, "name": code}
            _insert(connection, "concatenated_operation", row)
            for number, step in enumerate(steps, start=1):
                connection.execute(
                    "INSERT INTO concatenated_operation_step "
                    "VALUES ('PROJ', ?, ?, 'PROJ', ?, NULL)",
                    (code, number, step),
                )
        connection.commit()


@pytest.fixture(scope="module")
def database_alone(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """A directory holding no grid, and PROJ's database with a few operations added."""
    directory = tmp_path_factory.mktemp("database")
    shutil.copyfile(installed_proj_db(), directory / "proj.db")
    _register_stated_grids(directory / "proj.db")
    return directory


@pytest.fixture
def lone_grid_directory(
    database_alone: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Iterator[Path]:
    """PROJ's search path cut down to its database and one empty directory."""
    grids = tmp_path / "grids"
    grids.mkdir()
    monkeypatch.setattr(
        _grids.datadir, "get_user_data_dir", lambda *_: str(tmp_path / "user")
    )
    monkeypatch.setattr(_grids, "is_network_enabled", lambda: False)
    previous = pyproj.datadir.get_data_dir()
    pyproj.datadir.set_data_dir(f"{database_alone}{os.pathsep}{grids}")
    try:
        yield grids
    finally:
        pyproj.datadir.set_data_dir(previous)


def test_a_grid_is_installed_only_under_a_name_proj_reads(
    lone_grid_directory: Path,
) -> None:
    def conus() -> tuple[bool, bool]:
        grid = catalog.grids(operation="EPSG:1241").one()
        operation = catalog.operations(code="EPSG:1241").one()
        return grid.installed, operation.grids_available()

    assert conus() == (False, False)
    # The files NADCON publishes, which PROJ cannot read, singly or together.
    (lone_grid_directory / "conus.las").write_bytes(b"")
    assert conus() == (False, False)
    (lone_grid_directory / "conus.los").write_bytes(b"")
    assert conus() == (False, False)
    # PROJ's older release of the grid, which PROJ falls back to.
    (lone_grid_directory / "conus").write_bytes(b"")
    assert conus() == (True, True)
    (lone_grid_directory / "conus").unlink()
    assert conus() == (False, False)
    (lone_grid_directory / "us_noaa_conus.tif").write_bytes(b"")
    assert conus() == (True, True)


def test_an_operation_names_the_file_proj_reads_and_where_it_is(
    lone_grid_directory: Path,
) -> None:
    operation = catalog.operations(code="EPSG:1241").one()
    (missing,) = operation.grids()
    assert (missing.name, missing.available, missing.full_name) == (
        "us_noaa_conus.tif",
        False,
        "",
    )
    assert missing.url.endswith("us_noaa_conus.tif")
    installed = lone_grid_directory / "us_noaa_conus.tif"
    installed.write_bytes(b"")
    (found,) = operation.grids()
    assert (found.available, found.full_name) == (True, str(installed))


def test_a_grid_the_database_does_not_know_is_looked_for_not_assumed(
    lone_grid_directory: Path,
) -> None:
    # An entry outlives a database switch, so it may name a grid the active
    # database states nowhere.
    kept = replace(
        catalog.operations(code="EPSG:1241").one(), grid_names=("elsewhere.gsb",)
    )
    (unknown,) = kept.grids()
    assert (unknown.name, unknown.available) == ("elsewhere.gsb", False)
    assert not kept.grids_available()
    (lone_grid_directory / "elsewhere.gsb").write_bytes(b"")
    assert kept.grids_available()


def test_with_proj_networking_a_downloadable_grid_is_available_not_installed(
    lone_grid_directory: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(_grids, "is_network_enabled", lambda: True)
    assert catalog.operations(code="EPSG:1241").one().grids_available()
    assert "EPSG:1241" in {
        e.authority_code
        for e in catalog.operations(method="NADCON", grids_available=True)
    }
    assert not catalog.grids(operation="EPSG:1241").one().installed


def test_with_proj_networking_a_url_alone_does_not_make_a_grid_available(
    lone_grid_directory: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(_grids, "is_network_enabled", lambda: True)
    # The RDNAP grid has a URL to download from, but no open licence, and
    # PROJ's network does not hold it.
    rdnap = catalog.operations(code="EPSG:7000").one()
    (grid,) = rdnap.grids()
    assert grid.url and grid.direct_download and not grid.open_license
    assert not grid.available
    assert not rdnap.grids_available()
    listed = catalog.operations(method=rdnap.method, grids_available=True)
    assert "EPSG:7000" not in _codes(listed)


@pytest.mark.parametrize(
    ("url", "direct_download", "open_license", "expected"),
    [
        ("https://cdn.proj.org/some.tif", True, True, True),
        ("https://example.org/register-to-download", False, True, False),
        ("https://example.org/some.gsb", True, False, False),
        ("https://example.org/some.gsb", True, None, False),
        (None, True, True, False),
    ],
)
def test_proj_downloads_only_a_direct_download_under_an_open_licence(
    url: str | None, direct_download: bool, open_license: bool | None, expected: bool
) -> None:
    grid = GridEntry(
        name="some.tif",
        kind=GridKind.UNKNOWN,
        url=url,
        direct_download=direct_download,
        open_license=open_license,
    )
    assert _grids._obtainable(grid, True) is expected
    assert not _grids._obtainable(grid, False)


@pytest.mark.parametrize(
    ("code", "names", "optional", "available"),
    [
        ("NEEDED", ("test_regional.gsb",), (), False),
        ("OPTIONAL", ("test_regional.gsb",), ("test_regional.gsb",), True),
        ("OPTIONAL_THEN_NULL", ("test_regional.gsb",), ("test_regional.gsb",), True),
        ("NULL", (), (), True),
        ("NEEDED_THEN_NULL", ("test_regional.gsb",), (), False),
        (
            "OPTIONAL_AND_NEEDED",
            ("test_regional.gsb", "test_national.gsb"),
            ("test_regional.gsb",),
            False,
        ),
    ],
)
def test_an_optional_grid_is_listed_but_not_needed(
    lone_grid_directory: Path,
    code: str,
    names: tuple[str, ...],
    optional: tuple[str, ...],
    available: bool,
) -> None:
    entry = catalog.operations(code=f"PROJ:{code}").one()
    assert (entry.grid_names, entry.optional_grid_names) == (names, optional)
    assert entry.grid_based is bool(names)
    assert [grid.name for grid in entry.grids()] == list(names)
    assert entry.grids_available() is available
    listed = catalog.operations(authority="PROJ", grids_available=True)
    assert (entry.authority_code in _codes(listed)) is available
    # PROJ is the judge: with no grid installed, it builds the pipeline or not.
    try:
        Transformer.from_pipeline(_PIPELINE.format(grids=_STATED[code]))
    except ProjError:
        builds = False
    else:
        builds = True
    assert available is builds


def test_an_optional_grid_is_a_grid_file_and_the_null_grid_is_not(
    lone_grid_directory: Path,
) -> None:
    regional = catalog.grids(operation="PROJ:OPTIONAL").one()
    assert regional.name == "test_regional.gsb"
    assert {"PROJ:OPTIONAL", "PROJ:OPTIONAL_THEN_NULL"} <= set(regional.used_by)
    assert all(grid.name != "null" for grid in catalog.grids())
    needed = catalog.operations(code="PROJ:OPTIONAL_AND_NEEDED").one()
    (lone_grid_directory / "test_regional.gsb").write_bytes(b"")
    assert not needed.grids_available()
    (lone_grid_directory / "test_national.gsb").write_bytes(b"")
    assert needed.grids_available()


def test_a_chain_needs_the_grids_any_of_its_steps_needs(
    lone_grid_directory: Path,
) -> None:
    optional = catalog.operations(code="PROJ:CHAIN_OPTIONAL").one()
    assert optional.grid_names == ("test_regional.gsb", "test_other.gsb")
    assert optional.optional_grid_names == optional.grid_names
    assert optional.grids_available()
    # The grid one step can run without, the next step cannot.
    needed = catalog.operations(code="PROJ:CHAIN_NEEDED").one()
    assert needed.grid_names == ("test_regional.gsb",)
    assert needed.optional_grid_names == ()
    assert not needed.grids_available()


def test_optional_grids_are_exported(
    lone_grid_directory: Path, listing_validator: Any
) -> None:
    document = catalog.operations(code="PROJ:OPTIONAL_AND_NEEDED").to_json_dict()
    listing_validator.validate(document)
    (item,) = document["items"]
    assert item["grids"] == ["test_regional.gsb", "test_national.gsb"]
    assert item["optional_grids"] == ["test_regional.gsb"]
    (plain,) = catalog.operations(code="PROJ:NEEDED").to_json_dict()["items"]
    assert "optional_grids" not in plain


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
