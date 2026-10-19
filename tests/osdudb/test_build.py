"""Building a database from a catalogue, end to end.

Each test builds a real proj.db from a copy of the official one and then
queries it, rather than asserting on intermediate row dictionaries, so what is
checked is what a consumer of the database would actually find in it.
"""

from __future__ import annotations

import json
import logging
import os
import sqlite3
from collections.abc import Callable, Iterator
from contextlib import closing, contextmanager
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from geodetic_engine.osdudb.build import build
from geodetic_engine.osdudb.catalog import (
    BOUND_CRS,
    GEODETIC_CRS,
    PROJECTED_CRS,
    OsduCatalog,
)
from geodetic_engine.projdb.cli import inspect_database
from geodetic_engine.projdb.errors import ProjDbBuildError
from geodetic_engine.projdb.report import BuildReport, log_summary
from geodetic_engine.projdb.settings import AuthorityPreference
from geodetic_engine.projdb.validate import validate

from .conftest import (
    AUTHORITY,
    CUSTOM_GEOGRAPHIC_WKT,
    CUSTOM_PROJECTED_WKT,
    CUSTOM_TRANSFORMATION_WKT,
    authority_code,
    crs_record,
    make_config,
    transformation_record,
    usage,
    write_catalog,
)

Build = Callable[..., BuildReport]


