"""Annotations this authority puts on objects owned by someone else.

A register records what an organisation calls EPSG:32632 and what it uses it
for. Those rows must reach the database without the EPSG object itself being
touched, and must never point at an object the database does not hold.
"""

from __future__ import annotations

import sqlite3
from typing import Any

from geodetic_engine.georepository.client import GeorepositoryClient
from geodetic_engine.projdb.build import build
from geodetic_engine.projdb.config import ProjDbBuildConfig
from tests.projdb.conftest import (
    API,
    AUTHORITY,
    EPSG_SCOPE,
    EPSG_WORLD_EXTENT,
    FakeGeorepository,
)

# An EPSG CRS that the base proj.db already defines, so annotating it is legal.
EPSG_CRS = 4326
# One this authority has its own name and scope for.
CUSTOM_SCOPE = 5000
CUSTOM_EXTENT = 5001
# A CRS the base database does not hold, which must not be annotated.
ABSENT_CRS = 999999


def _summary(endpoint: str, code: int, authority: str) -> dict[str, Any]:
    return {
        "Code": code,
        "DataSource": authority,
        "Links": [{"rel": "self", "href": f"{API}/api/v1/{endpoint}/{code}"}],
    }


def _register(*, scope_authority: str, crs_code: int = EPSG_CRS) -> FakeGeorepository:
    """An EPSG CRS carrying one usage whose scope belongs to `scope_authority`."""
    fake = FakeGeorepository(
        {
            "GeodeticCoordRefSystem": [
                _summary("GeodeticCoordRefSystem", crs_code, "EPSG")
            ]
        }
    )
    fake.add_object(
        f"/api/v1/GeodeticCoordRefSystem/{crs_code}",
        {
            "Code": crs_code,
            "DataSource": "EPSG",
            "Name": "WGS 84",
            "Usage": [
                {
                    "Scope": {"href": f"{API}/api/v1/Scope/{CUSTOM_SCOPE}"},
                    "Extent": {"href": f"{API}/api/v1/Extent/{CUSTOM_EXTENT}"},
                }
            ],
        },
    )
    fake.add_aliases(
        f"/api/v1/GeodeticCoordRefSystem/{crs_code}",
        [{"Alias": "Company WGS 84", "NamingSystem": {"Name": AUTHORITY}}],
    )
    fake.add_object(
        f"/api/v1/Scope/{CUSTOM_SCOPE}",
        {"Code": CUSTOM_SCOPE, "DataSource": scope_authority, "Name": "Company survey"},
    )
    fake.add_object(
        f"/api/v1/Extent/{CUSTOM_EXTENT}",
        {
            "Code": CUSTOM_EXTENT,
            "DataSource": scope_authority,
            "Name": "Company area",
            "Description": "Company operating area",
            "BoundingBoxSouthBoundLatitude": 50.0,
            "BoundingBoxNorthBoundLatitude": 75.0,
            "BoundingBoxWestBoundLongitude": -5.0,
            "BoundingBoxEastBoundLongitude": 35.0,
        },
    )
    return fake


def _build(config: ProjDbBuildConfig, fake: FakeGeorepository):
    client = GeorepositoryClient(config.georepository, transport=fake.transport())
    return build(config, client=client)


def _query(config: ProjDbBuildConfig, sql: str, *args: Any) -> list[tuple[Any, ...]]:
    with sqlite3.connect(f"file:{config.output_db}?mode=ro", uri=True) as connection:
        return connection.execute(sql, args).fetchall()


def test_custom_scope_is_attached_to_an_epsg_crs(config: ProjDbBuildConfig) -> None:
    """The usage points at the EPSG object but carries this authority's scope."""
    _build(config, _register(scope_authority=AUTHORITY))

    rows = _query(
        config,
        "SELECT object_auth_name, CAST(object_code AS TEXT), scope_auth_name, "
        "extent_auth_name FROM usage WHERE auth_name = ? AND object_code = ?",
        AUTHORITY,
        str(EPSG_CRS),
    )

    assert rows == [("EPSG", str(EPSG_CRS), AUTHORITY, AUTHORITY)]


def test_the_epsg_object_itself_is_not_rewritten(config: ProjDbBuildConfig) -> None:
    """Annotating must not re-import or alter another authority's row."""
    report = _build(config, _register(scope_authority=AUTHORITY))

    with sqlite3.connect(f"file:{config.base_proj_db}?mode=ro", uri=True) as base:
        before = base.execute(
            "SELECT COUNT(*) FROM geodetic_crs WHERE auth_name = 'EPSG'"
        ).fetchall()
    after = _query(config, "SELECT COUNT(*) FROM geodetic_crs WHERE auth_name = 'EPSG'")

    assert before == after
    assert report.rows_by_table.get("geodetic_crs", 0) == 0


def test_custom_alias_is_attached_to_an_epsg_crs(config: ProjDbBuildConfig) -> None:
    """A local name for a standard CRS is why the custom database exists."""
    _build(config, _register(scope_authority=AUTHORITY))

    rows = _query(
        config,
        "SELECT alt_name FROM alias_name WHERE table_name = 'geodetic_crs' "
        "AND auth_name = 'EPSG' AND code = ? AND source = ?",
        str(EPSG_CRS),
        AUTHORITY,
    )

    assert rows == [("Company WGS 84",)]


def test_epsg_owned_usage_is_not_duplicated(config: ProjDbBuildConfig) -> None:
    """An EPSG scope on an EPSG object is already in the base database."""
    _build(config, _register(scope_authority="EPSG"))

    rows = _query(
        config,
        "SELECT COUNT(*) FROM usage WHERE auth_name = ? AND object_code = ?",
        AUTHORITY,
        str(EPSG_CRS),
    )

    assert rows == [(0,)]


def test_absent_objects_are_not_annotated(config: ProjDbBuildConfig) -> None:
    """A usage pointing at a CRS the database lacks is a dangling reference."""
    _build(config, _register(scope_authority=AUTHORITY, crs_code=ABSENT_CRS))

    assert _query(
        config,
        "SELECT COUNT(*) FROM usage WHERE auth_name = ? AND object_code = ?",
        AUTHORITY,
        str(ABSENT_CRS),
    ) == [(0,)]
    assert _query(config, "PRAGMA foreign_key_check") == []


def test_annotation_can_be_switched_off(config: ProjDbBuildConfig) -> None:
    """The pass reads every CRS in the register, so it must be optional."""
    import dataclasses

    disabled = dataclasses.replace(config, annotate_foreign_objects=False)
    _build(disabled, _register(scope_authority=AUTHORITY))

    assert _query(
        disabled,
        "SELECT COUNT(*) FROM usage WHERE auth_name = ? AND object_code = ?",
        AUTHORITY,
        str(EPSG_CRS),
    ) == [(0,)]


def test_scope_and_extent_rows_are_written(config: ProjDbBuildConfig) -> None:
    """The scope and extent the usage points at must exist, or the FK dangles."""
    _build(config, _register(scope_authority=AUTHORITY))

    assert _query(
        config,
        "SELECT scope FROM scope WHERE auth_name = ? AND code = ?",
        AUTHORITY,
        str(CUSTOM_SCOPE),
    ) == [("Company survey",)]
    assert _query(config, "PRAGMA foreign_key_check") == []


# EPSG_SCOPE and EPSG_WORLD_EXTENT are imported for symmetry with the other
# build fixtures; referenced here so the import is not flagged as unused.
_UNUSED = (EPSG_SCOPE, EPSG_WORLD_EXTENT)
