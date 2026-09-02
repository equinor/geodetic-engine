"""End to end build of a custom proj.db against a scripted Georepository.

The fixtures below describe a small authority that defines one geographic CRS
referenced to the EPSG WGS 84 ensemble, a Helmert transformation that gives it a
real path to WGS 84, and one deprecated CRS that is superseded by the first.
"""

from __future__ import annotations

import sqlite3
from typing import Any

import pytest

from geodetic_engine.georepository.client import GeorepositoryClient
from geodetic_engine.projdb.build import build
from geodetic_engine.projdb.config import ProjDbBuildConfig
from geodetic_engine.projdb.validate import validate
from tests.projdb.conftest import (
    API,
    AUTHORITY,
    EPSG_ELLIPSOIDAL_2D_CS,
    EPSG_SCOPE,
    EPSG_WORLD_EXTENT,
    FakeGeorepository,
)

CURRENT_CRS = 1000
DEPRECATED_CRS = 1001
TRANSFORMATION = 2000
GRID_TRANSFORMATION = 2001
MISSING_GRID = "example_not_installed.gsb"
DATUM = 6000
ELLIPSOID = 7000
EPSG_GREENWICH = 8901
EPSG_METRE = 9001


def _summary(endpoint: str, code: int) -> dict[str, Any]:
    return {
        "Code": code,
        "DataSource": AUTHORITY,
        "Links": [{"rel": "self", "href": f"{API}/api/v1/{endpoint}/{code}"}],
    }


def _usage() -> list[dict[str, Any]]:
    return [
        {
            "Scope": {"href": f"{API}/api/v1/Scope/{EPSG_SCOPE}"},
            "Extent": {"href": f"{API}/api/v1/Extent/{EPSG_WORLD_EXTENT}"},
        }
    ]


def _geodetic_crs(code: int, name: str, *, deprecated: bool = False) -> dict[str, Any]:
    return {
        "Code": code,
        "DataSource": AUTHORITY,
        "Name": name,
        "Kind": "geographic 2D",
        "Remark": "Defined for the test suite.",
        # A ChildLink carries no DataSource, which is how EPSG references appear.
        "CoordSys": {"Code": int(EPSG_ELLIPSOIDAL_2D_CS)},
        "Datum": {"Code": DATUM, "DataSource": AUTHORITY},
        "Usage": _usage(),
        "Alias": [{"Alias": f"{name} alias", "NamingSystem": {"Name": AUTHORITY}}],
        "Deprecations": (
            [{"ReplacedBy": {"Code": CURRENT_CRS}, "Reason": "superseded"}]
            if deprecated
            else []
        ),
    }


def _ellipsoid() -> dict[str, Any]:
    """An ellipsoid unrelated to WGS 84, so a datum shift is genuinely required."""
    return {
        "Code": ELLIPSOID,
        "DataSource": AUTHORITY,
        "Name": "Example ellipsoid",
        "SemiMajorAxis": 6378388.0,
        "InverseFlattening": 297.0,
        "Unit": {"Code": EPSG_METRE},
        "Deprecations": [],
    }


def _datum() -> dict[str, Any]:
    return {
        "Code": DATUM,
        "DataSource": AUTHORITY,
        "Name": "Example datum",
        "Type": "geodetic",
        "Origin": "Example fundamental point.",
        "PublicationDate": "1975-01-01",
        "Ellipsoid": {"Code": ELLIPSOID, "DataSource": AUTHORITY},
        "PrimeMeridian": {"Code": EPSG_GREENWICH},
        "Usage": _usage(),
        "Deprecations": [],
    }


