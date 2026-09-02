"""A bound CRS named by its code must still know its own transformation.

proj.db stores a bound CRS as a text definition on an ordinary CRS row. PROJ
honours that when selecting an operation but hands back an unwrapped object, so
the CRS can no longer say what it carries. These tests pin down that the
definition is read back, and just as importantly that reading it back never
disturbs an ordinary CRS.
"""

from __future__ import annotations

import shutil
import sqlite3
from collections.abc import Iterator
from pathlib import Path

import pyproj
import pytest
from pyproj import CRS
from pyproj.crs import BoundCRS, CoordinateOperation

from geodetic_engine.geodesy import (
    AmbiguousOperationError,
    CoordinateReferenceSystem,
    OperationRoute,
    Transformation,
)
from geodetic_engine.geodesy.database import bound_definition

AUTHORITY = "Example"
GEODETIC_CODE = "9100001"
PROJECTED_CODE = "9200001"
ED50_TO_WGS84 = ("EPSG", 1133)
OSLO_XY = (10.7522, 59.9139)


def _bound(base: int) -> str:
    """A BOUNDCRS WKT tying a base CRS to WGS 84 through EPSG:1133."""
    return str(
        BoundCRS(
            CRS.from_epsg(base),
            CRS.from_epsg(4326),
            CoordinateOperation.from_authority(*ED50_TO_WGS84),
        ).to_wkt()
    )


@pytest.fixture
def proj_data_with_bound_crs(tmp_path: Path) -> Iterator[Path]:
    """A PROJ data directory whose proj.db defines two bound CRSs.

    The database is a copy of the installed one with two rows added, which is
    how the projdb builder writes them: the whole BOUNDCRS WKT in
    text_definition, with the structured columns NULL.
    """
    directory = tmp_path / "proj"
    directory.mkdir()
    database = directory / "proj.db"
    shutil.copyfile(Path(pyproj.datadir.get_data_dir()) / "proj.db", database)

    with sqlite3.connect(database) as connection:
        connection.execute(
            "INSERT INTO geodetic_crs (auth_name, code, name, description, type, "
            "coordinate_system_auth_name, coordinate_system_code, datum_auth_name, "
            "datum_code, text_definition, deprecated) "
            "VALUES (?, ?, ?, NULL, 'geographic 2D', NULL, NULL, NULL, NULL, ?, 0)",
            (AUTHORITY, GEODETIC_CODE, "Example ED50 bound", _bound(4230)),
        )
        connection.execute(
            "INSERT INTO projected_crs (auth_name, code, name, description, "
            "coordinate_system_auth_name, coordinate_system_code, "
            "geodetic_crs_auth_name, geodetic_crs_code, conversion_auth_name, "
            "conversion_code, text_definition, deprecated) "
            "VALUES (?, ?, ?, NULL, NULL, NULL, NULL, NULL, NULL, NULL, ?, 0)",
            (AUTHORITY, PROJECTED_CODE, "Example ED50 UTM32 bound", _bound(23032)),
        )
        connection.commit()

    previous = pyproj.datadir.get_data_dir()
    pyproj.datadir.set_data_dir(f"{directory}{__import__('os').pathsep}{previous}")
    # The resolved CRS and the scanned definitions are both cached by input.
    _clear_caches()
    try:
        yield directory
    finally:
        pyproj.datadir.set_data_dir(previous)
        _clear_caches()


def _clear_caches() -> None:
    from geodetic_engine.geodesy import crs as crs_module
    from geodetic_engine.geodesy import database as database_module

    crs_module._cached.cache_clear()
    database_module._definitions.cache_clear()


def test_bound_definition_is_found_by_code(proj_data_with_bound_crs: Path) -> None:
    """The stored WKT is what PROJ will not give back."""
    definition = bound_definition(AUTHORITY, GEODETIC_CODE)

    assert definition is not None
    assert definition.startswith("BOUNDCRS[")


def test_proj_alone_loses_the_binding(proj_data_with_bound_crs: Path) -> None:
    """The behaviour this module exists to work around, pinned down.

    If a PROJ release starts preserving the wrapper this test fails, which is
    the signal that the workaround can be removed.
    """
    assert CRS.from_user_input(f"{AUTHORITY}:{GEODETIC_CODE}").is_bound is False


@pytest.mark.parametrize("code", [GEODETIC_CODE, PROJECTED_CODE])
def test_crs_named_by_code_is_bound_again(
    proj_data_with_bound_crs: Path, code: str
) -> None:
    """Resolving by code must give back the CRS the database describes."""
    crs = CoordinateReferenceSystem.from_user_input(f"{AUTHORITY}:{code}")

    assert crs.crs.is_bound


@pytest.mark.parametrize("code", [GEODETIC_CODE, PROJECTED_CODE])
def test_bound_crs_named_by_code_transforms_and_names_its_operation(
    proj_data_with_bound_crs: Path, code: str
) -> None:
    """Without the restoration this raises AmbiguousOperationError instead."""
    transformation = Transformation(f"{AUTHORITY}:{code}", "EPSG:4326")

    assert transformation.operation.route is OperationRoute.BOUND
    assert transformation.operation.authority_code == "EPSG:1133"


def test_result_matches_naming_the_operation(proj_data_with_bound_crs: Path) -> None:
    """Early binding must not quietly mean a different answer."""
    through_code = Transformation(
        f"{AUTHORITY}:{GEODETIC_CODE}", "EPSG:4326"
    ).transform([OSLO_XY])
    through_name = Transformation(
        "EPSG:4230", "EPSG:4326", operation="EPSG:1133"
    ).transform([OSLO_XY])

    assert through_code.coordinates == through_name.coordinates


def test_ordinary_crs_is_untouched(proj_data_with_bound_crs: Path) -> None:
    """A CRS that is not bound must resolve exactly as before."""
    crs = CoordinateReferenceSystem.from_user_input("EPSG:4326")

    assert not crs.crs.is_bound
    assert crs.axis_abbreviations == ("Lat", "Lon")
    with pytest.raises(AmbiguousOperationError):
        Transformation("EPSG:4230", "EPSG:4326")


def test_lookup_is_silent_without_a_database(tmp_path: Path) -> None:
    """A data directory with no proj.db must not break CRS resolution."""
    previous = pyproj.datadir.get_data_dir()
    empty = tmp_path / "empty"
    empty.mkdir()
    _clear_caches()
    try:
        pyproj.datadir.set_data_dir(str(empty))
        assert bound_definition(AUTHORITY, GEODETIC_CODE) is None
    finally:
        pyproj.datadir.set_data_dir(previous)
        _clear_caches()
