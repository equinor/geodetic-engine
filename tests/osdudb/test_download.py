"""Updating the committed official OSDU catalogue from OSDU.

The server is replaced by an httpx mock transport, so these run offline. Each
failure must leave the committed copy as it was and nothing half-written beside
it.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import httpx
import pytest

from geodetic_engine.osdudb import (
    CatalogDownload,
    CatalogDownloadError,
    OsduCatalogError,
    download_official_catalog,
)
from geodetic_engine.osdudb import __main__ as cli
from geodetic_engine.osdudb.download import (
    DEFAULT_CATALOG_DESTINATION,
    official_catalog_url,
    provenance_path,
)

from .test_cli import geographic

MANIFEST = json.dumps(
    {"kind": "osdu:wks:Manifest:1.0.0", "ReferenceData": [geographic()]}
).encode()
DIGEST = hashlib.sha256(MANIFEST).hexdigest()
COMMIT = "0123456789abcdef0123456789abcdef01234567"


def _server(
    body: bytes = MANIFEST,
    *,
    status: int = 200,
    content_type: str = "text/plain; charset=utf-8",
    stated_sha256: str | None = DIGEST,
    seen: list[httpx.Request] | None = None,
) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        if seen is not None:
            seen.append(request)
        headers = {"content-type": content_type, "x-gitlab-last-commit-id": COMMIT}
        if stated_sha256 is not None:
            headers["x-gitlab-content-sha256"] = stated_sha256
        return httpx.Response(status, content=body, headers=headers)

    return httpx.MockTransport(handler)


def _unreachable() -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    return httpx.MockTransport(handler)


def _leftovers(directory: Path) -> list[str]:
    return sorted(
        path.name for path in directory.iterdir() if path.name.startswith(".")
    )


def test_the_newest_catalogue_is_downloaded_verified_and_recorded(
    tmp_path: Path,
) -> None:
    destination = tmp_path / "catalogs" / "CRS_CT.json"
    seen: list[httpx.Request] = []

    result = download_official_catalog(destination, transport=_server(seen=seen))

    assert result == CatalogDownload(
        destination, official_catalog_url(), "master", COMMIT, DIGEST, changed=True
    )
    assert destination.read_bytes() == MANIFEST
    assert seen[0].url.params["ref"] == "master"
    assert "/api/v4/" in str(seen[0].url)
    provenance = json.loads(provenance_path(destination).read_text())
    assert provenance["commit"] == COMMIT
    assert provenance["sha256"] == DIGEST
    assert provenance["license"] == "Apache-2.0"
    assert provenance["path"].endswith("LOCAL/CRS_CT.json")
    assert _leftovers(destination.parent) == []


def test_the_committed_catalogue_and_its_record_sit_side_by_side() -> None:
    assert DEFAULT_CATALOG_DESTINATION.parent == Path("catalogs/osdu")
    assert provenance_path(DEFAULT_CATALOG_DESTINATION).name == (
        "CRS_CT_osdu_official_crs_catalog.source.json"
    )


def test_a_copy_that_is_already_the_newest_is_left_untouched(
    tmp_path: Path,
) -> None:
    destination = tmp_path / "CRS_CT.json"
    destination.write_bytes(MANIFEST)
    provenance_path(destination).write_text('{"commit": "recorded earlier"}')

    result = download_official_catalog(destination, transport=_server())

    assert not result.changed
    assert result.commit == COMMIT
    assert destination.read_bytes() == MANIFEST
    assert provenance_path(destination).read_text() == '{"commit": "recorded earlier"}'
    assert _leftovers(tmp_path) == []


def test_an_expected_digest_is_enforced(tmp_path: Path) -> None:
    destination = tmp_path / "CRS_CT.json"
    destination.write_bytes(b"the committed copy")

    with pytest.raises(CatalogDownloadError, match="not the expected"):
        download_official_catalog(
            destination, expected_sha256="0" * 64, transport=_server()
        )

    assert destination.read_bytes() == b"the committed copy"
    assert not provenance_path(destination).exists()
    assert _leftovers(tmp_path) == []


def test_a_body_that_does_not_match_the_stated_digest_is_refused(
    tmp_path: Path,
) -> None:
    destination = tmp_path / "CRS_CT.json"
    with pytest.raises(CatalogDownloadError, match="server states"):
        download_official_catalog(
            destination, ref="master", transport=_server(stated_sha256="0" * 64)
        )
    assert not destination.exists()
    assert _leftovers(tmp_path) == []


@pytest.mark.parametrize(
    ("server", "message"),
    [
        (_server(b"<html>bot check</html>", content_type="text/html"), "web page"),
        (_server(b"not found", status=404), "HTTP 404"),
        (_server(b"busy", status=503), "HTTP 503"),
        (_unreachable(), "could not download"),
    ],
    ids=["bot-check-page", "unknown-revision", "server-error", "unreachable"],
)
def test_a_failed_download_says_why_and_writes_nothing(
    tmp_path: Path, server: httpx.MockTransport, message: str
) -> None:
    destination = tmp_path / "CRS_CT.json"
    with pytest.raises(CatalogDownloadError, match=message):
        download_official_catalog(destination, ref="master", transport=server)
    assert not destination.exists()
    assert not provenance_path(destination).exists()
    assert _leftovers(tmp_path) == []


def test_a_file_that_is_not_a_manifest_is_refused(tmp_path: Path) -> None:
    body = b'{"kind": "something else"}'
    destination = tmp_path / "CRS_CT.json"
    with pytest.raises(OsduCatalogError, match="not an OSDU manifest"):
        download_official_catalog(
            destination,
            ref="master",
            transport=_server(body, stated_sha256=hashlib.sha256(body).hexdigest()),
        )
    assert not destination.exists()
    assert _leftovers(tmp_path) == []


def test_the_command_prints_what_it_wrote(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    destination = tmp_path / "CRS_CT.json"

    def fake(path: Path, *, ref: str, timeout: float) -> CatalogDownload:
        return CatalogDownload(path, "https://example", ref, COMMIT, DIGEST, True)

    monkeypatch.setattr(cli, "download_official_catalog", fake)

    assert cli.main(["download", "--output", str(destination), "--ref", "v1"]) == 0
    printed = json.loads(capsys.readouterr().out)
    assert printed["path"] == str(destination)
    assert printed["ref"] == "v1"
    assert printed["commit"] == COMMIT
    assert printed["changed"] is True


def test_the_command_fails_with_the_reason(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def fake(path: Path, *, ref: str, timeout: float) -> CatalogDownload:
        raise CatalogDownloadError("the server is unreachable")

    monkeypatch.setattr(cli, "download_official_catalog", fake)

    assert cli.main(["download", "--output", str(tmp_path / "x.json")]) == 1
    assert "error: the server is unreachable" in capsys.readouterr().err
