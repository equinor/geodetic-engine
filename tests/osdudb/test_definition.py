"""Taking WKT apart into proj.db building blocks."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from pathlib import Path

import pytest
from pyproj import CRS
from pyproj.crs import CoordinateOperation

from geodetic_engine.osdudb import definition as df
from geodetic_engine.osdudb.errors import UnreadableDefinitionError

from .conftest import (
    CUSTOM_GEOGRAPHIC_WKT,
    CUSTOM_PROJECTED_WKT,
    CUSTOM_TRANSFORMATION_WKT,
)


@pytest.fixture
def connection(base_proj_db: Path) -> Iterator[sqlite3.Connection]:
    with sqlite3.connect(f"file:{base_proj_db}?mode=ro", uri=True) as handle:
        yield handle


@pytest.fixture
def units(connection: sqlite3.Connection) -> df.UnitResolver:
    return df.UnitResolver(connection)


class TestParsing:
    def test_a_record_with_no_wkt_is_refused(self) -> None:
        with pytest.raises(UnreadableDefinitionError, match="carries no WKT"):
            df.parse_crs(None, "CRS OSDU:1")

    def test_wkt_proj_rejects_is_refused(self) -> None:
        with pytest.raises(UnreadableDefinitionError, match="PROJ rejects"):
            df.parse_crs('GEOGCRS["broken"', "CRS OSDU:1")

    def test_identifiers_survive_the_round_trip(self) -> None:
        crs = df.parse_crs(CUSTOM_GEOGRAPHIC_WKT, "CRS OSDU:4100")
        assert df.identifier_of(crs) == df.Identifier("OSDU", "4100")
        assert df.identifier_of(crs.datum) == df.Identifier("OSDU", "6100")
        assert df.identifier_of(crs.ellipsoid) == df.Identifier("OSDU", "7100")
        assert df.identifier_of(crs.prime_meridian) == df.Identifier("EPSG", "8901")


class TestUnitResolver:
    def test_the_shorthand_units_resolve(self, units: df.UnitResolver) -> None:
        assert units.resolve("metre", described="x") == df.Identifier("EPSG", "9001")
        assert units.resolve("degree", described="x") == df.Identifier("EPSG", "9102")
        assert units.resolve("unity", described="x") == df.Identifier("EPSG", "9201")

    def test_a_unit_proj_exports_without_a_code_resolves_by_name(
        self, units: df.UnitResolver
    ) -> None:
        # PROJ drops a unit's identifier on export, so arc-second arrives as a
        # name and a factor and has to be matched back to EPSG:9104.
        unit = {
            "type": "AngularUnit",
            "name": "arc-second",
            "conversion_factor": 4.84813681109536e-06,
        }
        assert units.resolve(unit, described="x") == df.Identifier("EPSG", "9104")

    def test_a_unit_resolves_by_factor_when_the_name_is_unfamiliar(
        self, units: df.UnitResolver
    ) -> None:
        unit = {
            "type": "ScaleUnit",
            "name": "ppm",
            "conversion_factor": 1e-06,
        }
        assert units.resolve(unit, described="x") == df.Identifier("EPSG", "9202")

    def test_an_explicit_identifier_wins(self, units: df.UnitResolver) -> None:
        unit = {
            "type": "LinearUnit",
            "name": "whatever",
            "id": {"authority": "EPSG", "code": 9002},
        }
        assert units.resolve(unit, described="x") == df.Identifier("EPSG", "9002")

    def test_an_unknown_unit_is_refused_rather_than_defaulted(
        self, units: df.UnitResolver
    ) -> None:
        # Falling back to a default here would silently rescale every value
        # expressed in the unit.
        unit = {"type": "LinearUnit", "name": "cubits", "conversion_factor": 0.4572}
        with pytest.raises(UnreadableDefinitionError, match="not a unit of measure"):
            units.resolve(unit, described="the semi-major axis")


class TestMeasure:
    def test_a_bare_number_uses_the_default_unit(self) -> None:
        assert df.measure(6378137, "metre") == (6378137.0, "metre")

    def test_an_object_carries_its_own_unit(self) -> None:
        unit = {"type": "LinearUnit", "name": "foot"}
        assert df.measure({"value": 20926201.0, "unit": unit}, "metre") == (
            20926201.0,
            unit,
        )


class TestEllipsoid:
    def test_the_stated_defining_parameter_is_kept_and_the_other_left_null(
        self, units: df.UnitResolver
    ) -> None:
        crs = df.parse_crs(CUSTOM_GEOGRAPHIC_WKT, "CRS OSDU:4100")
        built = df.ellipsoid_row(crs.ellipsoid, units)
        assert built is not None
        identifier, row = built
        assert identifier == df.Identifier("OSDU", "7100")
        assert row["semi_major_axis"] == 6378137
        assert row["inv_flattening"] == 298.257222101
        # An ellipsoid is defined by one or the other, never both.
        assert row["semi_minor_axis"] is None
        assert (row["uom_auth_name"], row["uom_code"]) == ("EPSG", "9001")
        assert (row["celestial_body_auth_name"], row["celestial_body_code"]) == (
            "PROJ",
            "EARTH",
        )

    def test_a_sphere_is_stored_with_equal_axes(self, units: df.UnitResolver) -> None:
        wkt = (
            'GEOGCRS["S",DATUM["S",ELLIPSOID["Sphere",6371000,0,'
            'LENGTHUNIT["metre",1,ID["EPSG",9001]],ID["OSDU",7101]],ID["OSDU",6101]],'
            'CS[ellipsoidal,2,ID["EPSG",6422]],AXIS["lat",north],AXIS["lon",east],'
            'ANGLEUNIT["degree",0.0174532925199433,ID["EPSG",9102]],ID["OSDU",4101]]'
        )
        crs = df.parse_crs(wkt, "CRS OSDU:4101")
        built = df.ellipsoid_row(crs.ellipsoid, units)
        assert built is not None
        row = built[1]
        assert row["semi_major_axis"] == row["semi_minor_axis"] == 6371000
        assert not row["inv_flattening"]


class TestCoordinateSystem:
    def test_axes_keep_the_order_the_wkt_declares(self, units: df.UnitResolver) -> None:
        crs = df.parse_crs(CUSTOM_GEOGRAPHIC_WKT, "CRS OSDU:4100")
        # PROJ does not export a coordinate system's code, so OSDU supplies it.
        system, axes = df.coordinate_system_rows(
            crs, df.Identifier("EPSG", "6422"), units
        )
        assert system == {
            "auth_name": "EPSG",
            "code": "6422",
            "type": "ellipsoidal",
            "dimension": 2,
        }
        assert [
            (a["abbrev"], a["orientation"], a["coordinate_system_order"]) for a in axes
        ] == [
            ("Lat", "north", 1),
            ("Lon", "east", 2),
        ]
        assert all(a["uom_code"] == "9102" for a in axes)

    def test_axis_codes_are_unique_within_the_system(
        self, units: df.UnitResolver
    ) -> None:
        crs = df.parse_crs(CUSTOM_GEOGRAPHIC_WKT, "CRS OSDU:4100")
        _, axes = df.coordinate_system_rows(crs, df.Identifier("EPSG", "6422"), units)
        assert [a["code"] for a in axes] == ["6422_1", "6422_2"]


class TestDatum:
    def test_a_geodetic_datum_references_its_ellipsoid_and_meridian(self) -> None:
        crs = df.parse_crs(CUSTOM_GEOGRAPHIC_WKT, "CRS OSDU:4100")
        assert df.datum_table(crs.datum) == "geodetic_datum"
        built = df.datum_row(
            crs.datum,
            "geodetic_datum",
            ellipsoid=df.Identifier("OSDU", "7100"),
            prime_meridian=df.Identifier("EPSG", "8901"),
        )
        assert built is not None
        identifier, row = built
        assert identifier == df.Identifier("OSDU", "6100")
        assert row["name"] == "Example Datum 2020"
        assert (row["ellipsoid_auth_name"], row["ellipsoid_code"]) == ("OSDU", "7100")
        assert (row["prime_meridian_auth_name"], row["prime_meridian_code"]) == (
            "EPSG",
            "8901",
        )

    def test_a_geodetic_datum_without_an_ellipsoid_is_refused(self) -> None:
        crs = df.parse_crs(CUSTOM_GEOGRAPHIC_WKT, "CRS OSDU:4100")
        with pytest.raises(UnreadableDefinitionError, match="no identified ellipsoid"):
            df.datum_row(crs.datum, "geodetic_datum")

    def test_a_dynamic_datum_keeps_its_reference_epoch(self) -> None:
        wkt = (
            'GEOGCRS["D",DYNAMIC[FRAMEEPOCH[2010.0]],'
            'DATUM["D frame",ELLIPSOID["GRS 1980",6378137,298.257222101,'
            'LENGTHUNIT["metre",1,ID["EPSG",9001]],ID["EPSG",7019]],ID["OSDU",6102]],'
            'CS[ellipsoidal,2,ID["EPSG",6422]],AXIS["lat",north],AXIS["lon",east],'
            'ANGLEUNIT["degree",0.0174532925199433,ID["EPSG",9102]],ID["OSDU",4102]]'
        )
        crs = df.parse_crs(wkt, "CRS OSDU:4102")
        built = df.datum_row(
            crs.datum,
            "geodetic_datum",
            ellipsoid=df.Identifier("EPSG", "7019"),
            prime_meridian=df.Identifier("EPSG", "8901"),
        )
        assert built is not None
        # Dropping the epoch would turn a time dependent datum into a static one.
        assert built[1]["frame_reference_epoch"] == "2010"


class TestParameters:
    def test_units_and_values_are_read_unconverted(
        self, units: df.UnitResolver
    ) -> None:
        operation = CoordinateOperation.from_string(CUSTOM_TRANSFORMATION_WKT)
        parameters = df.parameters_of(operation, units, "transformation OSDU:9100")
        by_code = {p.code: p for p in parameters}
        assert by_code["8605"].value == 1.5
        assert (by_code["8605"].uom_auth_name, by_code["8605"].uom_code) == (
            "EPSG",
            "9001",
        )
        # The rotation stays in arc-seconds; PROJ applies the unit itself.
        assert by_code["8608"].value == 0.1
        assert by_code["8608"].uom_code == "9104"
        assert by_code["8611"].uom_code == "9202"

    def test_a_conversions_parameters_are_read_from_the_crs(
        self, units: df.UnitResolver
    ) -> None:
        crs = df.parse_crs(CUSTOM_PROJECTED_WKT, "CRS OSDU:32100")
        parameters = df.parameters_of(
            crs.coordinate_operation, units, "conversion OSDU:17100"
        )
        assert [(p.code, p.value) for p in parameters] == [
            ("8801", 0.0),
            ("8802", 3.0),
            ("8805", 0.9996),
            ("8806", 500000.0),
            ("8807", 0.0),
        ]

    def test_a_parameter_naming_a_grid_file_is_recorded_as_a_file(
        self, units: df.UnitResolver
    ) -> None:
        conversion = {
            "parameters": [
                {
                    "name": "Latitude and longitude difference file",
                    "value": "ntv2_0.gsb",
                    "id": {"authority": "EPSG", "code": 8656},
                }
            ]
        }
        parameters = df.parameters_of(conversion, units, "transformation OSDU:1")
        assert parameters[0].file == "ntv2_0.gsb"
        assert parameters[0].value is None


class TestGeodeticType:
    @pytest.mark.parametrize(
        ("code", "expected"),
        [(4326, "geographic 2D"), (4979, "geographic 3D"), (4978, "geocentric")],
    )
    def test_the_proj_db_vocabulary_matches_the_crs(
        self, code: int, expected: str
    ) -> None:
        assert df.geodetic_type(CRS.from_epsg(code)) == expected
