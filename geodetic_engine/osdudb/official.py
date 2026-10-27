"""Access the official OSDU catalogue included with a release."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Generator
from contextlib import contextmanager
from importlib.resources import as_file, files
from pathlib import Path

from geodetic_engine.osdudb.download import (
    DEFAULT_CATALOG_DESTINATION,
    OFFICIAL_CATALOG_PROJECT,
    provenance_path,
)
from geodetic_engine.osdudb.errors import ConfigurationError


def _source_version(catalog: Path) -> str:
    provenance = provenance_path(catalog)
    try:
        metadata = json.loads(provenance.read_text(encoding="utf-8"))
        with catalog.open("rb") as stream:
            digest = hashlib.file_digest(stream, "sha256").hexdigest()
    except (OSError, ValueError) as error:
        raise ConfigurationError(
            f"cannot read the official OSDU catalogue or its provenance: {error}"
        ) from error
    if not isinstance(metadata, dict) or metadata.get("sha256") != digest:
        raise ConfigurationError(
            f"the official OSDU catalogue {catalog} does not match {provenance}"
        )
    commit = metadata.get("commit")
    if commit is not None and not isinstance(commit, str):
        raise ConfigurationError(f"invalid commit in {provenance}")
    return f"{OFFICIAL_CATALOG_PROJECT}@{commit or f'sha256:{digest}'}"


@contextmanager
def official_catalog() -> Generator[tuple[Path, str]]:
    """Yield the verified bundled catalogue and its recorded source version."""
    bundled = files("geodetic_engine.osdudb").joinpath("_data")
    if bundled.is_dir():
        with as_file(bundled) as directory:
            catalog = directory / DEFAULT_CATALOG_DESTINATION.name
            yield catalog, _source_version(catalog)
        return

    root = Path(__file__).resolve().parents[2]
    catalog = root / DEFAULT_CATALOG_DESTINATION
    if not (root / "pyproject.toml").is_file() or not catalog.is_file():
        raise ConfigurationError(
            "the official OSDU catalogue is missing from this installation"
        )
    yield catalog, _source_version(catalog)
