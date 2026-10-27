"""Regression checks for the patches applied to upstream pyproj wheels."""

import hashlib
import shlex
import string
import subprocess
import sys
from pathlib import Path, PurePosixPath

import pytest

from scripts.patch_pyproj_build import _OPENSSL_CHECK_PATCHED

SCRIPT = Path(__file__).resolve().parents[1] / "scripts/patch_pyproj_build.py"

DATA_DIR_SOURCE = """\
    2. The internal proj directory (if exists & valid)
    3. The directory in PROJ_DATA (PROJ 9.1+) | PROJ_LIB (PROJ<9.1) (if exists & valid)

    if valid_data_dirs(_USER_PROJ_DATA):
        _VALIDATED_PROJ_DATA = _USER_PROJ_DATA
    elif valid_data_dir(internal_datadir):
        _VALIDATED_PROJ_DATA = str(internal_datadir)
    elif valid_data_dirs(proj_lib_dirs):
        _VALIDATED_PROJ_DATA = proj_lib_dirs
    elif valid_data_dir(prefix_datadir):
        _VALIDATED_PROJ_DATA = str(prefix_datadir)
"""


@pytest.fixture
def upstream(tmp_path: Path) -> Path:
    root = tmp_path / "pyproj-src"
    (root / "pyproj").mkdir(parents=True)
    (root / "ci").mkdir()
    (root / "pyproj/__init__.py").write_text(
        '__version__ = "3.8.0"\n', encoding="utf-8"
    )
    (root / "pyproj/datadir.py").write_text(DATA_DIR_SOURCE, encoding="utf-8")
    (root / "ci/proj-compile-wheels.sh").write_text(
        "OPENSSL_HASH=${OPENSSL_HASH:-6ae015467dabf0469b139ada93319327"
        "be24b98251ffaeceda0221848dc09262}\n"
        "    check_sha256sum $ARCHIVE_SDIR/${OPENSSL_ROOT}.tar.gz ${OPENSSL_HASH}\n"
        "        yum_install perl-core\n"
        '        flags="$flags --with-darwinssl"\n'
        "-DBUILD_TESTING:BOOL=OFF \\\n",
        encoding="utf-8",
    )
    return root


def _patch(root: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPT), str(root), "3.8.0", "9.9.0"],
        capture_output=True,
        text=True,
        check=False,
    )


def test_wheel_patches_keep_explicit_data_dir_first_and_honor_proj_data(
    upstream: Path,
) -> None:
    result = _patch(upstream)
    assert result.returncode == 0, result.stderr
    assert "+proj9.9.0" in (upstream / "pyproj/__init__.py").read_text()
    assert (
        "-DEMBED_RESOURCE_FILES=OFF"
        in (upstream / "ci/proj-compile-wheels.sh").read_text()
    )
    build_script = (upstream / "ci/proj-compile-wheels.sh").read_text()
    assert "83c7329fe52c850677d75e5d0b0ca245309b97e8ecbcfdc1dfdc4ab9fac35b39" in (
        build_script
    )
    assert 'cmake -E sha256sum "$archive"' in build_script
    assert "command -v perl >/dev/null" in build_script
    assert (
        'suppress build_openssl\n        flags="$flags --with-openssl=$BUILD_PREFIX"'
        in (build_script)
    )
    assert "--with-darwinssl" not in build_script

    data_dir = (upstream / "pyproj/datadir.py").read_text()
    assert data_dir.index("valid_data_dirs(_USER_PROJ_DATA)") < data_dir.index(
        "valid_data_dirs(proj_lib_dirs)"
    )
    assert data_dir.index("valid_data_dirs(proj_lib_dirs)") < data_dir.index(
        "valid_data_dir(internal_datadir)"
    )
    assert "os.pathsep.join((str(internal_datadir), proj_lib_dirs))" in data_dir
    assert "first PROJ_DATA/PROJ_LIB directory must contain proj.db" in data_dir


def test_wheel_patch_rejects_an_unexpected_upstream_layout(upstream: Path) -> None:
    (upstream / "pyproj/datadir.py").write_text("changed upstream\n")
    result = _patch(upstream)
    assert result.returncode != 0
    assert "expected" in result.stderr
    assert "datadir.py" in result.stderr


