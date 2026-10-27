"""Verify that a release wheel contains the official OSDU catalogue intact."""

from __future__ import annotations

import argparse
import hashlib
import json
import zipfile
from pathlib import Path

_DATA = "geodetic_engine/osdudb/_data/"
_CATALOG = _DATA + "CRS_CT_osdu_official_crs_catalog.json"
_PROVENANCE = _DATA + "CRS_CT_osdu_official_crs_catalog.source.json"
_REQUIRED = (
    "geodetic_engine/_environment.py",
    "geodetic_engine/osdudb/__main__.py",
    "geodetic_engine/osdudb/official.py",
    _CATALOG,
    _PROVENANCE,
)


def check_wheel(path: Path) -> None:
    """Reject missing, duplicated, or mismatched release resources."""
    with zipfile.ZipFile(path) as wheel:
        names = wheel.namelist()
        for name in _REQUIRED:
            if names.count(name) != 1:
                raise ValueError(f"{name} must appear exactly once")
        for name in ("LICENSE", "NOTICE"):
            matches = [
                entry
                for entry in names
                if entry.endswith((f".dist-info/licenses/{name}", f".dist-info/{name}"))
            ]
            if len(matches) != 1:
                raise ValueError(f"the wheel must include exactly one {name}")
        catalog = wheel.read(_CATALOG)
        metadata = json.loads(wheel.read(_PROVENANCE))
    digest = hashlib.sha256(catalog).hexdigest()
    if not isinstance(metadata, dict) or metadata.get("sha256") != digest:
        raise ValueError("the bundled OSDU catalogue does not match its provenance")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("wheel", type=Path, help="the built project wheel")
    args = parser.parse_args()
    try:
        check_wheel(args.wheel)
    except (OSError, ValueError, zipfile.BadZipFile) as error:
        raise SystemExit(f"{args.wheel}: {error}") from error
    print(f"{args.wheel}: bundled OSDU catalogue verified")


if __name__ == "__main__":
    main()