def _helmert() -> dict[str, Any]:
    return {
        "Code": TRANSFORMATION,
        "DataSource": AUTHORITY,
        "Name": "Example Geographic 2D to WGS 84 (1)",
        "Method": {"Code": 9603, "Name": "Geocentric translations (geog2D domain)"},
        "SourceCrs": {"Code": CURRENT_CRS, "DataSource": AUTHORITY},
        "TargetCrs": {"Code": 4326},
        "Accuracy": 1.0,
        "CoordTfmVersion": "Example-1",
        "ParameterValues": [
            {
                "ParameterCode": 8605,
                "ParameterValue": 0.0,
                "Unit": {"Code": 9001},
                "SortOrder": 1,
            },
            {
                "ParameterCode": 8606,
                "ParameterValue": 0.0,
                "Unit": {"Code": 9001},
                "SortOrder": 2,
            },
            {
                "ParameterCode": 8607,
                "ParameterValue": 0.0,
                "Unit": {"Code": 9001},
                "SortOrder": 3,
            },
        ],
        "Usage": _usage(),
        "Deprecations": [],
    }


def _grid_transformation() -> dict[str, Any]:
    """An NTv2 transformation whose grid is deliberately not installed."""
    return {
        "Code": GRID_TRANSFORMATION,
        "DataSource": AUTHORITY,
        "Name": "Example Geographic 2D to WGS 84 (grid)",
        "Method": {"Code": 9615, "Name": "NTv2"},
        "SourceCrs": {"Code": CURRENT_CRS, "DataSource": AUTHORITY},
        "TargetCrs": {"Code": 4326},
        "Accuracy": 0.1,
        "ParameterValues": [
            {
                "ParameterCode": 8656,
                "Name": "Latitude and longitude difference file",
                "ParamValueFileRef": MISSING_GRID,
                "SortOrder": 1,
            }
        ],
        "Usage": _usage(),
        "Deprecations": [],
    }


@pytest.fixture
def fake_instance() -> FakeGeorepository:
    fake = FakeGeorepository(
        {
            "Ellipsoid": [_summary("Ellipsoid", ELLIPSOID)],
            "Datum": [_summary("Datum", DATUM)],
            "GeodeticCoordRefSystem": [
                _summary("GeodeticCoordRefSystem", CURRENT_CRS),
                _summary("GeodeticCoordRefSystem", DEPRECATED_CRS),
            ],
            "Transformation": [_summary("Transformation", TRANSFORMATION)],
        }
    )
    fake.add_object(f"/api/v1/Ellipsoid/{ELLIPSOID}", _ellipsoid())
    fake.add_object(f"/api/v1/Datum/{DATUM}", _datum())
    # The datum exposes its aliases only on the /alias endpoint, not inline.
    fake.add_aliases(
        f"/api/v1/Datum/{DATUM}",
        [
            {"Alias": "Example datum alias", "NamingSystem": {"Name": AUTHORITY}},
            {"Alias": "Someone else's name", "NamingSystem": {"Name": "Other"}},
        ],
    )
    fake.add_object(
        f"/api/v1/GeodeticCoordRefSystem/{CURRENT_CRS}",
        _geodetic_crs(CURRENT_CRS, "Example Geographic 2D"),
    )
    fake.add_object(
        f"/api/v1/GeodeticCoordRefSystem/{DEPRECATED_CRS}",
        _geodetic_crs(DEPRECATED_CRS, "Example Geographic 2D (old)", deprecated=True),
    )
    fake.add_object(f"/api/v1/Transformation/{TRANSFORMATION}", _helmert())
    fake.add_object(
        f"/api/v1/Scope/{EPSG_SCOPE}",
        {
            "Code": int(EPSG_SCOPE),
            "DataSource": "EPSG",
            "ScopeDetails": "Spatial referencing.",
        },
    )
    fake.add_object(
        f"/api/v1/Extent/{EPSG_WORLD_EXTENT}",
        {
            "Code": int(EPSG_WORLD_EXTENT),
            "DataSource": "EPSG",
            "Name": "World",
            "BoundingBoxSouthBoundLatitude": -90.0,
            "BoundingBoxNorthBoundLatitude": 90.0,
            "BoundingBoxWestBoundLongitude": -180.0,
            "BoundingBoxEastBoundLongitude": 180.0,
        },
    )
    return fake


