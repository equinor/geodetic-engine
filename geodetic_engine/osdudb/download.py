"""Update the official OSDU coordinate reference catalogue committed with the package.

OSDU publishes its CRSs and coordinate transformations as one manifest,
``CRS_CT.json``, in the OSDU Data Definitions repository on the Open Group's
community GitLab, under the Apache License 2.0. A copy is committed in this
repository, at ``DEFAULT_CATALOG_DESTINATION``, byte for byte as published,
so a build needs no network and is reproducible from the repository alone. This
module replaces that copy with a newer one, for the change to be reviewed and
committed.

The repository's web pages sit behind a bot check that a script cannot pass, so
the file is read through GitLab's REST API instead, which serves the same bytes
without one and states their SHA-256 in a header.

Beside the catalogue, a ``.source.json`` file records where it came from: the
project, the path, the commit that last changed it and its SHA-256, so a build
can record what it read.

Nothing is written unless the whole file arrived, matches the digest the server
states and reads as an OSDU manifest, so a failed download leaves the committed
copy as it was.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO, Final
from urllib.parse import quote

import httpx

from geodetic_engine.osdudb.catalog import OsduCatalog
from geodetic_engine.osdudb.errors import CatalogDownloadError

logger = logging.getLogger(__name__)

OFFICIAL_CATALOG_PROJECT: Final = "osdu/data/data-definitions"
"""The GitLab project that publishes the catalogue."""

OFFICIAL_CATALOG_PATH: Final = (
    "ReferenceValues/Manifests/reference-data/LOCAL/CRS_CT.json"
)
"""The catalogue's path in that project."""

LATEST: Final = "master"
"""The revision fetched by default: the newest the project publishes."""

DEFAULT_CATALOG_DESTINATION: Final = Path(
    "catalogs/osdu/CRS_CT_osdu_official_crs_catalog.json"
)
"""Where the official catalogue is committed, relative to the repository root."""

_HOST: Final = "https://community.opengroup.org"
_LICENSE: Final = "Apache-2.0"

# The catalogue is about 20 MB; anything far larger is not it.
_MAX_BYTES: Final = 256 * 1024 * 1024
_CHUNK_BYTES: Final = 1 << 16


@dataclass(frozen=True, slots=True)
class CatalogDownload:
    """The official OSDU catalogue, downloaded and verified.

    Attributes:
        path: Where the catalogue is.
        url: The API endpoint it was read from.
        ref: The revision that was asked for.
        commit: The commit that last changed the file at that revision, as the
            server reported it.
        sha256: Hex SHA-256 of the file.
        changed: Whether the file at ``path`` changed. False when the copy
            already there is the one downloaded.
    """

    path: Path
    url: str
    ref: str
    commit: str | None
    sha256: str
    changed: bool


def official_catalog_url() -> str:
    """The GitLab API endpoint that serves the raw catalogue."""
    project = quote(OFFICIAL_CATALOG_PROJECT, safe="")
    path = quote(OFFICIAL_CATALOG_PATH, safe="")
    return f"{_HOST}/api/v4/projects/{project}/repository/files/{path}/raw"


def provenance_path(catalog: Path) -> Path:
    """The ``.source.json`` file recording where a catalogue came from."""
    return catalog.with_name(f"{catalog.stem}.source.json")


