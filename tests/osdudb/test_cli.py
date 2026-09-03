"""The command line interface.

The headline promise is that a build needs nothing but the catalogue path, so
that is what these check.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from geodetic_engine.osdudb.__main__ import main, sidecar
from geodetic_engine.osdudb.catalog import GEODETIC_CRS

from .conftest import (
    AUTHORITY,
    CUSTOM_GEOGRAPHIC_WKT,
    authority_code,
    crs_record,
    write_catalog,
)


def geographic() -> dict:
    return crs_record(
        Code="4100",
        Name="Example 2020",
        CoordinateReferenceSystemType=GEODETIC_CRS,
        Kind="geographic 2D",
        OGCWellKnownText2=CUSTOM_GEOGRAPHIC_WKT,
        Datum=authority_code(AUTHORITY, 6100, Name="Example Datum 2020"),
        CoordinateSystem=authority_code("EPSG", 6422),
    )


@pytest.fixture
def catalog(tmp_path: Path) -> Path:
    return write_catalog(tmp_path / "CRS_CT.json", geographic())


def test_a_build_needs_nothing_but_the_catalogue(
    catalog: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    output = tmp_path / "out" / "proj.db"
    assert main(["build", str(catalog), "--output", str(output)]) == 0
    assert output.is_file()

    # The database is only half the artefact; it is untraceable without the
    # report and the log beside it.
    report = json.loads(sidecar(output, ".report.json").read_text(encoding="utf-8"))
    assert report["status"] == "passed"
    assert report["source"] == str(catalog)
    assert sidecar(output, ".log").is_file()
    assert str(output) in capsys.readouterr().out


def test_a_dry_run_leaves_nothing_behind(catalog: Path, tmp_path: Path) -> None:
    output = tmp_path / "out" / "proj.db"
    assert main(["build", str(catalog), "--output", str(output), "--dry-run"]) == 0
    assert not output.exists()
    assert not sidecar(output, ".report.json").exists()


def test_authorities_can_be_named_on_the_command_line(
    catalog: Path, tmp_path: Path
) -> None:
    output = tmp_path / "out" / "proj.db"
    assert (
        main(
            [
                "build",
                str(catalog),
                "--output",
                str(output),
                "--authority",
                AUTHORITY,
                "--authority",
                "EPSG",
            ]
        )
        == 0
    )
    report = json.loads(sidecar(output, ".report.json").read_text(encoding="utf-8"))
    assert report["authorities"] == ["EPSG", AUTHORITY]


def test_a_missing_catalogue_is_reported_rather_than_traced(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["build", str(tmp_path / "absent.json")]) == 1
    assert "does not exist" in capsys.readouterr().err


def test_validate_reports_on_an_existing_database(
    catalog: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    output = tmp_path / "out" / "proj.db"
    main(["build", str(catalog), "--output", str(output)])
    capsys.readouterr()

    assert main(["validate", str(output), "--authority", AUTHORITY]) == 0
    summary = json.loads(capsys.readouterr().out)
    assert summary["crs_checked"] == 1


def test_inspect_summarises_what_was_built(
    catalog: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    output = tmp_path / "out" / "proj.db"
    main(["build", str(catalog), "--output", str(output)])
    capsys.readouterr()

    assert main(["inspect", str(output)]) == 0
    summary = json.loads(capsys.readouterr().out)
    assert summary["crs_by_authority"][AUTHORITY] == 1
    assert summary["epsg_version"]


def test_config_shows_the_resolved_settings(
    catalog: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["config", str(catalog)]) == 0
    resolved = json.loads(capsys.readouterr().out)
    assert resolved["catalog"] == str(catalog)
    assert resolved["authorities"] == [AUTHORITY]


def test_the_output_need_not_be_called_proj_db(catalog: Path, tmp_path: Path) -> None:
    # PROJ opens the file named proj.db in each search directory, so validation
    # has to stage any other name; without that it would silently check the
    # installed database instead.
    output = tmp_path / "out" / "osdu-proj.db"
    assert main(["build", str(catalog), "--output", str(output)]) == 0
    report = json.loads(sidecar(output, ".report.json").read_text(encoding="utf-8"))
    assert report["status"] == "passed"
    with sqlite3.connect(f"file:{output}?mode=ro", uri=True) as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM geodetic_crs WHERE auth_name = ?", (AUTHORITY,)
        ).fetchone() == (1,)


def test_append_extends_a_database_rather_than_rebuilding_it(
    catalog: Path, tmp_path: Path
) -> None:
    """A second source adds to the first, which is the point of --append."""
    output = tmp_path / "out" / "proj.db"
    assert main(["build", str(catalog), "--output", str(output)]) == 0

    second = write_catalog(
        tmp_path / "more.json",
        crs_record(
            Code="4101",
            Name="Example 2021",
            CoordinateReferenceSystemType=GEODETIC_CRS,
            Kind="geographic 2D",
            # Same datum as the first build, which is the interesting case: the
            # datum row is already in the database, so it is reused rather than
            # re-imported or collided with.
            OGCWellKnownText2=CUSTOM_GEOGRAPHIC_WKT.replace(
                'ID["OSDU",4100]', 'ID["OSDU",4101]'
            ).replace('GEOGCRS["Example 2020"', 'GEOGCRS["Example 2021"'),
            Datum=authority_code(AUTHORITY, 6100, Name="Example Datum 2020"),
            CoordinateSystem=authority_code("EPSG", 6422),
        ),
    )
    assert main(["build", str(second), "--output", str(output), "--append"]) == 0

    with sqlite3.connect(f"file:{output}?mode=ro", uri=True) as connection:
        codes = {
            str(code)
            for (code,) in connection.execute(
                "SELECT code FROM geodetic_crs WHERE auth_name = ?", (AUTHORITY,)
            )
        }
    assert codes == {"4100", "4101"}

    # The appending build must not overwrite the provenance of the one it
    # extended, so each keeps a report named after itself.
    assert sidecar(output, ".report.json").is_file()
    appended = json.loads(
        sidecar(output, ".report.json", source="osdudb").read_text(encoding="utf-8")
    )
    assert appended["appended"] is True
    assert appended["status"] == "passed"


def test_a_second_build_without_append_starts_over(
    catalog: Path, tmp_path: Path
) -> None:
    """Without --append the output is a fresh copy of the base, as before."""
    output = tmp_path / "out" / "proj.db"
    assert main(["build", str(catalog), "--output", str(output)]) == 0

    empty = write_catalog(tmp_path / "empty.json")
    assert main(["build", str(empty), "--output", str(output)]) == 0

    with sqlite3.connect(f"file:{output}?mode=ro", uri=True) as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM geodetic_crs WHERE auth_name = ?", (AUTHORITY,)
        ).fetchone() == (0,)
