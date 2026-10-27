"""Apply the patches required to build the project's pyproj wheels."""

from __future__ import annotations

import argparse
from pathlib import Path

_DATA_ORDER = """\
    2. The internal proj directory (if exists & valid)
    3. The directory in PROJ_DATA (PROJ 9.1+) | PROJ_LIB (PROJ<9.1) (if exists & valid)
"""
_DATA_ORDER_PATCHED = """\
    2. The directory in PROJ_DATA (PROJ 9.1+) | PROJ_LIB (PROJ<9.1) (if exists & valid)
    3. The internal proj directory (if exists & valid)
"""
_DATA_SELECTION = """\
    elif valid_data_dir(internal_datadir):
        _VALIDATED_PROJ_DATA = str(internal_datadir)
    elif valid_data_dirs(proj_lib_dirs):
        _VALIDATED_PROJ_DATA = proj_lib_dirs
"""
_DATA_SELECTION_PATCHED = """\
    elif valid_data_dirs(proj_lib_dirs):
        if not valid_data_dir(proj_lib_dirs.split(os.pathsep)[0]):
            raise DataDirError(
                "the first PROJ_DATA/PROJ_LIB directory must contain proj.db"
            )
        _VALIDATED_PROJ_DATA = proj_lib_dirs
    elif valid_data_dir(internal_datadir):
        _VALIDATED_PROJ_DATA = (
            os.pathsep.join((str(internal_datadir), proj_lib_dirs))
            if proj_lib_dirs
            else str(internal_datadir)
        )
"""
# pyproj 3.8.0's release script has a stale hash and calls an undefined checker.
_OPENSSL_HASH = "83c7329fe52c850677d75e5d0b0ca245309b97e8ecbcfdc1dfdc4ab9fac35b39"
_OPENSSL_CHECK = """\
    check_sha256sum $ARCHIVE_SDIR/${OPENSSL_ROOT}.tar.gz ${OPENSSL_HASH}
"""
_OPENSSL_CHECK_PATCHED = """\
    local archive="${ARCHIVE_SDIR:-archives}/${OPENSSL_ROOT}.tar.gz"
    local actual
    actual="$(cmake -E sha256sum "$archive" | cut -d' ' -f1)"
    if [ "$actual" != "$OPENSSL_HASH" ]; then
        echo "error: $archive has SHA-256 $actual, expected $OPENSSL_HASH" >&2
        return 1
    fi
"""


def _replace(source: Path, relative: str, old: str, new: str) -> None:
    path = source / relative
    text = path.read_text(encoding="utf-8")
    if text.count(old) != 1:
        raise SystemExit(f"{path}: expected {old!r} exactly once")
    path.write_text(text.replace(old, new), encoding="utf-8")


def patch_build(source: Path, pyproj_version: str, proj_version: str) -> None:
    """Patch one checked-out pyproj release, refusing an unexpected layout."""
    _replace(
        source,
        "pyproj/__init__.py",
        f'__version__ = "{pyproj_version}"',
        f'__version__ = "{pyproj_version}+proj{proj_version}"',
    )
    _replace(
        source,
        "ci/proj-compile-wheels.sh",
        "-DBUILD_TESTING:BOOL=OFF \\",
        "-DBUILD_TESTING:BOOL=OFF -DEMBED_RESOURCE_FILES=OFF \\",
    )
    _replace(
        source,
        "ci/proj-compile-wheels.sh",
        "OPENSSL_HASH=${OPENSSL_HASH:-6ae015467dabf0469b139ada93319327"
        "be24b98251ffaeceda0221848dc09262}",
        f"OPENSSL_HASH=${{OPENSSL_HASH:-{_OPENSSL_HASH}}}",
    )
    _replace(
        source, "ci/proj-compile-wheels.sh", _OPENSSL_CHECK, _OPENSSL_CHECK_PATCHED
    )
    # AlmaLinux installs perl-core as a group, not a package named perl-core.
    _replace(
        source,
        "ci/proj-compile-wheels.sh",
        "        yum_install perl-core",
        "        yum install -y perl-core && command -v perl >/dev/null",
    )
    _replace(source, "pyproj/datadir.py", _DATA_ORDER, _DATA_ORDER_PATCHED)
    _replace(source, "pyproj/datadir.py", _DATA_SELECTION, _DATA_SELECTION_PATCHED)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path, help="path to the pyproj checkout")
    parser.add_argument("pyproj_version", help="the pyproj release being built")
    parser.add_argument("proj_version", help="the PROJ release being bundled")
    args = parser.parse_args()
    patch_build(args.source, args.pyproj_version, args.proj_version)


if __name__ == "__main__":
    main()