@pytest.fixture
def report(config: ProjDbBuildConfig, fake_instance: FakeGeorepository):
    client = GeorepositoryClient(
        config.georepository, transport=fake_instance.transport()
    )
    return build(config, client=client)


def test_custom_objects_are_written(report, config: ProjDbBuildConfig) -> None:
    assert report.rows_by_table["geodetic_crs"] == 2
    assert report.rows_by_table["helmert_transformation_table"] == 1
    assert config.output_db.is_file()


def test_epsg_objects_are_not_reimported(report) -> None:
    """The EPSG dataset in proj.db stays authoritative for its own objects."""
    assert "extent" not in report.rows_by_table
    assert "scope" not in report.rows_by_table


def test_no_dangling_references(config: ProjDbBuildConfig, report) -> None:
    with sqlite3.connect(f"file:{config.output_db}?mode=ro", uri=True) as connection:
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []


def test_deprecated_crs_is_flagged_and_superseded(
    config: ProjDbBuildConfig, report
) -> None:
    """This is what turns 'CRS not found' into 'deprecated, superseded by X'."""
    with sqlite3.connect(f"file:{config.output_db}?mode=ro", uri=True) as connection:
        deprecated = connection.execute(
            "SELECT deprecated FROM geodetic_crs WHERE auth_name = ? AND code = ?",
            (AUTHORITY, str(DEPRECATED_CRS)),
        ).fetchone()
        replacement = connection.execute(
            "SELECT replacement_auth_name, replacement_code FROM supersession "
            "WHERE superseded_auth_name = ? AND superseded_code = ?",
            (AUTHORITY, str(DEPRECATED_CRS)),
        ).fetchone()
    assert deprecated[0] == 1
    # proj.db code columns are INTEGER_OR_TEXT, which has INTEGER affinity, so
    # numeric codes come back as integers.
    assert (replacement[0], str(replacement[1])) == (AUTHORITY, str(CURRENT_CRS))
    assert report.supersessions_written == 1


def test_aliases_are_imported_for_configured_naming_systems(
    config: ProjDbBuildConfig, report
) -> None:
    with sqlite3.connect(f"file:{config.output_db}?mode=ro", uri=True) as connection:
        aliases = connection.execute(
            "SELECT alt_name FROM alias_name WHERE auth_name = ?", (AUTHORITY,)
        ).fetchall()
    assert ("Example Geographic 2D alias",) in aliases


def test_datum_aliases_are_imported(config: ProjDbBuildConfig, report) -> None:
    """Datums were previously the one object type whose aliases were dropped."""
    with sqlite3.connect(f"file:{config.output_db}?mode=ro", uri=True) as connection:
        aliases = connection.execute(
            "SELECT alt_name FROM alias_name "
            "WHERE table_name = 'geodetic_datum' AND auth_name = ?",
            (AUTHORITY,),
        ).fetchall()
    assert aliases == [("Example datum alias",)]


def test_aliases_from_other_naming_systems_are_ignored(
    config: ProjDbBuildConfig, report
) -> None:
    with sqlite3.connect(f"file:{config.output_db}?mode=ro", uri=True) as connection:
        sources = connection.execute(
            "SELECT DISTINCT source FROM alias_name WHERE auth_name = ?", (AUTHORITY,)
        ).fetchall()
    assert sources == [(AUTHORITY,)]


def test_authority_preferences_make_custom_operations_selectable(
    config: ProjDbBuildConfig, report
) -> None:
    """Without these rows PROJ never considers a custom authority's operations."""
    with sqlite3.connect(f"file:{config.output_db}?mode=ro", uri=True) as connection:
        rows = dict(
            connection.execute(
                "SELECT source_auth_name || '>' || target_auth_name, "
                "allowed_authorities FROM authority_to_authority_preference"
            )
        )
    assert rows[f"{AUTHORITY}>any"] == f"{AUTHORITY},PROJ,EPSG"
    assert rows[f"any>{AUTHORITY}"] == f"{AUTHORITY},PROJ,EPSG"
    # The stock EPSG rule is extended rather than replaced, so custom operations
    # become candidates without displacing the established ordering.
    assert rows["EPSG>EPSG"] == f"PROJ,EPSG,NKG,{AUTHORITY}"


