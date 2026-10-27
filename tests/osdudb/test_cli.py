"""The command line interface.

The headline promise is that a build needs nothing but the catalogue path, so
that is what these check.
"""

from __future__ import annotations

import json
import sqlite3
from contextlib import closing, contextmanager
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


def test_official_catalogue_is_available_with_its_recorded_version() -> None:
    from geodetic_engine.osdudb.official import official_catalog

    with official_catalog() as (catalog, version):
        assert catalog.is_file()
        assert catalog.name == "CRS_CT_osdu_official_crs_catalog.json"
        assert version.startswith("osdu/data/data-definitions@")


def test_official_build_records_the_bundled_catalogue_version(
    catalog: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    @contextmanager
    def official_catalog():
        yield catalog, "osdu/data/data-definitions@abc123"

    monkeypatch.setattr(
        "geodetic_engine.osdudb.__main__.official_catalog",
        official_catalog,
        raising=False,
    )
    monkeypatch.setenv("GEODETIC_ENGINE_OSDU_CATALOG", str(tmp_path / "other.json"))
    monkeypatch.setenv("GEODETIC_ENGINE_OSDU_CATALOG_VERSION", "unrelated")
    output = tmp_path / "official" / "proj.db"
    assert main(["build", "--official", "--output", str(output)]) == 0
    report = json.loads(sidecar(output, ".report.json").read_text(encoding="utf-8"))
    assert report["source_version"] == "osdu/data/data-definitions@abc123"


def test_official_and_explicit_catalogue_are_mutually_exclusive(
    catalog: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["build", str(catalog), "--official"]) == 2
    assert "cannot" in capsys.readouterr().err


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
    assert resolved["osdu_persistable_references"] is True


def test_the_output_need_not_be_called_proj_db(catalog: Path, tmp_path: Path) -> None:
    # PROJ opens the file named proj.db in each search directory, so validation
    # has to stage any other name; without that it would silently check the
    # installed database instead.
    output = tmp_path / "out" / "osdu-proj.db"
    assert main(["build", str(catalog), "--output", str(output)]) == 0
    report = json.loads(sidecar(output, ".report.json").read_text(encoding="utf-8"))
    assert report["status"] == "passed"
    with closing(sqlite3.connect(f"file:{output}?mode=ro", uri=True)) as connection:
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

    with closing(sqlite3.connect(f"file:{output}?mode=ro", uri=True)) as connection:
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

    with closing(sqlite3.connect(f"file:{output}?mode=ro", uri=True)) as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM geodetic_crs WHERE auth_name = ?", (AUTHORITY,)
        ).fetchone() == (0,)


def _identified(tmp_path: Path) -> Path:
    """A catalogue whose one record carries its OSDU id."""
    record = geographic() | {
        "id": "{{NAMESPACE}}:reference-data--CoordinateReferenceSystem:"
        "Geographic2D:OSDU::4100"
    }
    return write_catalog(tmp_path / "identified.json", record)


def test_inspect_counts_the_osdu_ids(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    output = tmp_path / "out" / "proj.db"
    main(["build", str(_identified(tmp_path)), "--output", str(output)])
    capsys.readouterr()

    assert main(["inspect", str(output)]) == 0
    assert json.loads(capsys.readouterr().out)["identifiers_by_scheme"] == {"OSDU": 1}


def test_osdu_id_aliases_can_be_turned_off_on_the_command_line(
    tmp_path: Path,
) -> None:
    output = tmp_path / "out" / "proj.db"
    catalog = _identified(tmp_path)
    assert (
        main(["build", str(catalog), "--output", str(output), "--no-osdu-id-aliases"])
        == 0
    )
    with closing(sqlite3.connect(output)) as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM alias_name WHERE source = 'OSDU_ID'"
        ).fetchone() == (0,)
        assert connection.execute(
            "SELECT COUNT(*) FROM geodetic_engine_osdu_object_identifier"
        ).fetchone() == (1,)


_REFERENCE = json.dumps(
    {
        "authCode": {"auth": "OSDU", "code": "4100"},
        "name": "GCS_Example_2020",
        "type": "LBC",
        "ver": "PE_10_9_1",
        "wkt": 'GEOGCS["GCS_Example_2020",DATUM["D_Example_2020",'
        'SPHEROID["Example_1980",6378137.0,298.257222101]],'
        'PRIMEM["Greenwich",0.0],UNIT["Degree",0.0174532925199433]]',
    },
    separators=(",", ":"),
)
_GEOGRAPHIC_ID = "reference-data--CoordinateReferenceSystem:Geographic2D:OSDU::4100"


