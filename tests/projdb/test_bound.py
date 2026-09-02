"""Importing bound CRSs, including ones defined over a concatenated chain.

A bound CRS reaches proj.db as a text definition rather than as structured
columns, so these tests check the row shape PROJ's CHECK constraints demand as
well as the outcome: that PROJ can read the row back and that a chain PROJ
cannot embed is collapsed first, or refused and reported.
"""

from __future__ import annotations

import sqlite3
from typing import Any

import pytest
from pyproj import CRS
from pyproj.crs import CoordinateOperation

from geodetic_engine.georepository.client import GeorepositoryClient
from geodetic_engine.projdb.build import build
from geodetic_engine.projdb.config import ProjDbBuildConfig
from tests.projdb.conftest import API, AUTHORITY, FakeGeorepository

BOUND_GEODETIC = 3000
BOUND_OVER_CHAIN = 3001
BASE_CRS = 4230  # ED50
HUB_CRS = 4326  # WGS 84
SINGLE_STEP = 1133  # ED50 to WGS 84 (1)
CHAIN = 8047  # ED50 to WGS 84 (15), two Helmert steps through ED87
GRID_CHAIN = 3896  # MGI (Ferro) to WGS 84 (2), a chain with a non-Helmert step


def _summary(endpoint: str, code: int) -> dict[str, Any]:
    return {
        "Code": code,
        "DataSource": AUTHORITY,
        "Links": [{"rel": "self", "href": f"{API}/api/v1/{endpoint}/{code}"}],
    }


def _bound(code: int, transformation_code: int) -> dict[str, Any]:
    return {
        "Code": code,
        "DataSource": AUTHORITY,
        "Name": f"Example bound {code}",
        "Kind": "geographic 2D",
        "BaseCoordRefSystem": {"href": f"{API}/api/v1/CoordRefSystem/{BASE_CRS}"},
        "Transformation": {
            "href": f"{API}/api/v1/Transformation/{transformation_code}"
        },
        "Deprecations": [],
    }


def _register(*bound_crs: dict[str, Any]) -> FakeGeorepository:
    """A register serving the bound CRSs plus the EPSG objects they point at."""
    fake = FakeGeorepository(
        {
            "BoundCoordRefSystem": [
                _summary("BoundCoordRefSystem", b["Code"]) for b in bound_crs
            ]
        }
    )
    for item in bound_crs:
        fake.add_object(f"/api/v1/BoundCoordRefSystem/{item['Code']}", item)

    for code in (BASE_CRS, HUB_CRS):
        fake.add_object(
            f"/api/v1/CoordRefSystem/{code}", {"Code": code, "DataSource": "EPSG"}
        )
        fake.add_export(f"/api/v1/CoordRefSystem/{code}", CRS.from_epsg(code).to_wkt())

    for code in (SINGLE_STEP, CHAIN, GRID_CHAIN):
        try:
            operation = CoordinateOperation.from_authority("EPSG", code)
        except Exception:  # pragma: no cover - depends on the EPSG release
            continue
        fake.add_object(
            f"/api/v1/Transformation/{code}",
            {
                "Code": code,
                "DataSource": "EPSG",
                "TargetCrs": {"href": f"{API}/api/v1/CoordRefSystem/{HUB_CRS}"},
            },
        )
        fake.add_export(f"/api/v1/Transformation/{code}", operation.to_wkt())
    return fake


def _build(config: ProjDbBuildConfig, fake: FakeGeorepository):
    client = GeorepositoryClient(config.georepository, transport=fake.transport())
    return build(config, client=client)


def _rows(config: ProjDbBuildConfig) -> list[tuple[Any, ...]]:
    with sqlite3.connect(f"file:{config.output_db}?mode=ro", uri=True) as connection:
        return connection.execute(
            "SELECT code, coordinate_system_auth_name, datum_auth_name, "
            "text_definition FROM geodetic_crs WHERE auth_name = ?",
            (AUTHORITY,),
        ).fetchall()


def test_bound_crs_is_written_as_a_text_definition(
    config: ProjDbBuildConfig,
) -> None:
    """proj.db has no bound CRS table; the WKT goes in text_definition."""
    report = _build(config, _register(_bound(BOUND_GEODETIC, SINGLE_STEP)))
    assert report.rows_by_table.get("geodetic_crs") == 1

    (code, cs_auth, datum_auth, definition), *rest = _rows(config)
    assert not rest
    assert int(code) == BOUND_GEODETIC
    # The CHECK constraints require these to be NULL alongside a text definition.
    assert cs_auth is None
    assert datum_auth is None
    assert definition.startswith("BOUNDCRS[")


def test_written_bound_crs_is_readable_by_proj(config: ProjDbBuildConfig) -> None:
    """A definition PROJ cannot parse back would be worse than no row at all."""
    _build(config, _register(_bound(BOUND_GEODETIC, SINGLE_STEP)))
    (_, _, _, definition), *_ = _rows(config)

    crs = CRS.from_wkt(definition)

    assert crs.is_bound
    assert crs.to_json_dict()["transformation"]["id"]["code"] == SINGLE_STEP


def test_bound_crs_over_a_chain_is_collapsed(config: ProjDbBuildConfig) -> None:
    """PROJ cannot embed a chain, so it must be reduced to one step first."""
    _build(config, _register(_bound(BOUND_OVER_CHAIN, CHAIN)))
    rows = _rows(config)
    assert len(rows) == 1

    crs = CRS.from_wkt(rows[0][3])
    transformation = crs.to_json_dict()["transformation"]

    assert crs.is_bound
    # An embedded chain would still carry its steps; a collapsed one is a single
    # Helmert stated under one method.
    assert "steps" not in transformation
    assert transformation["method"]["name"].startswith("Position Vector")
    assert "collapsed" in transformation["name"]


def test_uncollapsible_chain_is_skipped_and_reported(
    config: ProjDbBuildConfig,
) -> None:
    """A chain that is not equivalent to one Helmert must not be guessed at."""
    try:
        CoordinateOperation.from_authority("EPSG", GRID_CHAIN)
    except Exception:  # pragma: no cover - depends on the EPSG release
        pytest.skip(f"EPSG:{GRID_CHAIN} is not in this EPSG release")

    report = _build(config, _register(_bound(BOUND_GEODETIC, GRID_CHAIN)))

    assert not _rows(config)
    reasons = [item["reason"] for item in report.skipped]
    assert any("not a plain Helmert" in str(reason) for reason in reasons)
