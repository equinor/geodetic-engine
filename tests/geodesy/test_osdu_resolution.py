"""Resolving CRSs and operations from OSDU record ids.

The database here is a copy of the installed proj.db with identifiers added the
way a build from an OSDU catalogue adds them, written by hand so each test
states exactly what the database records.
"""

from __future__ import annotations

import json
import os
import shutil
import sqlite3
from collections.abc import Iterator
from contextlib import closing
from pathlib import Path

import pyproj
import pytest
from pyproj import CRS
from pyproj.crs import BoundCRS, CoordinateOperation

from geodetic_engine.geodesy import (
    CoordinateReferenceSystem,
    OperationNotAvailableError,
    OperationRequest,
    Transformation,
    UnresolvableCRSError,
)
from geodetic_engine.geodesy import database as database_module
from geodetic_engine.geodesy.database import (
    grid_dataset,
    identified_object,
    object_identifiers,
    recorded_identifiers,
)
from geodetic_engine.geodesy.osdu_id import CRS_ENTITY, SCHEME, TRANSFORMATION_ENTITY
from geodetic_engine.projdb import schema
from tests.support import URI_SYNTAX_DIRECTORIES, installed_proj_db

NAMESPACE = "opendes"
BOUND_AUTHORITY = "OSDU"
BOUND_CODE = "23031001"

PROJECTED = f"{CRS_ENTITY}:Projected:EPSG::23031"
BOUND = f"{CRS_ENTITY}:BoundProjected:EPSG::23031_EPSG::1133"
SKIPPED = f"{CRS_ENTITY}:BoundProjected:EPSG::27573_EPSG::8094"
ODD = f"{CRS_ENTITY}:Projected:ACME:123"
TRANSFORMATION = f"{TRANSFORMATION_ENTITY}:EPSG::1612"

POINT = (500000.0, 6600000.0)


def _write(directory: Path) -> Path:
    """A proj.db that records OSDU ids as a build from a catalogue would."""
    directory.mkdir()
    database = directory / "proj.db"
    shutil.copyfile(installed_proj_db(), database)
    bound = BoundCRS(
        CRS.from_epsg(23031),
        CRS.from_epsg(4326),
        CoordinateOperation.from_authority("EPSG", 1133),
    ).to_wkt()
    with closing(sqlite3.connect(database)) as connection, connection:
        connection.execute(
            "INSERT INTO projected_crs (auth_name, code, name, description, "
            "coordinate_system_auth_name, coordinate_system_code, "
            "geodetic_crs_auth_name, geodetic_crs_code, conversion_auth_name, "
            "conversion_code, text_definition, deprecated) "
            "VALUES (?, ?, ?, NULL, NULL, NULL, NULL, NULL, NULL, NULL, ?, 0)",
            (
                BOUND_AUTHORITY,
                BOUND_CODE,
                "ED50 * DMA-mean / UTM zone 31N [23031,1133]",
                bound,
            ),
        )
        for statement in schema.IDENTIFIER_DDL:
            connection.execute(statement)
        identifiers = [
            (PROJECTED, "projected_crs", "EPSG", "23031"),
            (BOUND, "projected_crs", BOUND_AUTHORITY, BOUND_CODE),
            (ODD, "geodetic_crs", "EPSG", "4326"),
            (TRANSFORMATION, "helmert_transformation", "EPSG", "1612"),
        ]
        connection.executemany(
            f"INSERT INTO {schema.IDENTIFIER_TABLE} (scheme, identifier, "
            "table_name, auth_name, code, deprecated, source) "
            "VALUES (?, ?, ?, ?, ?, 0, 'CRS_CT.json')",
            [(SCHEME, *row) for row in identifiers],
        )
        connection.executemany(
            "INSERT INTO alias_name (table_name, auth_name, code, alt_name, source) "
            "VALUES (?, ?, ?, ?, ?)",
            [
                (table, auth, code, identifier, schema.identifier_alias_source(SCHEME))
                for identifier, table, auth, code in identifiers
            ],
        )
        connection.execute(
            f"CREATE TABLE {schema.BUILD_HISTORY_TABLE} "
            "(sequence INTEGER PRIMARY KEY, report TEXT NOT NULL)"
        )
        connection.execute(
            f"INSERT INTO {schema.BUILD_HISTORY_TABLE} (report) VALUES (?)",
            (
                json.dumps(
                    {
                        "skipped": [
                            {
                                "table": "projected_crs",
                                "auth_name": BOUND_AUTHORITY,
                                "code": "27573001",
                                "name": "NTF (Paris) * EPSG-Fra / Lambert zone III",
                                "reason": "EPSG:8094 does not collapse",
                                "identifier": SKIPPED,
                            }
                        ]
                    }
                ),
            ),
        )
    return database


