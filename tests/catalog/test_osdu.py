"""What a database built from an OSDU catalogue adds to a listing."""

from __future__ import annotations

import json
import os
import re
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pyproj
import pytest

from geodetic_engine import catalog
from geodetic_engine.osdudb.build import build
from geodetic_engine.osdudb.config import OsduBuildConfig
from tests.support import installed_proj_db

CATALOG = (
    Path(__file__).parents[2]
    / "catalogs"
    / "osdu"
    / "CRS_CT_osdu_official_crs_catalog.json"
)
RECORD = re.compile(
    r"reference-data--(?:CoordinateReferenceSystem:\w+|CoordinateTransformation)"
    r":\w+::[\w.]+(?:::[\w.]+)?"
)
WANTED = (
    "reference-data--CoordinateReferenceSystem:BoundGeographic2D:EPSG::4230_EPSG::1612",
    "reference-data--CoordinateReferenceSystem:Projected:EPSG::23031",
    "reference-data--CoordinateTransformation:EPSG::1612",
)


@pytest.fixture(scope="module")
def osdu_database(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """A proj.db built from a few official catalogue records."""
    document = json.loads(CATALOG.read_text(encoding="utf-8"))
    records = {
        record["id"].split(":", 1)[1]: record for record in document["ReferenceData"]
    }
    wanted = list(WANTED)
    chosen: dict[str, Any] = {}
    while wanted:
        key = wanted.pop()
        if key not in chosen and key in records:
            chosen[key] = records[key]
            wanted += RECORD.findall(json.dumps(records[key]))
    subset = tmp_path_factory.mktemp("catalog") / "CRS_CT.json"
    subset.write_text(
        json.dumps({**document, "ReferenceData": list(chosen.values())}),
        encoding="utf-8",
    )
    directory = tmp_path_factory.mktemp("osdu-proj-data")
    build(
        OsduBuildConfig(
            catalog=subset,
            base_proj_db=installed_proj_db(),
            output_db=directory / "proj.db",
        )
    )
    return directory


@pytest.fixture
def reading_osdu(osdu_database: Path) -> Iterator[None]:
    previous = pyproj.datadir.get_data_dir()
    pyproj.datadir.set_data_dir(f"{osdu_database}{os.pathsep}{previous}")
    try:
        yield
    finally:
        pyproj.datadir.set_data_dir(previous)


@pytest.mark.usefixtures("reading_osdu")
def test_a_bound_crs_is_listed_as_one() -> None:
    bound = catalog.crs(bound=True, authority="OSDU")
    assert bound
    entry = next(e for e in bound if "1612" in " ".join(e.osdu_ids))
    assert entry.projjson_type == "BoundCRS"
    assert entry.to_json_dict()["type"] == "BoundCRS"
    assert entry.has_persistable_reference
    assert entry.to_summary_dict()["osdu_ids"] == list(entry.osdu_ids)


@pytest.mark.usefixtures("reading_osdu")
def test_epsg_objects_carry_their_osdu_ids() -> None:
    ed50_utm = catalog.crs(code="EPSG:23031").one()
    assert ed50_utm.osdu_ids == (
        "reference-data--CoordinateReferenceSystem:Projected:EPSG::23031",
    )
    operation = catalog.operations(code="EPSG:1612").one()
    assert operation.osdu_ids == (
        "reference-data--CoordinateTransformation:EPSG::1612",
    )
    assert operation.has_persistable_reference


@pytest.mark.usefixtures("reading_osdu")
def test_an_osdu_record_id_names_a_crs_to_filter_by() -> None:
    found = catalog.operations(
        source_crs="osdu:reference-data--CoordinateReferenceSystem:Geographic2D:EPSG::4230:",
        target_crs="EPSG:4326",
    )
    assert "EPSG:1612" in {e.authority_code for e in found}


@pytest.mark.usefixtures("reading_osdu")
def test_a_bound_crs_names_the_crs_it_wraps_however_it_is_given() -> None:
    bound = next(
        e
        for e in catalog.crs(bound=True, authority="OSDU")
        if e.kind is catalog.CrsKind.GEOGRAPHIC_2D
    )
    expected = {
        e.authority_code
        for e in catalog.operations(source_crs="EPSG:4230", target_crs="EPSG:4326")
    }
    assert "EPSG:1612" in expected
    for source in (bound, bound.authority_code, bound.name, bound.resolve()):
        found = catalog.operations(source_crs=source, target_crs="EPSG:4326")
        assert {e.authority_code for e in found} == expected


@pytest.mark.usefixtures("reading_osdu")
def test_the_export_of_an_osdu_listing_validates(listing_validator: Any) -> None:
    document = catalog.crs(authority="OSDU").to_json_dict(include_definitions=True)
    listing_validator.validate(document)


def test_a_stock_database_records_no_osdu_ids() -> None:
    entry = catalog.crs(code="EPSG:23031").one()
    if entry.osdu_ids:
        pytest.skip("the active database was built from an OSDU catalogue")
    assert not entry.has_persistable_reference
    assert "osdu_ids" not in entry.to_summary_dict()
