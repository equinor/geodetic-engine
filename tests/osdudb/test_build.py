"""Building a database from a catalogue, end to end.

Each test builds a real proj.db from a copy of the official one and then
queries it, rather than asserting on intermediate row dictionaries, so what is
checked is what a consumer of the database would actually find in it.
"""

from __future__ import annotations

import os
import sqlite3
from collections.abc import Callable, Iterator
from contextlib import contextmanager
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
from geodetic_engine.projdb.report import BuildReport
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
    with sqlite3.connect(f"file:{database}?mode=ro", uri=True) as connection:
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