def _reading(directory: Path) -> Iterator[None]:
    previous = pyproj.datadir.get_data_dir()
    pyproj.datadir.set_data_dir(f"{directory}{os.pathsep}{previous}")
    try:
        yield
    finally:
        pyproj.datadir.set_data_dir(previous)


@pytest.fixture
def osdu_database(tmp_path: Path) -> Iterator[Path]:
    """PROJ reading a database that records OSDU ids."""
    database = _write(tmp_path / "proj")
    yield from _reading(database.parent)


@pytest.fixture(params=URI_SYNTAX_DIRECTORIES)
def osdu_database_named_as_uri_syntax(
    tmp_path: Path, request: pytest.FixtureRequest
) -> Iterator[None]:
    """PROJ reading that database from a directory named with URI syntax."""
    database = _write(tmp_path / request.param)
    yield from _reading(database.parent)


@pytest.fixture
def stock_database(tmp_path: Path) -> Iterator[Path]:
    """PROJ reading a database no OSDU build has written."""
    directory = tmp_path / "stock"
    directory.mkdir()
    shutil.copyfile(installed_proj_db(), directory / "proj.db")
    yield from _reading(directory)


@pytest.fixture
def legacy_identifier_database(tmp_path: Path) -> Iterator[Path]:
    """PROJ reading a database with the former identifier table name."""
    directory = tmp_path / "legacy"
    directory.mkdir()
    database = directory / "proj.db"
    shutil.copyfile(installed_proj_db(), database)
    with closing(sqlite3.connect(database)) as connection, connection:
        connection.execute(
            f"""CREATE TABLE {schema.LEGACY_IDENTIFIER_TABLE}(
                scheme TEXT NOT NULL,
                identifier TEXT NOT NULL,
                table_name TEXT NOT NULL,
                auth_name TEXT NOT NULL,
                code INTEGER_OR_TEXT NOT NULL,
                deprecated BOOLEAN NOT NULL,
                source TEXT,
                PRIMARY KEY (scheme, identifier)
            ) WITHOUT ROWID"""
        )
        connection.execute(
            f"INSERT INTO {schema.LEGACY_IDENTIFIER_TABLE} "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                SCHEME,
                PROJECTED,
                "projected_crs",
                "EPSG",
                23031,
                0,
                "CRS_CT.json",
            ),
        )
    yield from _reading(directory)


def test_the_table_name_is_spelled_alike_in_both_packages() -> None:
    assert database_module._IDENTIFIER_TABLE == schema.IDENTIFIER_TABLE


def test_a_database_with_the_legacy_table_name_still_resolves_ids(
    legacy_identifier_database: Path,
) -> None:
    found = identified_object(SCHEME, PROJECTED)
    assert found is not None
    assert (found.table_name, found.auth_name, found.code) == (
        "projected_crs",
        "EPSG",
        "23031",
    )


@pytest.mark.parametrize(
    "value",
    [
        f"{NAMESPACE}:{PROJECTED}",
        f"another-partition:{PROJECTED}",
        f"{{{{NAMESPACE}}}}:{PROJECTED}",
        PROJECTED,
        f"{NAMESPACE}:{PROJECTED}:",
        f"{NAMESPACE}:{PROJECTED}:1562066009929332",
    ],
)
def test_an_id_resolves_in_any_partition_and_at_any_version(
    osdu_database: Path, value: str
) -> None:
    crs = CoordinateReferenceSystem.from_user_input(value)
    assert crs.authority_code == "EPSG:23031"
    assert crs.definition == value.strip()