def test_failed_append_validation_preserves_published_artifacts(
    base_proj_db: Path,
    output_db: Path,
    catalog_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    write_catalog(catalog_path, geographic())
    config = make_config(base_proj_db, output_db, catalog_path)
    build(config)
    before = output_db.read_bytes()
    sidecar = output_db.with_suffix(".db.report.json")
    report_before = sidecar.read_bytes()

    def reject(*args: Any, **kwargs: Any) -> dict[str, Any]:
        raise ProjDbBuildError("validation regression")

    monkeypatch.setattr("geodetic_engine.projdb.common.validate", reject)
    with pytest.raises(ProjDbBuildError, match="validation regression"):
        build(replace(config, append=True))
    assert output_db.read_bytes() == before
    assert sidecar.read_bytes() == report_before


def test_overwrite_updates_crs_and_projection(
    base_proj_db: Path, output_db: Path, catalog_path: Path
) -> None:
    write_catalog(
        catalog_path,
        geographic(
            NameAlias=[
                {
                    "AliasName": "Old alias",
                    "AliasNameTypeID": "ns:reference-data--AliasNameType:OSDU:",
                }
            ]
        ),
        projected(),
    )
    config = make_config(base_proj_db, output_db, catalog_path)
    build(config)
    write_catalog(
        catalog_path,
        geographic(Name="Corrected"),
        projected(OGCWellKnownText2=CUSTOM_PROJECTED_WKT.replace("500000", "600000")),
    )
    report = build(replace(config, append=True, overwrite_rows=True))
    assert report.validation["status"] == "passed"
    with closing(sqlite3.connect(output_db)) as connection:
        assert (
            connection.execute(
                "SELECT 1 FROM alias_name WHERE auth_name='OSDU' AND code=4100 AND alt_name='Old alias'"
            ).fetchone()
            is None
        )
        assert (
            connection.execute(
                "SELECT name FROM geodetic_crs WHERE auth_name='OSDU' AND code=4100"
            ).fetchone()[0]
            == "Corrected"
        )
        assert (
            connection.execute(
                "SELECT param4_value FROM conversion_table WHERE auth_name='OSDU' AND code=17100"
            ).fetchone()[0]
            == 600000
        )


@pytest.mark.parametrize(
    "fields",
    [
        {"Method": authority_code("EPSG", 9607)},
        {"SourceCRS": authority_code("EPSG", 4326)},
        {"TargetCRS": authority_code("EPSG", 4258)},
    ],
)
def test_operation_metadata_cannot_contradict_wkt(
    base_proj_db: Path, output_db: Path, catalog_path: Path, fields: dict[str, Any]
) -> None:
    write_catalog(catalog_path, geographic(), helmert(**fields))
    report = build(make_config(base_proj_db, output_db, catalog_path))
    assert any("contradict" in str(item["reason"]) for item in report.skipped)
    with closing(sqlite3.connect(output_db)) as connection:
        assert (
            connection.execute(
                "SELECT 1 FROM helmert_transformation_table WHERE auth_name='OSDU' AND code=9100"
            ).fetchone()
            is None
        )


def geographic(**fields: Any) -> dict[str, Any]:
    """The custom geographic CRS, whose datum no EPSG dataset defines."""
    return crs_record(
        **{
            "Code": "4100",
            "Name": "Example 2020",
            "CoordinateReferenceSystemType": GEODETIC_CRS,
            "Kind": "geographic 2D",
            "OGCWellKnownText2": CUSTOM_GEOGRAPHIC_WKT,
            "Datum": authority_code(AUTHORITY, 6100, Name="Example Datum 2020"),
            "CoordinateSystem": authority_code("EPSG", 6422),
        }
        | fields
    )


def projected(**fields: Any) -> dict[str, Any]:
    """A projected CRS on it, whose conversion also lives only in the WKT."""
    return crs_record(
        **{
            "Code": "32100",
            "Name": "Example 2020 / UTM zone 31N",
            "CoordinateReferenceSystemType": PROJECTED_CRS,
            "Kind": "projected",
            "OGCWellKnownText2": CUSTOM_PROJECTED_WKT,
            "BaseCRS": authority_code(AUTHORITY, 4100),
            "Projection": authority_code(AUTHORITY, 17100, Name="Example UTM zone 31N"),
            "CoordinateSystem": authority_code("EPSG", 4400),
        }
        | fields
    )


def helmert(**fields: Any) -> dict[str, Any]:
    """A seven parameter transformation from it to WGS 84."""
    return transformation_record(
        **{
            "Code": "9100",
            "Name": "Example 2020 to WGS 84 (1)",
            "OGCWellKnownText2": CUSTOM_TRANSFORMATION_WKT,
            "SourceCRS": authority_code(AUTHORITY, 4100),
            "TargetCRS": authority_code("EPSG", 4326),
            "Method": authority_code(
                "EPSG", 9606, Name="Position Vector transformation (geog2D domain)"
            ),
            "Accuracy": 1.0,
            "CoordTfmVersion": "EXAMPLE-Test",
        }
        | fields
    )


def bound(**fields: Any) -> dict[str, Any]:
    """The two of them bound together, which is what OSDU publishes."""
    return crs_record(
        **{
            "Code": "4100001",
            "Name": "Example 2020 * EXAMPLE [4100,9100]",
            "CoordinateReferenceSystemType": BOUND_CRS,
            "Kind": "BoundGeographic2D",
            "SourceCRS": authority_code(AUTHORITY, 4100),
            "Transformation": authority_code(AUTHORITY, 9100),
            "CoordinateSystem": authority_code("EPSG", 6422),
        }
        | fields
    )


@pytest.fixture
def run(base_proj_db: Path, output_db: Path, catalog_path: Path) -> Build:
    """Write a catalogue, read it back and build from it."""

    def _run(*entries: dict[str, Any], **overrides: Any) -> BuildReport:
        write_catalog(catalog_path, *entries)
        config = make_config(base_proj_db, output_db, catalog_path, **overrides)
        return build(config, catalog=OsduCatalog.from_file(catalog_path))

    return _run


def rows(database: Path, statement: str, *parameters: Any) -> list[tuple[Any, ...]]:
    with closing(sqlite3.connect(f"file:{database}?mode=ro", uri=True)) as connection:
        return connection.execute(statement, parameters).fetchall()


@contextmanager
def proj_reading(database: Path) -> Iterator[None]:
    """Point PROJ at a built database, which it opens as ``proj.db``."""
    from pyproj import datadir

    previous = datadir.get_data_dir()
    search = os.pathsep.join([str(database.parent.resolve()), previous])
    os.environ["PROJ_DATA"] = search
    datadir.set_data_dir(search)
    try:
        yield
    finally:
        datadir.set_data_dir(previous)
        os.environ.pop("PROJ_DATA", None)


class TestBuildingBlocks:
    """A CRS's datum, ellipsoid and axes exist only inside its WKT."""

    def test_a_datum_and_ellipsoid_are_produced_from_the_wkt(
        self, run: Build, output_db: Path
    ) -> None:
        run(geographic())

        assert rows(
            output_db,
            "SELECT name, semi_major_axis, inv_flattening, semi_minor_axis, "
            "uom_auth_name, uom_code FROM ellipsoid WHERE auth_name = ? AND code = ?",
            AUTHORITY,
            "7100",
        ) == [("Example 1980", 6378137.0, 298.257222101, None, "EPSG", 9001)]

        assert rows(
            output_db,
            "SELECT name, ellipsoid_auth_name, ellipsoid_code, "
            "prime_meridian_auth_name, prime_meridian_code FROM geodetic_datum "
            "WHERE auth_name = ? AND code = ?",
            AUTHORITY,
            "6100",
        ) == [("Example Datum 2020", AUTHORITY, 7100, "EPSG", 8901)]

    def test_the_crs_references_what_was_produced(
        self, run: Build, output_db: Path
    ) -> None:
        run(geographic())
        assert rows(
            output_db,
            "SELECT type, coordinate_system_auth_name, coordinate_system_code, "
            "datum_auth_name, datum_code FROM geodetic_crs "
            "WHERE auth_name = ? AND code = ?",
            AUTHORITY,
            "4100",
        ) == [("geographic 2D", "EPSG", 6422, AUTHORITY, 6100)]

    def test_an_object_the_base_database_defines_is_not_reimported(
        self, run: Build
    ) -> None:
        # The prime meridian is EPSG:8901 and the coordinate system EPSG:6422,
        # both of which PROJ already ships.
        report = run(geographic())
        assert "prime_meridian" not in report.rows_by_table
        assert "coordinate_system" not in report.rows_by_table

    def test_a_conversion_is_produced_from_the_projected_crs_wkt(
        self, run: Build, output_db: Path
    ) -> None:
        run(geographic(), projected())
        # Parameter values are written in the units the authority states.
        assert rows(
            output_db,
            "SELECT name, method_auth_name, method_code, param2_code, param2_value, "
            "param2_uom_code, param3_value FROM conversion_table "
            "WHERE auth_name = ? AND code = ?",
            AUTHORITY,
            "17100",
        ) == [("Example UTM zone 31N", "EPSG", 9807, 8802, 3.0, 9102, 0.9996)]

    def test_the_projected_crs_references_its_base_and_conversion(
        self, run: Build, output_db: Path
    ) -> None:
        run(geographic(), projected())
        assert rows(
            output_db,
            "SELECT geodetic_crs_auth_name, geodetic_crs_code, conversion_auth_name, "
            "conversion_code FROM projected_crs WHERE auth_name = ? AND code = ?",
            AUTHORITY,
            "32100",
        ) == [(AUTHORITY, 4100, AUTHORITY, 17100)]


class TestTransformations:
    def test_helmert_parameters_land_in_their_named_columns(
        self, run: Build, output_db: Path
    ) -> None:
        run(geographic(), helmert())
        assert rows(
            output_db,
            "SELECT tx, ty, tz, translation_uom_code, rx, ry, rz, rotation_uom_code, "
            "scale_difference, scale_difference_uom_code, accuracy, operation_version "
            "FROM helmert_transformation_table WHERE auth_name = ? AND code = ?",
            AUTHORITY,
            "9100",
        ) == [
            (
                1.5,
                -2.5,
                3.5,
                9001,
                0.1,
                0.2,
                0.3,
                9104,
                4.5,
                9202,
                1.0,
                "EXAMPLE-Test",
            )
        ]

    def test_the_table_follows_the_parameters(self, run: Build) -> None:
        report = run(geographic(), helmert())
        assert report.rows_by_table.get("helmert_transformation_table") == 1
        assert "other_transformation" not in report.rows_by_table

    def test_a_transformation_whose_source_crs_is_absent_is_skipped(
        self, run: Build
    ) -> None:
        report = run(helmert())
        assert [item["code"] for item in report.skipped] == ["9100"]
        assert "SourceCRS" in str(report.skipped[0]["reason"])


class TestBoundCrs:
    def test_a_bound_crs_is_stored_as_a_text_definition(
        self, run: Build, output_db: Path
    ) -> None:
        run(geographic(), helmert(), bound())
        result = rows(
            output_db,
            "SELECT text_definition, coordinate_system_auth_name, datum_auth_name "
            "FROM geodetic_crs WHERE auth_name = ? AND code = ?",
            AUTHORITY,
            "4100001",
        )
        assert len(result) == 1
        definition, coordinate_system, datum = result[0]
        assert definition.startswith("BOUNDCRS[")
        # proj.db's CHECK constraints require the structured columns to be NULL
        # when a text definition is present.
        assert coordinate_system is None and datum is None

    def test_the_bound_crs_embeds_the_transformation_the_catalogue_names(
        self, run: Build, output_db: Path
    ) -> None:
        run(geographic(), helmert(), bound())
        definition = rows(
            output_db,
            "SELECT text_definition FROM geodetic_crs WHERE auth_name = ? AND code = ?",
            AUTHORITY,
            "4100001",
        )[0][0]
        assert "Example 2020 to WGS 84 (1)" in definition
        # An abridged transformation states the scale as 1 + s, so the 4.5 ppm
        # scale difference must appear as 1.0000045 rather than be dropped.
        assert "1.0000045" in definition
        assert '"X-axis rotation",0.1' in definition

    def test_a_bound_crs_whose_transformation_is_missing_is_skipped(
        self, run: Build
    ) -> None:
        report = run(geographic(), bound())
        assert [item["code"] for item in report.skipped] == ["4100001"]
        assert "transformation" in str(report.skipped[0]["reason"])


class TestUsageAndProvenance:
    def test_multi_authority_build_keeps_each_objects_usage_owner(
        self, run: Build, output_db: Path
    ) -> None:
        run(geographic(), projected(), authorities=frozenset({"EPSG", AUTHORITY}))

        assert rows(
            output_db,
            "SELECT auth_name, object_table_name, scope_auth_name, extent_auth_name "
            "FROM usage WHERE object_auth_name = ? ORDER BY object_table_name",
            AUTHORITY,
        ) == [
            (AUTHORITY, "geodetic_crs", "EPSG", "EPSG"),
            (AUTHORITY, "projected_crs", "EPSG", "EPSG"),
        ]
        assert rows(output_db, "PRAGMA foreign_key_check") == []

    def test_every_imported_crs_keeps_its_extent(
        self, run: Build, output_db: Path
    ) -> None:
        run(geographic(), projected())
        assert rows(
            output_db,
            "SELECT object_table_name, extent_auth_name, extent_code FROM usage "
            "WHERE auth_name = ? ORDER BY object_table_name",
            AUTHORITY,
        ) == [
            ("geodetic_crs", "EPSG", 1262),
            ("projected_crs", "EPSG", 1262),
        ]

    def test_an_extent_osdu_computed_is_kept_under_this_authority(
        self, run: Build, output_db: Path
    ) -> None:
        # A bound CRS's extent is an intersection OSDU computed and gave no
        # code; losing it would leave the CRS looking valid everywhere its base
        # CRS is.
        run(
            geographic(),
            helmert(),
            bound(Usages=[usage(extent_code=None, bounds=(50.2, 54.7, 9.9, 13.8))]),
        )
        assert rows(
            output_db,
            "SELECT south_lat, north_lat, west_lon, east_lon FROM extent "
            "WHERE auth_name = ?",
            AUTHORITY,
        ) == [(50.2, 54.7, 9.9, 13.8)]

    def test_aliases_are_imported_for_the_configured_naming_systems(
        self, run: Build, output_db: Path
    ) -> None:
        run(
            geographic(
                NameAlias=[
                    {
                        "AliasName": "Example 2020 local name",
                        "AliasNameTypeID": "ns:reference-data--AliasNameType:OSDU:",
                    },
                    {
                        "AliasName": "Somebody else's name",
                        "AliasNameTypeID": "ns:reference-data--AliasNameType:Other:",
                    },
                ]
            )
        )
        assert rows(
            output_db,
            "SELECT alt_name, source FROM alias_name WHERE auth_name = ?",
            AUTHORITY,
        ) == [("Example 2020 local name", AUTHORITY)]

    def test_an_inactive_record_is_imported_and_flagged(
        self, run: Build, output_db: Path
    ) -> None:
        report = run(geographic(InactiveIndicator=True))
        assert rows(
            output_db,
            "SELECT deprecated FROM geodetic_crs WHERE auth_name = ? AND code = ?",
            AUTHORITY,
            "4100",
        ) == [(1,)]
        assert {item["code"] for item in report.deprecated_imported} == {"4100"}

    def test_inactive_records_can_be_left_out(self, run: Build) -> None:
        report = run(geographic(InactiveIndicator=True), include_deprecated=False)
        assert "geodetic_crs" not in report.rows_by_table

    def test_the_report_records_where_the_definitions_came_from(
        self, run: Build, catalog_path: Path, base_proj_db: Path
    ) -> None:
        report = run(geographic(), catalog_version="2024-06")
        assert report.source == str(catalog_path)
        assert report.source_version == "2024-06"
        assert report.authorities == [AUTHORITY]
        assert report.base_proj_db == str(base_proj_db)


class TestAuthorityRegistration:
    def test_the_authority_is_registered_with_proj(
        self, run: Build, output_db: Path
    ) -> None:
        # An object under an authority PROJ does not list is present in the
        # database, passes every foreign key, and is still "crs not found".
        run(geographic())
        assert rows(
            output_db,
            "SELECT auth_name FROM builtin_authorities WHERE auth_name = ?",
            AUTHORITY,
        ) == [(AUTHORITY,)]

    def test_an_authority_proj_already_lists_is_not_added_again(
        self, run: Build
    ) -> None:
        report = run(geographic(), authorities=frozenset({AUTHORITY, "EPSG"}))
        assert report.rows_by_table["builtin_authorities"] == 1

    def test_the_authority_enters_operation_selection(
        self, run: Build, output_db: Path
    ) -> None:
        run(geographic())
        assert rows(
            output_db,
            "SELECT allowed_authorities FROM authority_to_authority_preference "
            "WHERE source_auth_name = ? AND target_auth_name = 'any'",
            AUTHORITY,
        ) == [(f"{AUTHORITY},PROJ,EPSG",)]

    def test_preferences_can_be_left_alone(self, run: Build) -> None:
        report = run(geographic(), authority_preference=AuthorityPreference.NONE)
        assert report.authority_preferences == []


class TestRefusals:
    def test_a_record_of_another_authority_is_not_imported(self, run: Build) -> None:
        report = run(geographic(CodeSpace="SomebodyElse"))
        assert "geodetic_crs" not in report.rows_by_table

    def test_a_crs_needing_an_object_this_build_may_not_write_is_skipped(
        self, run: Build
    ) -> None:
        # The WKT defines the datum under OSDU, but only EPSG is configured, so
        # the datum would have to be invented under a code somebody else owns.
        report = run(geographic(CodeSpace="EPSG"), authorities=frozenset({"EPSG"}))
        assert len(report.skipped) == 1
        assert "not among the configured authorities" in str(
            report.skipped[0]["reason"]
        )

    def test_a_crs_whose_declared_datum_contradicts_its_wkt_is_skipped(
        self, run: Build
    ) -> None:
        # Resolving this either way attaches the CRS to a datum nobody meant.
        report = run(geographic(Datum=authority_code(AUTHORITY, 6999)))
        assert len(report.skipped) == 1
        assert "but its WKT defines" in str(report.skipped[0]["reason"])

    def test_a_projected_crs_whose_base_contradicts_its_wkt_is_skipped(
        self, run: Build, output_db: Path
    ) -> None:
        report = run(geographic(), projected(BaseCRS=authority_code("EPSG", 4326)))

        assert len(report.skipped) == 1
        skipped = report.skipped[0]
        assert skipped["table"] == "projected_crs"
        assert skipped["code"] == "32100"
        assert "declares base CRS EPSG:4326" in str(skipped["reason"])
        assert "but its WKT defines OSDU:4100" in str(skipped["reason"])
        assert (
            rows(
                output_db,
                "SELECT code FROM projected_crs WHERE auth_name = ? AND code = ?",
                AUTHORITY,
                "32100",
            )
            == []
        )
        assert (
            rows(
                output_db,
                "SELECT code FROM conversion_table WHERE auth_name = ? AND code = ?",
                AUTHORITY,
                "17100",
            )
            == []
        )

    def test_a_record_without_wkt_is_skipped(self, run: Build) -> None:
        record = geographic()
        del record["data"]["OGCWellKnownText2"]
        report = run(record)
        assert "carries no WKT" in str(report.skipped[0]["reason"])

    def test_an_unsupported_method_is_skipped_and_reported(self, run: Build) -> None:
        report = run(
            geographic(), helmert(), unsupported_method_codes=frozenset({9606})
        )
        assert "9606 is not supported" in str(report.skipped[0]["reason"])

    def test_a_crs_kind_proj_db_has_no_type_for_is_skipped(self, run: Build) -> None:
        report = run(geographic(Kind="something else"))
        assert "unsupported geodetic CRS kind" in str(report.skipped[0]["reason"])


class TestProjCanReadTheResult:
    """The database is only worth anything if PROJ can construct it back."""

    def test_proj_constructs_every_imported_object(
        self, run: Build, output_db: Path
    ) -> None:
        report = run(geographic(), projected(), helmert(), bound())
        summary = validate(
            output_db, authorities=[AUTHORITY], imported=report.imported_objects()
        )
        assert summary["crs_checked"] == 3
        # The map projection counts as an operation alongside the datum shift.
        assert summary["operations_checked"] == 2
        assert summary["missing_grids"] == []

    def test_the_bound_crs_applies_the_transformation_it_embeds(
        self, run: Build, output_db: Path
    ) -> None:
        run(geographic(), helmert(), bound())
        with proj_reading(output_db):
            from pyproj import CRS, Transformer

            bound_crs = CRS.from_authority(AUTHORITY, "4100001")
            to_wgs84 = Transformer.from_crs(
                bound_crs, CRS.from_epsg(4326), always_xy=True
            )
            assert "Example 2020 to WGS 84 (1)" in to_wgs84.description

            # The same seven parameters applied by hand, in the position vector
            # convention the method states.
            reference = Transformer.from_pipeline(
                "+proj=pipeline "
                "+step +proj=unitconvert +xy_in=deg +xy_out=rad "
                "+step +proj=cart +a=6378137 +rf=298.257222101 "
                "+step +proj=helmert +x=1.5 +y=-2.5 +z=3.5 "
                "+rx=0.1 +ry=0.2 +rz=0.3 +s=4.5 +convention=position_vector "
                "+step +inv +proj=cart +ellps=WGS84 "
                "+step +proj=unitconvert +xy_in=rad +xy_out=deg"
            )
            got = to_wgs84.transform(3.0, 60.0)
            want = reference.transform(3.0, 60.0)
            # A millimetre at this latitude is about 1e-8 degrees.
            assert got[0] == pytest.approx(want[0], abs=1e-9)
            assert got[1] == pytest.approx(want[1], abs=1e-9)

    def test_the_custom_projected_crs_projects(
        self, run: Build, output_db: Path
    ) -> None:
        run(geographic(), projected())
        with proj_reading(output_db):
            from pyproj import CRS, Transformer

            projected_crs = CRS.from_authority(AUTHORITY, "32100")
            # The conversion was read out of the WKT: UTM zone 31N on a GRS 1980
            # sized ellipsoid, so the natural origin maps to the false easting.
            forward = Transformer.from_crs(
                CRS.from_authority(AUTHORITY, "4100"), projected_crs, always_xy=True
            )
            easting, _ = forward.transform(3.0, 0.0)
            assert easting == pytest.approx(500000.0, abs=1e-6)


class TestDryRun:
    def test_the_whole_build_runs_and_is_then_discarded(
        self,
        base_proj_db: Path,
        output_db: Path,
        catalog_path: Path,
    ) -> None:
        write_catalog(catalog_path, geographic())
        config = make_config(base_proj_db, output_db, catalog_path)
        report = build(
            config, catalog=OsduCatalog.from_file(catalog_path), dry_run=True
        )
        assert report.status == "dry run"
        # Every constraint and collision check ran even though nothing is kept.
        assert report.rows_by_table["geodetic_crs"] == 1
        assert not output_db.exists()


CRS_ENTITY = "reference-data--CoordinateReferenceSystem"
CT_ENTITY = "reference-data--CoordinateTransformation"
GEOGRAPHIC_ID = f"{CRS_ENTITY}:Geographic2D:OSDU::4100"
HELMERT_ID = f"{CT_ENTITY}:OSDU::9100"
BOUND_ID = f"{CRS_ENTITY}:BoundGeographic2D:OSDU::4100_OSDU::9100"
EPSG_PROJECTED_ID = f"{CRS_ENTITY}:Projected:EPSG::23031"
EPSG_TRANSFORMATION_ID = f"{CT_ENTITY}:EPSG::1612"


def with_id(entry: dict[str, Any], identifier: str) -> dict[str, Any]:
    """The record under its id, in the placeholder partition of a manifest."""
    return entry | {"id": f"{{{{NAMESPACE}}}}:{identifier}"}


def epsg_projected(**fields: Any) -> dict[str, Any]:
    """An EPSG record for a CRS the base proj.db already defines."""
    return with_id(
        crs_record(
            **{
                "CodeSpace": "EPSG",
                "Code": "23031",
                "Name": "ED50 / UTM zone 31N",
                "CoordinateReferenceSystemType": PROJECTED_CRS,
                "Kind": "projected",
            }
            | fields
        ),
        EPSG_PROJECTED_ID,
    )


def epsg_transformation(**fields: Any) -> dict[str, Any]:
    """An EPSG record for a transformation the base proj.db already defines."""
    return with_id(
        transformation_record(
            **{"CodeSpace": "EPSG", "Code": "1612", "Name": "ED50 to WGS 84 (23)"}
            | fields
        ),
        EPSG_TRANSFORMATION_ID,
    )


def identified_catalogue() -> tuple[dict[str, Any], ...]:
    """Every kind of record OSDU publishes, each under its id."""
    return (
        with_id(geographic(), GEOGRAPHIC_ID),
        with_id(helmert(), HELMERT_ID),
        with_id(bound(), BOUND_ID),
        epsg_projected(),
        epsg_transformation(),
    )


def identifiers(database: Path) -> set[tuple[Any, ...]]:
    return set(
        rows(
            database,
            "SELECT identifier, table_name, auth_name, CAST(code AS TEXT), "
            "deprecated FROM geodetic_engine_osdu_object_identifier",
        )
    )


def identifier_aliases(database: Path) -> set[tuple[Any, ...]]:
    return set(
        rows(
            database,
            "SELECT alt_name, table_name, auth_name, CAST(code AS TEXT) "
            "FROM alias_name WHERE source = 'OSDU_ID'",
        )
    )


class TestOsduIds:
    """Every record's id, recorded against the object it names."""

    def test_every_record_is_recorded_under_its_id_without_the_partition(
        self, run: Build, output_db: Path
    ) -> None:
        report = run(*identified_catalogue())
        assert identifiers(output_db) == {
            (GEOGRAPHIC_ID, "geodetic_crs", AUTHORITY, "4100", 0),
            (HELMERT_ID, "helmert_transformation", AUTHORITY, "9100", 0),
            (BOUND_ID, "geodetic_crs", AUTHORITY, "4100001", 0),
            # Neither EPSG object is imported, being in the base proj.db
            # already; both ids are recorded all the same.
            (EPSG_PROJECTED_ID, "projected_crs", "EPSG", "23031", 0),
            (EPSG_TRANSFORMATION_ID, "helmert_transformation", "EPSG", "1612", 0),
        }
        assert report.identifiers["mapped"] == 5
        assert report.identifiers["written"] == 5
        assert report.identifiers["unmapped"] == []
        assert report.as_dict()["counts"]["identifiers_recorded"] == 5

    def test_each_id_is_also_a_proj_alias(self, run: Build, output_db: Path) -> None:
        report = run(*identified_catalogue())
        assert identifier_aliases(output_db) == {
            (identifier, table, auth, code)
            for identifier, table, auth, code, _ in identifiers(output_db)
        }
        assert report.identifiers["aliases_written"] == 5

    def test_the_aliases_can_be_left_out(self, run: Build, output_db: Path) -> None:
        report = run(*identified_catalogue(), osdu_id_aliases=False)
        assert identifier_aliases(output_db) == set()
        assert len(identifiers(output_db)) == 5
        assert report.identifiers["aliases_written"] == 0

    def test_the_data_id_serves_when_the_record_has_no_id(
        self, run: Build, output_db: Path
    ) -> None:
        record = epsg_projected()
        del record["id"]
        record["data"]["ID"] = "Projected:EPSG::23031"
        run(record)
        assert {row[0] for row in identifiers(output_db)} == {EPSG_PROJECTED_ID}

    def test_an_id_naming_nothing_is_reported_with_the_reason(
        self, run: Build, output_db: Path
    ) -> None:
        absent = epsg_projected(Code="99999999")
        anonymous = epsg_transformation()
        del anonymous["id"]
        report = run(
            with_id(geographic(), GEOGRAPHIC_ID),
            # Skipped: the transformation it binds is not in the catalogue.
            with_id(bound(), BOUND_ID),
            absent,
            anonymous,
        )
        reasons = {
            item["identifier"]: item["reason"]
            for item in report.identifiers["unmapped"]
        }
        assert "this build left OSDU:4100001 out" in reasons[BOUND_ID]
        assert "not in the base proj.db" in reasons[EPSG_PROJECTED_ID]
        assert reasons[""] == "the record states no id"
        assert {row[0] for row in identifiers(output_db)} == {GEOGRAPHIC_ID}
        # The skip carries the id, so a caller naming it is told why.
        assert report.skipped[0]["identifier"] == BOUND_ID

    def test_an_id_published_for_two_objects_is_recorded_for_neither(
        self, run: Build, output_db: Path
    ) -> None:
        report = run(epsg_projected(), epsg_projected(Code="23032"))
        assert identifiers(output_db) == set()
        assert len(report.identifiers["unmapped"]) == 2
        assert all(
            "more than one object" in item["reason"]
            for item in report.identifiers["unmapped"]
        )

    def test_an_inactive_record_is_recorded_as_inactive(
        self, run: Build, output_db: Path
    ) -> None:
        run(epsg_projected(InactiveIndicator=True))
        assert identifiers(output_db) == {
            (EPSG_PROJECTED_ID, "projected_crs", "EPSG", "23031", 1)
        }

    def test_inactive_records_can_be_left_out(
        self, run: Build, output_db: Path
    ) -> None:
        report = run(epsg_projected(InactiveIndicator=True), include_deprecated=False)
        assert identifiers(output_db) == set()
        assert report.identifiers["unmapped"] == []

    def test_disagreement_with_proj_is_reported_not_acted_on(
        self, run: Build, output_db: Path
    ) -> None:
        report = run(epsg_projected(Name="ED50 / UTM 31 (renamed)"))
        assert len(identifiers(output_db)) == 1
        assert [item["identifier"] for item in report.identifiers["warnings"]] == [
            EPSG_PROJECTED_ID
        ]

    def test_appending_the_same_catalogue_again_changes_nothing(
        self, run: Build, output_db: Path
    ) -> None:
        run(epsg_projected(), epsg_transformation())
        again = run(epsg_projected(), epsg_transformation(), append=True)
        assert again.identifiers["mapped"] == 2
        assert again.identifiers["written"] == 0
        assert again.identifiers["aliases_written"] == 0
        assert len(identifiers(output_db)) == 2
        assert len(identifier_aliases(output_db)) == 2

    def test_an_append_that_would_move_an_id_is_refused(
        self, run: Build, output_db: Path
    ) -> None:
        run(epsg_projected())
        with pytest.raises(ProjDbBuildError, match="already names"):
            run(epsg_projected(Code="23032"), append=True)
        assert {row[3] for row in identifiers(output_db)} == {"23031"}

    def test_overwriting_moves_the_id_and_its_alias(
        self, run: Build, output_db: Path
    ) -> None:
        run(epsg_projected())
        run(epsg_projected(Code="23032"), append=True, overwrite_rows=True)
        assert {row[3] for row in identifiers(output_db)} == {"23032"}
        assert {row[3] for row in identifier_aliases(output_db)} == {"23032"}

    def test_validation_refuses_an_alias_that_disagrees_with_its_id(
        self, run: Build, output_db: Path
    ) -> None:
        run(epsg_projected())
        assert validate(output_db, authorities=[AUTHORITY])["identifiers_checked"] == 1
        with closing(sqlite3.connect(output_db)) as connection, connection:
            connection.execute(
                "UPDATE alias_name SET code = 23032 WHERE source = 'OSDU_ID'"
            )
        with pytest.raises(ProjDbBuildError, match="disagree"):
            validate(output_db, authorities=[AUTHORITY])

    def test_validation_refuses_an_id_naming_nothing(
        self, run: Build, output_db: Path
    ) -> None:
        run(epsg_projected())
        with closing(sqlite3.connect(output_db)) as connection, connection:
            connection.execute(
                "DROP TRIGGER geodetic_engine_osdu_object_identifier_insert_trigger"
            )
            connection.execute(
                "INSERT INTO geodetic_engine_osdu_object_identifier VALUES "
                "('OSDU', 'dangling', 'projected_crs', 'EPSG', '99999999', 0, NULL)"
            )
        with pytest.raises(ProjDbBuildError, match="does not define"):
            validate(output_db, authorities=[AUTHORITY])

    def test_inspect_counts_the_ids(self, run: Build, output_db: Path) -> None:
        run(epsg_projected(), epsg_transformation())
        assert inspect_database(output_db)["identifiers_by_scheme"] == {"OSDU": 2}

    def test_ids_resolve_through_geodesy(self, run: Build, output_db: Path) -> None:
        """The point of recording them: an OSDU id works where a code does."""
        from geodetic_engine.geodesy import (
            CoordinateReferenceSystem,
            Transformation,
            UnresolvableCRSError,
        )

        run(*identified_catalogue())
        with proj_reading(output_db):
            bound_crs = CoordinateReferenceSystem.from_user_input(
                f"opendes:{BOUND_ID}:"
            )
            assert bound_crs.crs.is_bound
            assert "Example 2020 to WGS 84 (1)" in bound_crs.crs.to_wkt()

            stated = Transformation(
                f"opendes:{GEOGRAPHIC_ID}",
                "EPSG:4326",
                operation=f"opendes:{HELMERT_ID}",
            )
            assert stated.operation.authority_code == "OSDU:9100"
            via_bound = Transformation(f"opendes:{BOUND_ID}", "EPSG:4326")
            point = [(3.0, 60.0)]
            (got,) = via_bound.transform(point).coordinates.to_list()
            (want,) = stated.transform(point).coordinates.to_list()
            assert got == pytest.approx(want, abs=1e-12)

            epsg = CoordinateReferenceSystem.from_user_input(
                f"another-partition:{EPSG_PROJECTED_ID}"
            )
            assert epsg.authority_code == "EPSG:23031"
            with pytest.raises(UnresolvableCRSError, match="records no OSDU id"):
                CoordinateReferenceSystem.from_user_input(
                    f"opendes:{CRS_ENTITY}:Projected:EPSG::23032"
                )


# ESRI WKT the way OSDU's persistableReference states it.
GEOGCS_EXAMPLE = (
    'GEOGCS["GCS_Example_2020",DATUM["D_Example_2020",SPHEROID["Example_1980",'
    '6378137.0,298.257222101]],PRIMEM["Greenwich",0.0],'
    'UNIT["Degree",0.0174532925199433],AUTHORITY["OSDU",4100]]'
)
GEOGCS_WGS84 = (
    'GEOGCS["GCS_WGS_1984",DATUM["D_WGS_1984",SPHEROID["WGS_1984",6378137.0,'
    '298.257223563]],PRIMEM["Greenwich",0.0],UNIT["Degree",0.0174532925199433]]'
)
GEOGCS_ED50 = (
    'GEOGCS["GCS_European_1950",DATUM["D_European_1950",'
    'SPHEROID["International_1924",6378388.0,297.0]],PRIMEM["Greenwich",0.0],'
    'UNIT["Degree",0.0174532925199433]]'
)
PROJCS_23031 = (
    f'PROJCS["ED_1950_UTM_Zone_31N",{GEOGCS_ED50},'
    'PROJECTION["Transverse_Mercator"],PARAMETER["False_Easting",500000.0],'
    'PARAMETER["False_Northing",0.0],PARAMETER["Central_Meridian",3.0],'
    'PARAMETER["Scale_Factor",0.9996],PARAMETER["Latitude_Of_Origin",0.0],'
    'UNIT["Meter",1.0],AUTHORITY["EPSG",23031]]'
)


def geogtran(name: str, source: str, code: str, auth: str = "EPSG") -> str:
    return (
        f'GEOGTRAN["{name}",{source},{GEOGCS_WGS84},METHOD["Position_Vector"],'
        'PARAMETER["X_Axis_Translation",-116.641],'
        'PARAMETER["Y_Axis_Translation",-56.931],'
        'PARAMETER["Z_Axis_Translation",-110.559],'
        'PARAMETER["X_Axis_Rotation",0.893],PARAMETER["Y_Axis_Rotation",0.921],'
        'PARAMETER["Z_Axis_Rotation",-0.917],PARAMETER["Scale_Difference",-3.52],'
        f'OPERATIONACCURACY[1.0],AUTHORITY["{auth}",{code}]]'
    )


def envelope(
    kind: str, auth: str | None, code: str, name: str, **members: Any
) -> dict[str, Any]:
    stated: dict[str, Any] = {}
    if auth is not None:
        stated["authCode"] = {"auth": auth, "code": code}
    return stated | members | {"name": name, "type": kind, "ver": "PE_10_9_1"}


def payload(data: dict[str, Any]) -> str:
    """A persistableReference string, compact as OSDU publishes it."""
    return json.dumps(data, separators=(",", ":"))


GEOGRAPHIC_REFERENCE = envelope(
    "LBC", "OSDU", "4100", "GCS_Example_2020", wkt=GEOGCS_EXAMPLE
)
HELMERT_REFERENCE = envelope(
    "ST",
    "OSDU",
    "9100",
    "Example_2020_To_WGS_1984_1",
    wkt=geogtran("Example_2020_To_WGS_1984_1", GEOGCS_EXAMPLE, "9100", "OSDU"),
)
BOUND_REFERENCE = envelope(
    "EBC",
    "OSDU",
    "4100001",
    "Example 2020 * EXAMPLE [4100,9100]",
    lateBoundCRS=GEOGRAPHIC_REFERENCE,
    singleCT=HELMERT_REFERENCE,
)
PROJECTED_REFERENCE = envelope(
    "LBC", "EPSG", "23031", "ED_1950_UTM_Zone_31N", wkt=PROJCS_23031
)
TRANSFORMATION_REFERENCE = envelope(
    "ST",
    "EPSG",
    "1612",
    "ED_1950_To_WGS_1984_23",
    wkt=geogtran("ED_1950_To_WGS_1984_23", GEOGCS_ED50, "1612"),
)


def referenced(
    entry: dict[str, Any], reference: dict[str, Any] | Any
) -> dict[str, Any]:
    """The record, publishing a persistableReference."""
    stated = payload(reference) if isinstance(reference, dict) else reference
    entry["data"]["PersistableReference"] = stated
    return entry


def referenced_catalogue() -> tuple[dict[str, Any], ...]:
    """The identified catalogue, every record publishing its reference."""
    geographic_entry, helmert_entry, bound_entry, projected, transformation = (
        identified_catalogue()
    )
    return (
        referenced(geographic_entry, GEOGRAPHIC_REFERENCE),
        referenced(helmert_entry, HELMERT_REFERENCE),
        referenced(bound_entry, BOUND_REFERENCE),
        referenced(projected, PROJECTED_REFERENCE),
        referenced(transformation, TRANSFORMATION_REFERENCE),
    )


def references(database: Path) -> dict[str, tuple[Any, ...]]:
    """Each recorded reference by id: kind, stated authority, version, payload."""
    return {
        identifier: tuple(rest)
        for identifier, *rest in rows(
            database,
            "SELECT identifier, kind, stated_auth_name, stated_code, version, "
            "persistable_reference FROM geodetic_engine_osdu_persistable_reference",
        )
    }


class TestPersistableReferences:
    """Every record's persistableReference, recorded verbatim against its id."""

    def test_every_identified_record_has_its_reference_recorded_verbatim(
        self, run: Build, output_db: Path
    ) -> None:
        report = run(*referenced_catalogue())
        assert references(output_db) == {
            GEOGRAPHIC_ID: (
                "LBC",
                "OSDU",
                "4100",
                "PE_10_9_1",
                payload(GEOGRAPHIC_REFERENCE),
            ),
            HELMERT_ID: ("ST", "OSDU", "9100", "PE_10_9_1", payload(HELMERT_REFERENCE)),
            BOUND_ID: ("EBC", "OSDU", "4100001", "PE_10_9_1", payload(BOUND_REFERENCE)),
            # Neither EPSG object is imported, being in the base proj.db
            # already; both references are recorded all the same.
            EPSG_PROJECTED_ID: (
                "LBC",
                "EPSG",
                "23031",
                "PE_10_9_1",
                payload(PROJECTED_REFERENCE),
            ),
            EPSG_TRANSFORMATION_ID: (
                "ST",
                "EPSG",
                "1612",
                "PE_10_9_1",
                payload(TRANSFORMATION_REFERENCE),
            ),
        }
        stated = report.persistable_references
        assert stated["enabled"] is True
        assert (stated["recorded"], stated["written"], stated["unchanged"]) == (5, 5, 0)
        assert stated["missing"] == stated["invalid"] == stated["warnings"] == []
        counts = report.as_dict()["counts"]
        assert counts["persistable_references_recorded"] == 5
        assert counts["persistable_references_missing"] == 0
        assert counts["persistable_references_invalid"] == 0

    def test_the_view_names_the_object_each_reference_is_for(
        self, run: Build, output_db: Path
    ) -> None:
        run(*referenced_catalogue())
        assert set(
            rows(
                output_db,
                "SELECT table_name, auth_name, CAST(code AS TEXT), kind "
                "FROM geodetic_engine_osdu_persistable_reference_view",
            )
        ) == {
            ("geodetic_crs", AUTHORITY, "4100", "LBC"),
            ("helmert_transformation", AUTHORITY, "9100", "ST"),
            ("geodetic_crs", AUTHORITY, "4100001", "EBC"),
            ("projected_crs", "EPSG", "23031", "LBC"),
            ("helmert_transformation", "EPSG", "1612", "ST"),
        }

    def test_a_record_stating_none_is_listed_and_the_build_passes(
        self, run: Build, output_db: Path
    ) -> None:
        report = run(
            epsg_projected(),
            referenced(epsg_transformation(), "  "),
        )
        assert references(output_db) == {}
        assert report.status == "passed"
        assert {
            item["identifier"]: item["reason"]
            for item in report.persistable_references["missing"]
        } == {
            EPSG_PROJECTED_ID: "the record states no PersistableReference",
            EPSG_TRANSFORMATION_ID: "the record's PersistableReference is empty",
        }
        assert report.as_dict()["counts"]["persistable_references_missing"] == 2
        # The ids are recorded all the same: a missing reference costs nothing else.
        assert len(identifiers(output_db)) == 2

    @pytest.mark.parametrize(
        ("stated", "reason"),
        [
            ("not json", "cannot be read"),
            ('{"type":"XYZ","wkt":"GEOGCS[]"}', "cannot be read"),
            (42, "JSON int"),
            (
                payload(envelope("LBC", "EPSG", "23031", "x", wkt='PROJCS["x",')),
                "WKT is malformed",
            ),
            (payload(envelope("EBC", "EPSG", "23031", "x")), "malformed"),
            (payload(TRANSFORMATION_REFERENCE), "states a single transformation"),
        ],
        ids=[
            "not-json",
            "unknown-kind",
            "not-a-string",
            "bad-wkt",
            "no-ct",
            "wrong-entity",
        ],
    )
    def test_a_reference_that_cannot_be_used_is_not_recorded(
        self, run: Build, output_db: Path, stated: Any, reason: str
    ) -> None:
        report = run(referenced(epsg_projected(), stated))
        assert references(output_db) == {}
        assert report.status == "passed"
        (invalid,) = report.persistable_references["invalid"]
        assert invalid["identifier"] == EPSG_PROJECTED_ID
        assert reason in invalid["reason"]
        assert report.as_dict()["counts"]["persistable_references_invalid"] == 1

    def test_disagreements_are_recorded_with_a_warning(
        self, run: Build, output_db: Path
    ) -> None:
        """The string is the catalogue's word; its authCode is only provenance."""
        report = run(
            referenced(
                epsg_transformation(),
                TRANSFORMATION_REFERENCE
                | {"authCode": {"auth": "OSDU", "code": "1612"}},
            ),
            referenced(
                epsg_projected(),
                {
                    key: value
                    for key, value in PROJECTED_REFERENCE.items()
                    if key != "authCode"
                },
            ),
        )
        assert set(references(output_db)) == {EPSG_PROJECTED_ID, EPSG_TRANSFORMATION_ID}
        warnings = {
            item["identifier"]: item["warning"]
            for item in report.persistable_references["warnings"]
        }
        assert (
            "states authCode OSDU:1612, but the record is EPSG:1612"
            in (warnings[EPSG_TRANSFORMATION_ID])
        )
        assert "states no authCode" in warnings[EPSG_PROJECTED_ID]

    def test_an_early_bound_reference_on_an_unbound_crs_is_kept_with_a_warning(
        self, run: Build, output_db: Path
    ) -> None:
        early = envelope(
            "EBC",
            "EPSG",
            "23031",
            "ED50 * [23031,1612]",
            lateBoundCRS=PROJECTED_REFERENCE,
            singleCT=TRANSFORMATION_REFERENCE,
        )
        report = run(referenced(epsg_projected(), early))
        assert references(output_db)[EPSG_PROJECTED_ID][0] == "EBC"
        (warning,) = report.persistable_references["warnings"]
        assert "early bound CRS" in warning["warning"]

    def test_records_whose_id_names_nothing_are_counted_not_recorded(
        self, run: Build, output_db: Path
    ) -> None:
        report = run(referenced(epsg_projected(Code="99999999"), PROJECTED_REFERENCE))
        assert references(output_db) == {}
        assert report.persistable_references["unlinked"] == 1

    def test_one_id_publishing_two_references_records_neither(
        self, run: Build, output_db: Path
    ) -> None:
        changed = PROJECTED_REFERENCE | {"name": "ED50 / UTM 31N (other)"}
        report = run(
            referenced(epsg_projected(), PROJECTED_REFERENCE),
            referenced(epsg_projected(), changed),
        )
        assert references(output_db) == {}
        (invalid,) = report.persistable_references["invalid"]
        assert "2 different persistableReferences" in invalid["reason"]

    def test_references_can_be_left_out(self, run: Build, output_db: Path) -> None:
        report = run(*referenced_catalogue(), osdu_persistable_references=False)
        assert report.persistable_references == {"enabled": False}
        assert len(identifiers(output_db)) == 5
        assert not rows(
            output_db,
            "SELECT 1 FROM sqlite_master WHERE name = "
            "'geodetic_engine_osdu_persistable_reference'",
        )

    def test_inactive_records_left_out_have_no_reference(
        self, run: Build, output_db: Path
    ) -> None:
        report = run(
            referenced(epsg_projected(InactiveIndicator=True), PROJECTED_REFERENCE),
            include_deprecated=False,
        )
        assert references(output_db) == {}
        assert report.persistable_references["unlinked"] == 0

    def test_appending_the_same_catalogue_again_changes_nothing(
        self, run: Build, output_db: Path
    ) -> None:
        run(*referenced_catalogue())
        again = run(*referenced_catalogue(), append=True)
        stated = again.persistable_references
        assert (stated["recorded"], stated["written"], stated["unchanged"]) == (5, 0, 5)
        assert len(references(output_db)) == 5

    def test_an_append_that_would_change_a_reference_is_refused(
        self, run: Build, output_db: Path
    ) -> None:
        run(referenced(epsg_projected(), PROJECTED_REFERENCE))
        newer = PROJECTED_REFERENCE | {"ver": "PE_10_10_0"}
        with pytest.raises(ProjDbBuildError, match="different persistableReference"):
            run(referenced(epsg_projected(), newer), append=True)
        assert references(output_db)[EPSG_PROJECTED_ID][3] == "PE_10_9_1"

    def test_overwriting_replaces_a_changed_reference(
        self, run: Build, output_db: Path
    ) -> None:
        run(referenced(epsg_projected(), PROJECTED_REFERENCE))
        newer = PROJECTED_REFERENCE | {"ver": "PE_10_10_0"}
        report = run(
            referenced(epsg_projected(), newer), append=True, overwrite_rows=True
        )
        assert references(output_db)[EPSG_PROJECTED_ID][4] == payload(newer)
        assert report.persistable_references["written"] == 1

    def test_moving_an_id_moves_its_reference(
        self, run: Build, output_db: Path
    ) -> None:
        run(referenced(epsg_projected(), PROJECTED_REFERENCE))
        run(epsg_projected(Code="23032"), append=True, overwrite_rows=True)
        # The id now names EPSG:23032, whose record states no reference, so the
        # reference stated for EPSG:23031 is not handed out for it.
        assert references(output_db) == {}

    def test_validation_checks_each_reference(
        self, run: Build, output_db: Path
    ) -> None:
        run(*referenced_catalogue())
        checked = validate(output_db, authorities=[AUTHORITY])
        assert checked["persistable_references_checked"] == 5
        with closing(sqlite3.connect(output_db)) as connection, connection:
            connection.execute(
                "UPDATE geodetic_engine_osdu_persistable_reference "
                "SET persistable_reference = replace(persistable_reference, "
                "'PE_10_9_1', 'PE_10_10_0') WHERE kind = 'ST'"
            )
        with pytest.raises(ProjDbBuildError, match="digest"):
            validate(output_db, authorities=[AUTHORITY])

    def test_validation_refuses_a_reference_for_another_kind_of_object(
        self, run: Build, output_db: Path
    ) -> None:
        run(referenced(epsg_projected(), PROJECTED_REFERENCE))
        with closing(sqlite3.connect(output_db)) as connection, connection:
            connection.execute(
                "UPDATE geodetic_engine_osdu_persistable_reference SET kind = 'ST'"
            )
        with pytest.raises(ProjDbBuildError, match="different kind"):
            validate(output_db, authorities=[AUTHORITY])

    def test_inspect_counts_the_references_by_kind(
        self, run: Build, output_db: Path
    ) -> None:
        run(*referenced_catalogue())
        assert inspect_database(output_db)["persistable_references_by_kind"] == {
            "EBC": 1,
            "LBC": 2,
            "ST": 2,
        }

    def test_the_summary_warns_of_what_was_not_recorded(
        self, run: Build, caplog: pytest.LogCaptureFixture
    ) -> None:
        report = run(referenced(epsg_projected(), "not json"), epsg_transformation())
        caplog.clear()
        with caplog.at_level(logging.INFO, logger="geodetic_engine.projdb.report"):
            log_summary(report)
        warnings = [
            r.getMessage() for r in caplog.records if r.levelno == logging.WARNING
        ]
        assert any("1 record(s) state no persistableReference" in m for m in warnings)
        assert any("NOT recorded" in m for m in warnings)
        assert any(EPSG_PROJECTED_ID in m for m in warnings)

    def test_references_are_looked_up_through_the_api(
        self, run: Build, output_db: Path
    ) -> None:
        """The point of recording them: the exact string for any object named."""
        from geodetic_engine.geodesy import CoordinateReferenceSystem, transform
        from geodetic_engine.persistablereference import (
            Kind,
            missing_reference_reason,
            stored_reference,
        )

        stating_none = with_id(
            crs_record(
                CodeSpace="EPSG",
                Code="23032",
                Name="ED50 / UTM zone 32N",
                CoordinateReferenceSystemType=PROJECTED_CRS,
                Kind="projected",
            ),
            f"{CRS_ENTITY}:Projected:EPSG::23032",
        )
        run(*referenced_catalogue(), stating_none)
        found = stored_reference(f"opendes:{BOUND_ID}:", database=output_db)
        assert found is not None
        assert found.kind is Kind.EARLY_BOUND_CRS
        assert found.payload == payload(BOUND_REFERENCE)
        assert found.authority_code == f"{AUTHORITY}:4100001"
        assert stored_reference("EPSG:1612", database=output_db) is not None
        assert stored_reference("EPSG:23032", database=output_db) is None
        assert "states no persistableReference" in missing_reference_reason(
            "EPSG:23032", database=output_db
        )
        with proj_reading(output_db):
            bound_crs = CoordinateReferenceSystem.from_user_input(f"opendes:{BOUND_ID}")
            stored = bound_crs.stored_persistable_reference()
            assert stored is not None and stored.identifier == BOUND_ID
            result = transform(
                "EPSG:23031",
                "EPSG:4326",
                [(500000.0, 6600000.0)],
                operation=f"opendes:{EPSG_TRANSFORMATION_ID}",
            )
            stored = result.operation.stored_persistable_reference()
            assert stored is not None and stored.identifier == EPSG_TRANSFORMATION_ID

    @pytest.mark.parametrize("newer", [None, "not json"], ids=["none", "unusable"])
    def test_an_append_without_a_usable_reference_keeps_the_earlier_one(
        self, run: Build, output_db: Path, newer: str | None
    ) -> None:
        """Kept, as a changed reference would be, and reported as kept."""
        from geodetic_engine.persistablereference import (
            missing_reference_reason,
            stored_reference,
        )

        run(referenced(epsg_projected(), PROJECTED_REFERENCE))
        record = (
            epsg_projected() if newer is None else referenced(epsg_projected(), newer)
        )
        report = run(record, append=True)
        stated = report.persistable_references
        assert stated["missing"] == stated["invalid"] == []
        assert stated["kept"] == 1
        (warning,) = stated["warnings"]
        assert "an earlier build recorded is kept" in warning["warning"]
        assert references(output_db)[EPSG_PROJECTED_ID][4] == payload(
            PROJECTED_REFERENCE
        )
        found = stored_reference("EPSG:23031", database=output_db)
        assert found is not None
        assert "is recorded" in missing_reference_reason(
            "EPSG:23031", database=output_db
        )

    @pytest.mark.parametrize("newer", [None, "not json"], ids=["none", "unusable"])
    def test_overwriting_removes_a_reference_the_catalogue_no_longer_states(
        self, run: Build, output_db: Path, newer: str | None
    ) -> None:
        from geodetic_engine.persistablereference import stored_reference

        run(referenced(epsg_projected(), PROJECTED_REFERENCE))
        record = (
            epsg_projected() if newer is None else referenced(epsg_projected(), newer)
        )
        report = run(record, append=True, overwrite_rows=True)
        stated = report.persistable_references
        assert stated["removed"] == 1
        (entry,) = stated["missing"] + stated["invalid"]
        assert "an earlier build recorded for it was removed" in entry["reason"]
        assert references(output_db) == {}
        assert stored_reference("EPSG:23031", database=output_db) is None

    def test_a_reference_recorded_later_is_not_reported_missing(
        self, run: Build, output_db: Path
    ) -> None:
        """The history still lists the first build's gap; the row wins."""
        from geodetic_engine.persistablereference import missing_reference_reason

        run(epsg_projected())
        run(referenced(epsg_projected(), PROJECTED_REFERENCE), append=True)
        assert missing_reference_reason("EPSG:23031", database=output_db) == (
            "a persistable reference is recorded for EPSG:23031"
        )