@pytest.fixture
def referenced(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> Path:
    """A database built from a catalogue whose record states its reference."""
    record = geographic() | {"id": f"{{{{NAMESPACE}}}}:{_GEOGRAPHIC_ID}"}
    record["data"]["PersistableReference"] = _REFERENCE
    catalog = write_catalog(tmp_path / "referenced.json", record)
    output = tmp_path / "out" / "proj.db"
    assert main(["build", str(catalog), "--output", str(output)]) == 0
    capsys.readouterr()
    return output


def test_references_are_exported_verbatim(
    referenced: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["references", str(referenced)]) == 0
    (entry,) = json.loads(capsys.readouterr().out)
    assert entry["identifier"] == _GEOGRAPHIC_ID
    assert entry["persistable_reference"] == _REFERENCE


def test_references_are_looked_up_by_id_or_code(
    referenced: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert (
        main(
            [
                "references",
                str(referenced),
                f"opendes:{_GEOGRAPHIC_ID}",
                "OSDU:4100",
                "EPSG:4326",
                "--pretty",
            ]
        )
        == 0
    )
    found, by_code, missing = json.loads(capsys.readouterr().out)
    assert found["persistable_reference"] == json.loads(_REFERENCE)
    assert by_code["identifier"] == _GEOGRAPHIC_ID
    assert missing["persistable_reference"] is None
    assert "no record of the catalogue" in missing["reason"]


def test_references_with_their_wkt_laid_out(
    referenced: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["references", str(referenced), "--pretty-wkt"]) == 0
    out = capsys.readouterr().out
    assert '"wkt":\n        GEOGCS["GCS_Example_2020",\n' in out


@pytest.mark.parametrize("output_format", ["jsonl", "csv"])
def test_references_in_other_formats(
    referenced: Path,
    tmp_path: Path,
    output_format: str,
    capsys: pytest.CaptureFixture[str],
) -> None:
    target = tmp_path / f"references.{output_format}"
    arguments = ["references", str(referenced), "--format", output_format]
    assert main([*arguments, "--output", str(target)]) == 0
    assert "wrote 1 persistable reference(s)" in capsys.readouterr().err
    assert _GEOGRAPHIC_ID in target.read_text(encoding="utf-8")


def test_reference_filters(
    referenced: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["references", str(referenced), "--entity", "operation"]) == 0
    assert json.loads(capsys.readouterr().out) == []
    assert main(["references", str(referenced), "--kind", "LBC", "--active-only"]) == 0
    assert len(json.loads(capsys.readouterr().out)) == 1


@pytest.mark.parametrize(
    "arguments",
    [
        ["--format", "csv", "--pretty"],
        ["--format", "jsonl", "--pretty-wkt"],
        ["OSDU:4100", "--kind", "LBC"],
    ],
)
def test_contradictory_reference_options_are_usage_errors(
    referenced: Path, arguments: list[str], capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["references", str(referenced), *arguments]) == 2
    assert "error:" in capsys.readouterr().err


def test_references_of_a_missing_database_fail(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["references", str(tmp_path / "absent.db")]) == 1
    assert "does not exist" in capsys.readouterr().err


def test_inspect_counts_the_references(
    referenced: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["inspect", str(referenced)]) == 0
    assert json.loads(capsys.readouterr().out)["persistable_references_by_kind"] == {
        "LBC": 1
    }


def test_references_can_be_turned_off_on_the_command_line(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    record = geographic() | {"id": f"{{{{NAMESPACE}}}}:{_GEOGRAPHIC_ID}"}
    record["data"]["PersistableReference"] = _REFERENCE
    catalog = write_catalog(tmp_path / "referenced.json", record)
    output = tmp_path / "out" / "proj.db"
    arguments = ["build", str(catalog), "--output", str(output)]
    assert main([*arguments, "--no-persistable-references"]) == 0
    capsys.readouterr()
    assert main(["references", str(output)]) == 0
    captured = capsys.readouterr()
    assert json.loads(captured.out) == []
    assert "records no persistable references" in captured.err