def download_official_catalog(
    destination: Path = DEFAULT_CATALOG_DESTINATION,
    *,
    ref: str = LATEST,
    expected_sha256: str | None = None,
    timeout: float = 120.0,
    transport: httpx.BaseTransport | None = None,
) -> CatalogDownload:
    """Download the official OSDU catalogue and verify it.

    Args:
        destination: Where to write it. Replaced only once the new copy is
            verified, and left untouched when it already holds that copy.
        ref: The revision to fetch: a commit, branch or tag of
            ``OFFICIAL_CATALOG_PROJECT``. Defaults to the newest.
        expected_sha256: The digest the file must have, on top of the one the
            server states.
        timeout: Seconds to wait for the server, per network operation.
        transport: An httpx transport, for tests.

    Returns:
        What was downloaded, and whether it changed the copy at
        ``destination``.

    Raises:
        CatalogDownloadError: If the server cannot be reached, does not answer
            with the file, or the file does not match its digest.
        OsduCatalogError: If the file is not an OSDU manifest.

    Example:
        >>> download_official_catalog().changed  # doctest: +SKIP
        False
    """
    url = official_catalog_url()
    destination.parent.mkdir(parents=True, exist_ok=True)
    handle, name = tempfile.mkstemp(
        prefix=f".{destination.name}.", suffix=".part", dir=destination.parent
    )
    staged = Path(name)
    try:
        logger.info("downloading the OSDU catalogue at %s from %s", ref, url)
        with (
            os.fdopen(handle, "wb") as stream,
            httpx.Client(timeout=timeout, transport=transport) as client,
        ):
            sha256, headers = _fetch(client, url, ref, stream)

        stated = headers.get("x-gitlab-content-sha256")
        if stated is not None and stated.lower() != sha256:
            raise CatalogDownloadError(
                f"the OSDU catalogue downloaded from {url} has SHA-256 {sha256}, "
                f"but the server states {stated}; the download is incomplete or "
                "was altered on the way"
            )
        if expected_sha256 is not None and sha256 != expected_sha256.lower():
            raise CatalogDownloadError(
                f"the OSDU catalogue at {ref} has SHA-256 {sha256}, not the "
                f"expected {expected_sha256}; refusing it"
            )
        OsduCatalog.from_file(staged)
        changed = not destination.is_file() or _file_sha256(destination) != sha256
        if changed:
            # mkstemp's 0600 would hide a public file from other users.
            staged.chmod(0o644)
            os.replace(staged, destination)
    finally:
        staged.unlink(missing_ok=True)

    commit = headers.get("x-gitlab-last-commit-id") or headers.get("x-gitlab-commit-id")
    provenance = provenance_path(destination)
    if changed or not provenance.is_file():
        _write_provenance(provenance, url=url, ref=ref, commit=commit, sha256=sha256)
    logger.info(
        "%s the OSDU catalogue at %s in %s",
        "wrote" if changed else "already had",
        commit or ref,
        destination,
    )
    return CatalogDownload(destination, url, ref, commit, sha256, changed)


def _fetch(
    client: httpx.Client, url: str, ref: str, stream: BinaryIO
) -> tuple[str, httpx.Headers]:
    """Stream the file into ``stream``, returning its SHA-256 and the headers."""
    try:
        with client.stream("GET", url, params={"ref": ref}) as response:
            if response.status_code == 404:
                raise CatalogDownloadError(
                    f"{OFFICIAL_CATALOG_PROJECT} has no {OFFICIAL_CATALOG_PATH} at "
                    f"revision {ref!r} (HTTP 404)"
                )
            if response.status_code != 200:
                raise CatalogDownloadError(
                    f"{url} answered HTTP {response.status_code} for revision {ref!r}"
                )
            if "html" in response.headers.get("content-type", "").lower():
                raise CatalogDownloadError(
                    f"{url} answered with a web page rather than the catalogue; "
                    "a bot check, proxy or login page is in the way"
                )
            digest = hashlib.sha256()
            size = 0
            for chunk in response.iter_bytes(_CHUNK_BYTES):
                size += len(chunk)
                if size > _MAX_BYTES:
                    raise CatalogDownloadError(
                        f"{url} sent more than {_MAX_BYTES} bytes, far more than "
                        "the catalogue; refusing it"
                    )
                digest.update(chunk)
                stream.write(chunk)
            return digest.hexdigest(), response.headers
    except httpx.HTTPError as error:
        raise CatalogDownloadError(
            f"could not download the OSDU catalogue from {url}: {error}"
        ) from error


def _write_provenance(
    path: Path, *, url: str, ref: str, commit: str | None, sha256: str
) -> None:
    record = {
        "project": f"{_HOST}/{OFFICIAL_CATALOG_PROJECT}",
        "path": OFFICIAL_CATALOG_PATH,
        "license": _LICENSE,
        "ref": ref,
        "commit": commit,
        "sha256": sha256,
        "url": url,
    }
    path.write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(_CHUNK_BYTES), b""):
            digest.update(chunk)
    return digest.hexdigest()