def test_wheel_patch_rejects_an_unexpected_macos_tls_configuration(
    upstream: Path,
) -> None:
    script = upstream / "ci/proj-compile-wheels.sh"
    script.write_text(
        script.read_text(encoding="utf-8").replace("--with-darwinssl", "--with-other"),
        encoding="utf-8",
    )
    result = _patch(upstream)
    assert result.returncode != 0
    assert "expected" in result.stderr
    assert "--with-darwinssl" in result.stderr


def test_wheel_workflow_runs_when_its_pin_or_patch_changes() -> None:
    workflow = SCRIPT.parents[1] / ".github/workflows/pyproj-wheels.yml"
    text = workflow.read_text(encoding="utf-8")
    assert "- pyproject.toml" in text
    assert "- scripts/patch_pyproj_build.py" in text
    assert "cp315-*" in text


@pytest.mark.parametrize(
    ("arch", "deployment_target"), [("arm64", "14.0"), ("x86_64", "15.0")]
)
def test_macos_build_uses_a_writable_prefix_and_links_its_libraries(
    arch: str, deployment_target: str
) -> None:
    workflow = SCRIPT.parents[1] / ".github/workflows/pyproj-wheels.yml"
    text = workflow.read_text(encoding="utf-8")
    block = text.split("CIBW_ENVIRONMENT_MACOS: >-", 1)[1].split(
        "CIBW_ENVIRONMENT_WINDOWS:", 1
    )[0]
    runner_temp = PurePosixPath("/Users/runner/work/_temp")
    workspace = PurePosixPath("/Users/runner/work/geodetic-engine/geodetic-engine")
    for template, value in {
        "${{ runner.temp }}": str(runner_temp),
        "${{ github.workspace }}": str(workspace),
        "${{ env.PROJ_VERSION }}": "9.9.0",
        "${{ matrix.arch }}": arch,
        "${{ matrix.macos-target }}": deployment_target,
        "${{ steps.native-cache.outputs.cache-hit || 'false' }}": "true",
    }.items():
        block = block.replace(template, value)

    environment = {
        "CPPFLAGS": "-DEXISTING_CPP",
        "LDFLAGS": "-Wl,-dead_strip",
        "PKG_CONFIG_PATH": "/existing/pkgconfig",
    }
    for assignment in shlex.split(block):
        name, _, value = assignment.partition("=")
        environment[name] = string.Template(value).substitute(environment)

    prefix = runner_temp / "pyproj-deps"
    assert environment["BUILD_PREFIX"] == str(prefix)
    assert environment["PROJ_DIR"] == str(workspace / "pyproj-src/pyproj/proj_dir")
    assert environment["CMAKE_OSX_ARCHITECTURES"] == arch
    assert environment["MACOSX_DEPLOYMENT_TARGET"] == deployment_target
    assert f"-I{prefix}/include" in environment["CPPFLAGS"].split()
    assert "-DEXISTING_CPP" in environment["CPPFLAGS"].split()
    assert f"-L{prefix}/lib" in environment["LDFLAGS"].split()
    assert f"-Wl,-rpath,{prefix}/lib" in environment["LDFLAGS"].split()
    assert (
        f"-Wl,-rpath,{workspace}/pyproj-src/pyproj/proj_dir/lib"
        in environment["LDFLAGS"].split()
    )
    assert "-Wl,-dead_strip" in environment["LDFLAGS"].split()
    assert environment["PKG_CONFIG_PATH"].split(":") == [
        str(prefix / "lib/pkgconfig"),
        "/existing/pkgconfig",
    ]


@pytest.mark.parametrize("valid", [True, False])
def test_openssl_checksum_check_rejects_a_modified_archive(
    tmp_path: Path, valid: bool
) -> None:
    directory = tmp_path / "archives with spaces"
    directory.mkdir()
    content = b"checked OpenSSL source"
    (directory / "openssl-3.2.1.tar.gz").write_bytes(content)
    digest = hashlib.sha256(content if valid else b"different").hexdigest()
    code = (
        "set -e\n"
        'ARCHIVE_SDIR="$1"\n'
        "OPENSSL_ROOT=openssl-3.2.1\n"
        f"OPENSSL_HASH={digest}\n"
        "verify() {\n"
        f"{_OPENSSL_CHECK_PATCHED}"
        "}\n"
        "verify\n"
    )
    result = subprocess.run(
        ["bash", "-c", code, "verify", str(directory)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert (result.returncode == 0) is valid
    if not valid:
        assert "SHA-256" in result.stderr