def test_report_records_provenance(report, config: ProjDbBuildConfig) -> None:
    """A result must be traceable back to the versions that produced it."""
    assert report.proj_version == "9.8.1"
    assert report.epsg_version.startswith("v")
    assert report.database_layout_version == "1.6"
    assert report.authorities == [AUTHORITY]
    assert {
        "table": "geodetic_crs",
        "auth_name": AUTHORITY,
        "code": str(CURRENT_CRS),
    } in (report.imported)


def test_built_database_passes_validation(config: ProjDbBuildConfig, report) -> None:
    """PROJ must be able to construct everything that was written."""
    summary = validate(config.output_db, authorities=config.authorities)
    assert summary["crs_checked"] == 2
    assert summary["operations_checked"] == 1


def test_missing_grid_does_not_fail_validation(
    config: ProjDbBuildConfig, fake_instance: FakeGeorepository
) -> None:
    """A grid transformation is a correct database entry either way.

    Whether the grid file happens to be installed on the machine that built the
    database says nothing about the database, and the grid may well be present
    wherever it is used. Availability is reported, never enforced.
    """
    fake_instance.collections["Transformation"] = [
        _summary("Transformation", TRANSFORMATION),
        _summary("Transformation", GRID_TRANSFORMATION),
    ]
    fake_instance.add_object(
        f"/api/v1/Transformation/{GRID_TRANSFORMATION}", _grid_transformation()
    )
    client = GeorepositoryClient(
        config.georepository, transport=fake_instance.transport()
    )
    build(config, client=client)

    summary = validate(config.output_db, authorities=config.authorities)
    assert summary["operations_checked"] == 2
    assert summary["missing_grids"] == [MISSING_GRID]
    grid = next(g for g in summary["grids"] if g["name"] == MISSING_GRID)
    assert grid["available"] is False
    assert grid["used_by"] == [f"{AUTHORITY}:{GRID_TRANSFORMATION}"]


def test_grid_transformation_is_written_despite_the_missing_grid(
    config: ProjDbBuildConfig, fake_instance: FakeGeorepository
) -> None:
    fake_instance.collections["Transformation"] = [
        _summary("Transformation", GRID_TRANSFORMATION)
    ]
    fake_instance.add_object(
        f"/api/v1/Transformation/{GRID_TRANSFORMATION}", _grid_transformation()
    )
    client = GeorepositoryClient(
        config.georepository, transport=fake_instance.transport()
    )
    report = build(config, client=client)

    assert report.rows_by_table["grid_transformation"] == 1
    with sqlite3.connect(f"file:{config.output_db}?mode=ro", uri=True) as connection:
        stored = connection.execute(
            "SELECT grid_name FROM grid_transformation WHERE auth_name = ?",
            (AUTHORITY,),
        ).fetchone()
    assert stored == (MISSING_GRID,)


def test_dry_run_writes_nothing(
    config: ProjDbBuildConfig, fake_instance: FakeGeorepository
) -> None:
    """A dry run exercises the real inserts and constraints, then discards them."""
    client = GeorepositoryClient(
        config.georepository, transport=fake_instance.transport()
    )
    report = build(config, client=client, dry_run=True)

    assert report.dry_run is True
    assert report.rows_by_table["geodetic_crs"] == 2
    assert not config.output_db.exists()


