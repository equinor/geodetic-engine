"""Reading and indexing an OSDU manifest."""

from __future__ import annotations

from pathlib import Path

import pytest

from geodetic_engine.osdudb.catalog import (
    BOUND_CRS,
    GEODETIC_CRS,
    PROJECTED_CRS,
    TRANSFORMATION,
    OsduCatalog,
)
from geodetic_engine.osdudb.errors import OsduCatalogError

from .conftest import (
    CUSTOM_GEOGRAPHIC_WKT,
    CUSTOM_TRANSFORMATION_WKT,
    authority_code,
    crs_record,
    make_catalog,
    transformation_record,
    write_catalog,
)


def geographic() -> dict:
    return crs_record(
        Code="4100",
        Name="Example 2020",
        CoordinateReferenceSystemType=GEODETIC_CRS,
        Kind="geographic 2D",
        OGCWellKnownText2=CUSTOM_GEOGRAPHIC_WKT,
        Datum=authority_code("OSDU", 6100),
        CoordinateSystem=authority_code("EPSG", 6422),
    )


def transformation() -> dict:
    return transformation_record(
        Code="9100",
        Name="Example 2020 to WGS 84 (1)",
        OGCWellKnownText2=CUSTOM_TRANSFORMATION_WKT,
        SourceCRS=authority_code("OSDU", 4100),
        TargetCRS=authority_code("EPSG", 4326),
        Method=authority_code("EPSG", 9606, Name="Position Vector transformation"),
    )


class TestReading:
    def test_records_are_indexed_by_authority_and_code(self) -> None:
        catalog = make_catalog(geographic(), transformation())
        assert len(catalog) == 2
        assert catalog.crs("OSDU", "4100") is not None
        assert catalog.operation("OSDU", "9100") is not None

    def test_a_crs_and_an_operation_are_indexed_apart(self) -> None:
        # Their code spaces are not guaranteed disjoint, and resolving a bound
        # CRS's source against an operation of the same code would silently
        # substitute a different object.
        catalog = make_catalog(geographic(), transformation())
        assert catalog.operation("OSDU", "4100") is None
        assert catalog.crs("OSDU", "9100") is None

    def test_records_carry_their_type_and_a_readable_identity(self) -> None:
        record = make_catalog(geographic()).crs("OSDU", "4100")
        assert record is not None
        assert record.type == GEODETIC_CRS
        assert record.is_operation is False
        assert record.name == "Example 2020"
        assert "GeodeticCRS OSDU:4100" in record.described

    def test_records_can_be_selected_by_type(self) -> None:
        catalog = make_catalog(geographic(), transformation())
        assert [r.code for r in catalog.records(GEODETIC_CRS)] == ["4100"]
        assert [r.code for r in catalog.records(TRANSFORMATION)] == ["9100"]
        assert list(catalog.records(PROJECTED_CRS, BOUND_CRS)) == []
        assert len(list(catalog.records())) == 2

    def test_an_unrecognised_kind_is_ignored(self) -> None:
        other = {"kind": "osdu:wks:reference-data--UnitOfMeasure:1.0.0", "data": {}}
        assert len(make_catalog(geographic(), other)) == 1

    def test_a_record_without_a_code_is_ignored(self) -> None:
        broken = geographic()
        del broken["data"]["Code"]
        del broken["data"]["CodeAsNumber"]
        assert len(make_catalog(broken)) == 0

    def test_the_kind_version_is_not_pinned(self) -> None:
        # A minor version bump must not silently drop half the catalogue.
        record = geographic()
        record["kind"] = "osdu:wks:reference-data--CoordinateReferenceSystem:9.9.9"
        assert len(make_catalog(record)) == 1


class TestFromFile:
    def test_a_manifest_is_read_from_disk(self, tmp_path: Path) -> None:
        path = write_catalog(tmp_path / "CRS_CT.json", geographic())
        catalog = OsduCatalog.from_file(path)
        assert catalog.path == path
        assert catalog.crs("OSDU", "4100") is not None

    def test_a_missing_file_is_named(self, tmp_path: Path) -> None:
        with pytest.raises(OsduCatalogError, match="could not read"):
            OsduCatalog.from_file(tmp_path / "absent.json")

    def test_a_file_that_is_not_json_is_named(self, tmp_path: Path) -> None:
        path = tmp_path / "CRS_CT.json"
        path.write_text("not json at all", encoding="utf-8")
        with pytest.raises(OsduCatalogError, match="not valid JSON"):
            OsduCatalog.from_file(path)

    def test_a_document_without_reference_data_is_not_a_manifest(self) -> None:
        with pytest.raises(OsduCatalogError, match="not an OSDU manifest"):
            OsduCatalog.from_document({"kind": "something else"})
