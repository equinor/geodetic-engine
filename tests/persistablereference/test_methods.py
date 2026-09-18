"""The ESRI to EPSG tables, checked against the database PROJ itself ships.

These tables are the only place one vocabulary is turned into the other, and a
wrong code in them produces coordinates that are plausible and wrong rather
than an error. So every code is checked against a real transformation in
proj.db: if EPSG renumbers something, or a code was mistyped, this fails rather
than a datum shift quietly moving.
"""

from __future__ import annotations

import os
import sqlite3
from pathlib import Path

import pytest
from pyproj import datadir
from pyproj.crs import CoordinateOperation

from geodetic_engine.persistablereference import (
    UnresolvableGridError,
    UnsupportedMethodError,
)
from geodetic_engine.persistablereference import methods as mt

_TABLES = ("helmert_transformation", "grid_transformation", "other_transformation")


@pytest.fixture(scope="session")
def database() -> sqlite3.Connection:
    """PROJ's own database, read only."""
    for directory in datadir.get_data_dir().split(os.pathsep):
        path = Path(directory) / "proj.db"
        if path.is_file():
            return sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    pytest.skip("PROJ's database is not on the data path")


def _sample(database: sqlite3.Connection, code: int) -> CoordinateOperation:
    """Any operation PROJ defines that uses one method."""
    for table in _TABLES:
        found = database.execute(
            f"SELECT auth_name, code FROM {table} "
            "WHERE method_auth_name = 'EPSG' AND method_code = ? "
            "AND deprecated = 0 LIMIT 1",
            (str(code),),
        ).fetchone()
        if found:
            return CoordinateOperation.from_authority(*found)
    pytest.fail(f"PROJ's database defines no operation using EPSG method {code}")


@pytest.mark.parametrize("esri", sorted(mt.METHODS))
def test_method_codes_name_what_we_say_they_name(
    esri: str, database: sqlite3.Connection
) -> None:
    """Each EPSG method code carries the name the table claims for it."""
    method = mt.METHODS[esri]
    assert _sample(database, method.code).method_name == method.name


@pytest.mark.parametrize("esri", sorted(mt.METHODS))
def test_parameter_codes_name_what_we_say_they_name(
    esri: str, database: sqlite3.Connection
) -> None:
    """Each parameter a method takes is the EPSG parameter the table claims.

    Units are deliberately not compared. EPSG states some of these in
    microradians or parts per billion; ESRI states every one of them in one
    fixed unit, and recording that is the table's job.
    """
    method = mt.METHODS[esri]
    stated = {
        int(parameter["id"]["code"]): parameter["name"]
        for parameter in _sample(database, method.code).to_json_dict()["parameters"]
    }
    declared = {
        mt.PARAMETERS[name].code: mt.PARAMETERS[name].name for name in method.parameters
    }
    assert declared == stated


@pytest.mark.parametrize("esri", sorted(mt.METHODS))
def test_every_method_is_written_back_under_a_name_it_is_read_by(esri: str) -> None:
    """A method written out is a method that reads again."""
    assert mt.esri_method(mt.METHODS[esri].code) in mt.METHODS


def test_the_two_names_for_seven_parameters_agree() -> None:
    """ESRI's older name for the position vector method is the same method."""
    assert mt.METHODS["Bursa_Wolf"] == mt.METHODS["Position_Vector"]
    assert mt.esri_method(9606) == "Position_Vector"


@pytest.mark.parametrize(
    ("dataset", "expected"),
    [
        ("Dataset_conus", "NADCON"),
        ("conus", "NADCON"),
        ("Dataset_canada/Ntv2_0", "NTv2"),
        ("Dataset_australia/A66_National_13_09_01", "NTv2"),
        ("A66 National (13.09.01).gsb", "NTv2"),
    ],
)
def test_grid_datasets_resolve_however_they_are_spelt(
    dataset: str, expected: str
) -> None:
    """Every dialect punctuates a grid name differently; the letters agree."""
    assert mt.grid(dataset).method_name == expected


def test_nadcon_resolves_to_both_of_its_files() -> None:
    """EPSG states one file per direction where ESRI states one dataset."""
    files = mt.grid("Dataset_conus").files
    assert [name for _, _, name in files] == ["conus.las", "conus.los"]
    assert [code for code, _, _ in files] == ["8657", "8658"]


def test_an_unknown_grid_is_refused() -> None:
    """A grid PROJ has no definition for is an error, not a guess."""
    with pytest.raises(UnresolvableGridError):
        mt.grid("Dataset_no_such_grid_anywhere")


@pytest.mark.parametrize("esri", sorted(mt.REFUSED))
def test_refused_methods_say_why(esri: str) -> None:
    """A refusal names the method and the reason, so it is not worked around."""
    with pytest.raises(UnsupportedMethodError, match="because"):
        mt.method(esri)


def test_an_unknown_method_lists_the_known_ones() -> None:
    """The error says what would have been accepted."""
    with pytest.raises(UnsupportedMethodError, match="Position_Vector"):
        mt.method("Something_Invented")


def test_a_grid_method_is_not_translated_by_parameters() -> None:
    """Its definition comes from PROJ's database rather than from the table."""
    assert mt.is_grid_method("NADCON")
    with pytest.raises(UnsupportedMethodError, match="reads a grid"):
        mt.method("NADCON")


@pytest.mark.parametrize(
    ("unit", "expected"),
    [("metre", 1.0), ("unity", 1.0), (mt.ARC_SECOND, 4.84813681109536e-06)],
)
def test_units_state_their_factor(unit: mt.Unit, expected: float) -> None:
    """Restating a value needs the factor of the unit it is stated in."""
    assert mt.factor(unit) == pytest.approx(expected)


def test_a_unit_without_a_factor_is_refused() -> None:
    """Guessing one would silently rescale a parameter."""
    with pytest.raises(UnsupportedMethodError, match="conversion factor"):
        mt.factor({"type": "AngularUnit", "name": "invented"})