def test_all_naming_systems_can_be_imported(
    config: ProjDbBuildConfig, fake_instance: FakeGeorepository
) -> None:
    """A register that curates several naming systems for its own objects."""
    from dataclasses import replace

    wildcard = replace(config, naming_systems=frozenset({"*"}))
    client = GeorepositoryClient(
        wildcard.georepository, transport=fake_instance.transport()
    )
    build(wildcard, client=client)

    with sqlite3.connect(f"file:{wildcard.output_db}?mode=ro", uri=True) as connection:
        sources = {
            row[0]
            for row in connection.execute(
                "SELECT DISTINCT source FROM alias_name WHERE auth_name = ?",
                (AUTHORITY,),
            )
        }
    assert sources == {AUTHORITY, "Other"}


DERIVED_VERTICAL = 5100009
EPSG_LAT_DEPTH = 5861
EPSG_HEIGHT_CS = 6499
EPSG_LAT_DATUM = "1080"
HEIGHT_DEPTH_REVERSAL = 1068
VERTICAL_OFFSET = 9616


def _derived_vertical(method_code: int) -> dict[str, Any]:
    """A vertical CRS derived from another, stating no datum of its own."""
    return {
        "Code": DERIVED_VERTICAL,
        "DataSource": AUTHORITY,
        "Name": "Example LAT height",
        "CoordSys": {"Code": EPSG_HEIGHT_CS},
        "BaseCoordRefSystem": {"Code": EPSG_LAT_DEPTH},
        "Conversion": {
            "Code": 7812,
            "href": f"{API}/api/v1/Conversion/7812_{method_code}",
        },
        "Usage": _usage(),
        "Deprecations": [],
    }


def _with_derived_vertical(
    fake: FakeGeorepository, method_code: int
) -> FakeGeorepository:
    fake.collections["VerticalCoordRefSystem"] = [
        _summary("VerticalCoordRefSystem", DERIVED_VERTICAL)
    ]
    fake.add_object(
        f"/api/v1/VerticalCoordRefSystem/{DERIVED_VERTICAL}",
        _derived_vertical(method_code),
    )
    fake.add_object(
        f"/api/v1/Conversion/7812_{method_code}",
        {"Code": 7812, "DataSource": "EPSG", "Method": {"Code": method_code}},
    )
    return fake


def test_derived_vertical_crs_inherits_the_base_datum(
    config: ProjDbBuildConfig, fake_instance: FakeGeorepository
) -> None:
    """proj.db has no derived CRS table, so it is stored flattened.

    A height/depth reversal changes nothing but the axis direction, which the
    coordinate system already records, so the base datum plus this CRS's own
    coordinate system describe it exactly.
    """
    _with_derived_vertical(fake_instance, HEIGHT_DEPTH_REVERSAL)
    client = GeorepositoryClient(
        config.georepository, transport=fake_instance.transport()
    )
    report = build(config, client=client)

    assert [s for s in report.skipped if s["table"] == "vertical_crs"] == []
    with sqlite3.connect(f"file:{config.output_db}?mode=ro", uri=True) as connection:
        row = connection.execute(
            "SELECT coordinate_system_code, datum_auth_name, datum_code "
            "FROM vertical_crs WHERE auth_name = ? AND code = ?",
            (AUTHORITY, str(DERIVED_VERTICAL)),
        ).fetchone()
    assert row == (EPSG_HEIGHT_CS, "EPSG", int(EPSG_LAT_DATUM))


def test_derived_vertical_crs_with_an_offset_is_refused(
    config: ProjDbBuildConfig, fake_instance: FakeGeorepository
) -> None:
    """Flattening an offset onto the base datum would shift every height."""
    _with_derived_vertical(fake_instance, VERTICAL_OFFSET)
    client = GeorepositoryClient(
        config.georepository, transport=fake_instance.transport()
    )
    report = build(config, client=client)

    reasons = [s["reason"] for s in report.skipped if s["table"] == "vertical_crs"]
    assert len(reasons) == 1
    assert "changes more than the axis order, direction or unit" in reasons[0]


DERIVED_GEODETIC = 1200099
EPSG_WGS84_2D = 4326
EPSG_LATLON_CS = 6422
AXIS_ORDER_REVERSAL = 9843
LONGITUDE_ROTATION = 9601


