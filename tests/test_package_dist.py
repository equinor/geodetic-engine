"""Checks on resources required in the published project wheel."""

import hashlib
import json
import zipfile
from pathlib import Path

import pytest

from scripts.check_package_dist import _CATALOG, _PROVENANCE, _REQUIRED, check_wheel


def _wheel(
    path: Path,
    *,
    catalog: bytes,
    metadata: dict,
    missing: str = "",
    legacy_license_paths: bool = False,
) -> Path:
    with zipfile.ZipFile(path, "w") as archive:
        for name in _REQUIRED:
            if name == missing:
                continue
            content = (
                catalog
                if name == _CATALOG
                else json.dumps(metadata).encode()
                if name == _PROVENANCE
                else b""
            )
            archive.writestr(name, content)
        for name in ("LICENSE", "NOTICE"):
            if name != missing:
                directory = "pygeodetic_engine-0.1.0.dist-info"
                if not legacy_license_paths:
                    directory += "/licenses"
                archive.writestr(f"{directory}/{name}", "")
    return path


@pytest.mark.parametrize("legacy_license_paths", [False, True])
def test_release_wheel_contains_the_verified_catalogue(
    tmp_path: Path, legacy_license_paths: bool
) -> None:
    catalog = b"official catalogue"
    wheel = _wheel(
        tmp_path / "package.whl",
        catalog=catalog,
        metadata={"sha256": hashlib.sha256(catalog).hexdigest()},
        legacy_license_paths=legacy_license_paths,
    )
    check_wheel(wheel)


def test_release_wheel_rejects_missing_catalogue(tmp_path: Path) -> None:
    wheel = _wheel(tmp_path / "package.whl", catalog=b"", metadata={}, missing=_CATALOG)
    with pytest.raises(ValueError, match="exactly once"):
        check_wheel(wheel)


def test_release_wheel_rejects_modified_catalogue(tmp_path: Path) -> None:
    wheel = _wheel(
        tmp_path / "package.whl", catalog=b"changed", metadata={"sha256": "0"}
    )
    with pytest.raises(ValueError, match="does not match"):
        check_wheel(wheel)


def test_release_wheel_rejects_missing_attribution(tmp_path: Path) -> None:
    catalog = b"official catalogue"
    wheel = _wheel(
        tmp_path / "package.whl",
        catalog=catalog,
        metadata={"sha256": hashlib.sha256(catalog).hexdigest()},
        missing="NOTICE",
    )
    with pytest.raises(ValueError, match="NOTICE"):
        check_wheel(wheel)
