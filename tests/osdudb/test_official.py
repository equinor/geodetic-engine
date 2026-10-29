"""The installed official catalogue is used only with verified provenance."""

import hashlib
import json
from pathlib import Path

import pytest

from geodetic_engine.osdudb import official
from geodetic_engine.osdudb.errors import ConfigurationError


@pytest.fixture
def bundled(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    data = tmp_path / "_data"
    data.mkdir()
    monkeypatch.setattr(official, "files", lambda _package: tmp_path)
    return data


def test_official_catalogue_rejects_a_mismatched_digest(bundled: Path) -> None:
    catalog = bundled / official.DEFAULT_CATALOG_DESTINATION.name
    catalog.write_bytes(b"changed")
    official.provenance_path(catalog).write_text(json.dumps({"sha256": "0"}))
    with (
        pytest.raises(ConfigurationError, match="does not match"),
        official.official_catalog(),
    ):
        pass


def test_official_catalogue_reports_missing_provenance(bundled: Path) -> None:
    catalog = bundled / official.DEFAULT_CATALOG_DESTINATION.name
    catalog.write_bytes(b"catalogue")
    with (
        pytest.raises(ConfigurationError, match="provenance"),
        official.official_catalog(),
    ):
        pass


def test_official_catalogue_uses_its_digest_when_commit_is_absent(
    bundled: Path,
) -> None:
    catalog = bundled / official.DEFAULT_CATALOG_DESTINATION.name
    data = b"catalogue"
    catalog.write_bytes(data)
    digest = hashlib.sha256(data).hexdigest()
    official.provenance_path(catalog).write_text(json.dumps({"sha256": digest}))
    with official.official_catalog() as (path, version):
        assert path == catalog
        assert version == f"osdu/data/data-definitions@sha256:{digest}"


def test_official_catalogue_rejects_invalid_commit_metadata(bundled: Path) -> None:
    catalog = bundled / official.DEFAULT_CATALOG_DESTINATION.name
    data = b"catalogue"
    catalog.write_bytes(data)
    official.provenance_path(catalog).write_text(
        json.dumps({"sha256": hashlib.sha256(data).hexdigest(), "commit": 123})
    )
    with (
        pytest.raises(ConfigurationError, match="invalid commit"),
        official.official_catalog(),
    ):
        pass


def test_a_checkout_holding_only_the_generated_database_reads_the_committed_one(
    bundled: Path,
) -> None:
    """scripts/build_bundled_osdudb.py writes _data/proj_data/ into a checkout."""
    (bundled / "proj_data").mkdir()
    (bundled / "proj_data" / "proj.db").write_bytes(b"")
    with official.official_catalog() as (path, version):
        assert path == Path(official.__file__).resolve().parents[2] / (
            official.DEFAULT_CATALOG_DESTINATION
        )
        assert version.startswith("osdu/data/data-definitions@")


def test_missing_installed_catalogue_is_reported(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(official, "files", lambda _package: tmp_path / "missing")
    monkeypatch.setattr(
        official,
        "__file__",
        str(tmp_path / "installed/geodetic_engine/osdudb/official.py"),
    )
    with (
        pytest.raises(ConfigurationError, match="missing from this installation"),
        official.official_catalog(),
    ):
        pass
