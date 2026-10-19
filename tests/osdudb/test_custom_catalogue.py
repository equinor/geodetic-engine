"""A custom catalogue built on top of the official one.

The official catalogue is built first; an organisation's catalogue is then
appended with ``overwrite_rows``. Its own CRSs are added, a record it states
differently replaces the official definition and is named in the report, and a
record it states exactly as the official one did is left as it was.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import pytest

from geodetic_engine.geodesy.osdu_id import CRS_ENTITY
from geodetic_engine.osdudb.build import build
from geodetic_engine.osdudb.catalog import OsduCatalog
from geodetic_engine.projdb.errors import ProjDbBuildError
from geodetic_engine.projdb.report import BuildReport, log_summary

from .conftest import AUTHORITY, make_config, write_catalog
from .test_build import (
    GEOGRAPHIC_ID,
    GEOGRAPHIC_REFERENCE,
    Build,
    geographic,
    payload,
    projected,
    referenced,
    referenced_catalogue,
    references,
    rows,
    with_id,
)

PROJECTED_ID = f"{CRS_ENTITY}:Projected:{AUTHORITY}::32100"
CORRECTED = "Example 2020 (corrected)"
CORRECTED_REFERENCE = GEOGRAPHIC_REFERENCE | {"name": "GCS_Example_2020_corrected"}


@pytest.fixture
def run(base_proj_db: Path, output_db: Path, catalog_path: Path) -> Build:
    def _run(*entries: dict[str, Any], **overrides: Any) -> BuildReport:
        write_catalog(catalog_path, *entries)
        config = make_config(base_proj_db, output_db, catalog_path, **overrides)
        return build(config, catalog=OsduCatalog.from_file(catalog_path))

    return _run


def custom_catalogue() -> tuple[dict[str, Any], ...]:
    """The official records, one of them corrected, and a CRS of its own."""
    _, *unchanged = referenced_catalogue()
    return (
        referenced(
            with_id(geographic(Name=CORRECTED), GEOGRAPHIC_ID), CORRECTED_REFERENCE
        ),
        *unchanged,
        with_id(projected(), PROJECTED_ID),
    )


def test_the_custom_catalogue_adds_its_own_and_replaces_what_it_restates(
    run: Build, output_db: Path
) -> None:
    run(*referenced_catalogue())

    report = run(*custom_catalogue(), append=True, overwrite_rows=True)

    replaced = {
        (item["table"], item["auth_name"], item["code"]): item
        for item in report.overrides["objects"]
    }
    assert replaced[("geodetic_crs", AUTHORITY, "4100")]["changed"] == ["name"]
    assert replaced[("geodetic_crs", AUTHORITY, "4100")]["previous_name"] == (
        "Example 2020"
    )
    assert [
        item["identifier"] for item in report.overrides["persistable_references"]
    ] == [GEOGRAPHIC_ID]
    assert report.overrides["identifiers"] == []
    assert report.overrides["unchanged"] > 0
    assert report.as_dict()["counts"]["objects_replaced"] == len(replaced)

    assert rows(
        output_db,
        "SELECT name FROM geodetic_crs WHERE auth_name = ? AND code = '4100'",
        AUTHORITY,
    ) == [(CORRECTED,)]
    assert references(output_db)[GEOGRAPHIC_ID][4] == payload(CORRECTED_REFERENCE)
    assert rows(
        output_db,
        "SELECT name FROM projected_crs WHERE auth_name = ? AND code = '32100'",
        AUTHORITY,
    ) == [("Example 2020 / UTM zone 31N",)]
    assert ("projected_crs", AUTHORITY, "32100") not in replaced


def test_what_was_replaced_is_a_warning_in_the_summary(
    run: Build, caplog: pytest.LogCaptureFixture
) -> None:
    run(*referenced_catalogue())
    report = run(*custom_catalogue(), append=True, overwrite_rows=True)

    with caplog.at_level(logging.WARNING, logger="geodetic_engine.projdb.report"):
        log_summary(report)

    warnings = [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]
    assert any("REPLACED by this source's" in message for message in warnings)
    assert any(
        f"geodetic_crs {AUTHORITY}:4100 Example 2020 -> {CORRECTED}" in message
        for message in warnings
    )
    assert any("persistableReference(s) an earlier build" in m for m in warnings)
    assert any(GEOGRAPHIC_ID in message for message in warnings)


def test_a_long_list_of_replacements_is_counted_and_the_rest_logged_to_the_file(
    run: Build, caplog: pytest.LogCaptureFixture
) -> None:
    report = run(*referenced_catalogue())
    report.overrides["persistable_references"] = [
        {"identifier": f"record-{index}"} for index in range(30)
    ]

    with caplog.at_level(logging.DEBUG, logger="geodetic_engine.projdb.report"):
        log_summary(report)

    levels = {r.getMessage().strip(): r.levelno for r in caplog.records}
    assert levels["30 persistableReference(s) an earlier build recorded were REPLACED:"]
    assert levels["record-24"] == logging.WARNING
    assert levels["record-25"] == logging.DEBUG
    assert any("... and 5 more" in message for message in levels)


def test_the_same_catalogue_again_replaces_nothing(run: Build) -> None:
    run(*referenced_catalogue())

    report = run(*referenced_catalogue(), append=True, overwrite_rows=True)

    assert report.overrides["objects"] == []
    assert report.overrides["persistable_references"] == []
    assert report.overrides["unchanged"] > 0


def test_without_overwrite_rows_a_restated_record_is_refused_with_the_remedy(
    run: Build, output_db: Path
) -> None:
    run(*referenced_catalogue())

    with pytest.raises(ProjDbBuildError, match="--overwrite-rows"):
        run(*custom_catalogue(), append=True)

    assert rows(
        output_db,
        "SELECT name FROM geodetic_crs WHERE auth_name = ? AND code = '4100'",
        AUTHORITY,
    ) == [("Example 2020",)]