def test_a_bare_data_id_resolves_where_a_crs_is_asked_for(
    osdu_database: Path,
) -> None:
    crs = CoordinateReferenceSystem.from_osdu_id("Projected:EPSG::23031")
    assert crs.authority_code == "EPSG:23031"


def test_a_bound_crs_id_resolves_to_the_bound_crs(osdu_database: Path) -> None:
    """PROJ alone would hand back the unwrapped base CRS."""
    crs = CoordinateReferenceSystem.from_user_input(f"{NAMESPACE}:{BOUND}")
    assert crs.crs.is_bound
    assert crs.crs.to_json_dict()["transformation"]["id"] == {
        "authority": "EPSG",
        "code": 1133,
    }


def test_a_bound_crs_id_applies_its_own_transformation(osdu_database: Path) -> None:
    by_id = Transformation(f"{NAMESPACE}:{BOUND}", "EPSG:4326").transform([POINT])
    by_code = Transformation(f"{BOUND_AUTHORITY}:{BOUND_CODE}", "EPSG:4326").transform(
        [POINT]
    )
    assert by_id.operation.authority_code == "EPSG:1133"
    assert by_id.coordinates.to_list() == by_code.coordinates.to_list()


def test_an_id_as_given_is_matched_before_its_version_is_dropped(
    osdu_database: Path,
) -> None:
    """An operator's id may itself end in a colon and digits."""
    crs = CoordinateReferenceSystem.from_user_input(f"{NAMESPACE}:{ODD}")
    assert crs.authority_code == "EPSG:4326"


def test_a_near_miss_is_refused_rather_than_matched(osdu_database: Path) -> None:
    """PROJ's name search would resolve this alias typo to EPSG:1133's binding."""
    typo = f"{NAMESPACE}:{CRS_ENTITY}:BoundProjected:EPSG::23031_EPSG::113"
    with pytest.raises(UnresolvableCRSError, match="records no OSDU id"):
        CoordinateReferenceSystem.from_user_input(typo)


def test_proj_finds_the_alias_on_its_own(osdu_database: Path) -> None:
    """The alias is what lets a tool reading proj.db directly use an id."""
    assert CRS(PROJECTED).to_authority() == ("EPSG", "23031")


def test_ordinary_names_are_unaffected_by_the_aliases(osdu_database: Path) -> None:
    assert CRS("ED50 / UTM zone 31N").to_authority() == ("EPSG", "23031")
    assert CRS("WGS 84").to_authority() == ("EPSG", "4326")


def test_an_id_the_build_left_out_says_why(osdu_database: Path) -> None:
    with pytest.raises(UnresolvableCRSError, match="EPSG:8094 does not collapse"):
        CoordinateReferenceSystem.from_user_input(f"{NAMESPACE}:{SKIPPED}:")


def test_a_database_without_ids_says_how_to_get_them(stock_database: Path) -> None:
    with pytest.raises(
        UnresolvableCRSError,
        match=r"active_database\(\) says why .* use_bundled_database\(\) switches "
        r"it on\. .* geodetic-osdudb",
    ):
        CoordinateReferenceSystem.from_user_input(f"{NAMESPACE}:{PROJECTED}")


def test_a_transformation_id_is_not_a_crs(osdu_database: Path) -> None:
    with pytest.raises(UnresolvableCRSError, match="not of a CRS"):
        CoordinateReferenceSystem.from_user_input(f"{NAMESPACE}:{TRANSFORMATION}")
    with pytest.raises(UnresolvableCRSError, match="not of a CRS"):
        CoordinateReferenceSystem.from_osdu_id(f"{NAMESPACE}:{TRANSFORMATION}")


def test_from_osdu_id_refuses_what_is_not_an_id(osdu_database: Path) -> None:
    with pytest.raises(UnresolvableCRSError, match="not an OSDU record id"):
        CoordinateReferenceSystem.from_osdu_id("Projected EPSG 23031")