def _derived_geodetic(method_code: int) -> dict[str, Any]:
    """A geographic CRS derived from another, stating no datum of its own."""
    return {
        "Code": DERIVED_GEODETIC,
        "DataSource": AUTHORITY,
        "Name": "Example lon/lat",
        "Kind": "geographic 2D",
        "CoordSys": {"Code": EPSG_LATLON_CS},
        "BaseCoordRefSystem": {"Code": EPSG_WGS84_2D},
        "Conversion": {
            "Code": 15498,
            "href": f"{API}/api/v1/Conversion/15498_{method_code}",
        },
        "Usage": _usage(),
        "Deprecations": [],
    }


def _with_derived_geodetic(
    fake: FakeGeorepository, method_code: int
) -> FakeGeorepository:
    fake.collections["GeodeticCoordRefSystem"] = [
        _summary("GeodeticCoordRefSystem", DERIVED_GEODETIC)
    ]
    fake.collections["Transformation"] = []
    fake.add_object(
        f"/api/v1/GeodeticCoordRefSystem/{DERIVED_GEODETIC}",
        _derived_geodetic(method_code),
    )
    fake.add_object(
        f"/api/v1/Conversion/15498_{method_code}",
        {"Code": 15498, "DataSource": "EPSG", "Method": {"Code": method_code}},
    )
    return fake


def test_derived_geodetic_crs_inherits_the_base_datum(
    config: ProjDbBuildConfig, fake_instance: FakeGeorepository
) -> None:
    """An axis order reversal is captured by the coordinate system alone."""
    _with_derived_geodetic(fake_instance, AXIS_ORDER_REVERSAL)
    client = GeorepositoryClient(
        config.georepository, transport=fake_instance.transport()
    )
    report = build(config, client=client)

    assert [s for s in report.skipped if s["table"] == "geodetic_crs"] == []
    with sqlite3.connect(f"file:{config.output_db}?mode=ro", uri=True) as connection:
        row = connection.execute(
            "SELECT datum_auth_name, datum_code FROM geodetic_crs "
            "WHERE auth_name = ? AND code = ?",
            (AUTHORITY, str(DERIVED_GEODETIC)),
        ).fetchone()
    assert row == ("EPSG", 6326)  # WGS 84 ensemble, the datum of EPSG:4326


def test_derived_geodetic_crs_with_a_rotation_is_refused(
    config: ProjDbBuildConfig, fake_instance: FakeGeorepository
) -> None:
    """A longitude rotation moves the coordinates; it cannot be flattened."""
    _with_derived_geodetic(fake_instance, LONGITUDE_ROTATION)
    client = GeorepositoryClient(
        config.georepository, transport=fake_instance.transport()
    )
    report = build(config, client=client)

    reasons = [s["reason"] for s in report.skipped if s["table"] == "geodetic_crs"]
    assert len(reasons) == 1
    assert "changes more than the axis order, direction or unit" in reasons[0]


def test_skipped_objects_record_whether_they_are_deprecated(
    config: ProjDbBuildConfig, fake_instance: FakeGeorepository
) -> None:
    """A deprecated object that could not be imported is rarely a problem."""
    _with_derived_geodetic(fake_instance, LONGITUDE_ROTATION)
    detail = _derived_geodetic(LONGITUDE_ROTATION)
    detail["Deprecations"] = [{"ReplacedBy": {"Code": CURRENT_CRS}}]
    fake_instance.add_object(
        f"/api/v1/GeodeticCoordRefSystem/{DERIVED_GEODETIC}", detail
    )
    client = GeorepositoryClient(
        config.georepository, transport=fake_instance.transport()
    )
    report = build(config, client=client)

    skipped = [s for s in report.skipped if s["table"] == "geodetic_crs"]
    assert len(skipped) == 1
    assert skipped[0]["deprecated"] is True
    assert report.as_dict()["counts"]["skipped_active"] == 0