def test_a_transformation_id_names_the_operation(osdu_database: Path) -> None:
    request = OperationRequest.parse(f"{NAMESPACE}:{TRANSFORMATION}:")
    assert (request.auth_name, request.code) == ("EPSG", "1612")
    assert request.text == f"{NAMESPACE}:{TRANSFORMATION}:"


def test_a_manifest_placeholder_partition_is_not_read_as_a_payload(
    osdu_database: Path,
) -> None:
    """``{{NAMESPACE}}`` opens with a brace, as a JSON payload does."""
    request = OperationRequest.parse(f"{{{{NAMESPACE}}}}:{TRANSFORMATION}")
    assert (request.auth_name, request.code) == ("EPSG", "1612")


def test_a_transformation_id_pins_the_operation_applied(osdu_database: Path) -> None:
    by_id = Transformation(
        f"{NAMESPACE}:{PROJECTED}",
        "EPSG:4326",
        operation=f"{NAMESPACE}:{TRANSFORMATION}",
    )
    by_code = Transformation("EPSG:23031", "EPSG:4326", operation="EPSG:1612")
    assert by_id.operation.authority_code == "EPSG:1612"
    assert (
        by_id.transform([POINT]).coordinates.to_list()
        == by_code.transform([POINT]).coordinates.to_list()
    )


def test_a_crs_id_is_not_an_operation(osdu_database: Path) -> None:
    with pytest.raises(ValueError, match="not of a coordinate transformation"):
        OperationRequest.parse(f"{NAMESPACE}:{PROJECTED}")


def test_an_unknown_transformation_id_is_not_available(osdu_database: Path) -> None:
    with pytest.raises(OperationNotAvailableError, match="records no OSDU id"):
        OperationRequest.parse(f"{NAMESPACE}:{TRANSFORMATION_ENTITY}:EPSG::161")


def test_an_object_can_be_asked_for_its_ids(osdu_database: Path) -> None:
    assert object_identifiers("projected_crs", "EPSG", "23031", scheme=SCHEME) == (
        PROJECTED,
    )
    assert object_identifiers(
        "projected_crs", BOUND_AUTHORITY, BOUND_CODE, scheme=SCHEME
    ) == (BOUND,)
    assert object_identifiers("projected_crs", "EPSG", "23032", scheme=SCHEME) == ()


def test_a_lookup_returns_what_the_row_states(osdu_database: Path) -> None:
    found = identified_object(SCHEME, TRANSFORMATION)
    assert found is not None
    assert found.authority_code == "EPSG:1612"
    assert found.is_operation and not found.is_crs
    assert not found.deprecated
    assert identified_object(SCHEME, f"{NAMESPACE}:{TRANSFORMATION}") is None


def test_every_lookup_reads_the_database_proj_reads_wherever_it_is(
    osdu_database_named_as_uri_syntax: None, tmp_path: Path
) -> None:
    """A path is not a URI: "#", "?" and "%" in it name the directory."""
    crs = CoordinateReferenceSystem.from_osdu_id(f"{NAMESPACE}:{PROJECTED}")
    assert crs.authority_code == "EPSG:23031"
    bound = CoordinateReferenceSystem.from_user_input(f"{NAMESPACE}:{BOUND}")
    assert bound.crs.is_bound
    with pytest.raises(UnresolvableCRSError, match="EPSG:8094 does not collapse"):
        CoordinateReferenceSystem.from_user_input(f"{NAMESPACE}:{SKIPPED}:")
    assert object_identifiers("projected_crs", "EPSG", "23031", scheme=SCHEME) == (
        PROJECTED,
    )
    assert recorded_identifiers(SCHEME, code="23031") == (PROJECTED,)
    assert grid_dataset("conus") is not None
    # Reading the path as a URI opened, and so created, a database named after
    # the part before the "#" or "?", read-write: the fragment or query had
    # swallowed "mode=ro". The directory holding the database is all there is.
    assert len(list(tmp_path.iterdir())) == 1
