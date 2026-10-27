"""Tests for native dependency reuse in the pyproj wheel workflow."""

import os
import sqlite3
import subprocess
import sys
from contextlib import closing
from pathlib import Path

import pytest

from scripts import pyproj_native_cache as native

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/pyproj_native_cache.py"


def _installation(prefix: Path, platform: str, version: str = "9.9.0") -> None:
    database = prefix / "share/proj/proj.db"
    database.parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(database)) as connection, connection:
        connection.execute("CREATE TABLE metadata (key TEXT, value TEXT)")
        connection.execute(
            "INSERT INTO metadata VALUES ('PROJ.VERSION', ?)", (version,)
        )
    header = prefix / "include/proj.h"
    header.parent.mkdir(parents=True, exist_ok=True)
    header.write_text("native header")
    library = (
        prefix
        / {
            "linux": "lib/libproj.so.25",
            "macos": "lib/libproj.25.dylib",
            "windows": "bin/proj_9.dll",
        }[platform]
    )
    library.parent.mkdir(parents=True, exist_ok=True)
    library.write_bytes(b"native library")


def _dependencies(prefix: Path) -> None:
    (prefix / "include").mkdir(parents=True, exist_ok=True)
    (prefix / "include/dependency").touch()
    (prefix / "lib").mkdir(parents=True, exist_ok=True)
    for library in ("sqlite3", "tiff", "curl", "nghttp2"):
        (prefix / f"lib/lib{library}.1.dylib").touch()


def _cache_environment(system: str) -> dict[str, str]:
    return {
        "RUNNER_OS": system,
        "RUNNER_ARCH": "ARM64" if system == "macOS" else "X64",
        "ImageOS": "hosted-image",
        "ImageVersion": "20261022.1",
        "GITHUB_WORKSPACE": "/runner/work/project",
        "RUNNER_TEMP": "/runner/work/_temp",
        "NATIVE_ARCH": "arm64" if system == "macOS" else "AMD64",
        "NATIVE_CONFIG_HASH": "workflow-and-scripts-hash",
        "PYPROJ_REVISION": "upstream-revision",
        "PROJ_VERSION": "9.9.0",
        "PROJ_SHA256": "source-checksum",
        "NATIVE_MACOS_TARGET": "14.0",
        "NATIVE_TRIPLET": "x64-windows",
        "VCPKG_INSTALLATION_ROOT": "C:/vcpkg",
        "ProgramFiles(x86)": "C:/Program Files (x86)",
        "PATH": "/runner/tools",
    }


def _tool_output(*command: str) -> str:
    if "vswhere.exe" in command[0]:
        return '[{"installationVersion": "17.0"}]'
    return " ".join(command)


@pytest.mark.parametrize("platform", ["linux", "macos", "windows"])
def test_valid_installations_are_accepted(tmp_path: Path, platform: str) -> None:
    _installation(tmp_path, platform)
    native.verify_installation(tmp_path, "9.9.0", platform)


@pytest.mark.parametrize("platform", ["linux", "macos", "windows"])
def test_missing_library_is_rejected(tmp_path: Path, platform: str) -> None:
    _installation(tmp_path, platform)
    for path in tmp_path.glob(native._LIBRARIES[platform]):
        path.unlink()
    with pytest.raises(RuntimeError, match="has no"):
        native.verify_installation(tmp_path, "9.9.0", platform)


def test_missing_corrupt_and_wrong_version_databases_are_rejected(
    tmp_path: Path,
) -> None:
    with pytest.raises(RuntimeError, match="missing"):
        native.verify_installation(tmp_path, "9.9.0", "linux")

    _installation(tmp_path, "linux", version="9.8.0")
    with pytest.raises(RuntimeError, match=r"9\.8\.0.*9\.9\.0"):
        native.verify_installation(tmp_path, "9.9.0", "linux")

    (tmp_path / "share/proj/proj.db").write_bytes(b"not a database")
    with pytest.raises(RuntimeError, match="Cannot read"):
        native.verify_installation(tmp_path, "9.9.0", "linux")


def test_missing_proj_header_is_rejected(tmp_path: Path) -> None:
    _installation(tmp_path, "linux")
    (tmp_path / "include/proj.h").unlink()
    with pytest.raises(RuntimeError, match=r"proj\.h"):
        native.verify_installation(tmp_path, "9.9.0", "linux")


def test_macos_cache_miss_builds_then_validates(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    prefix, dependencies = tmp_path / "proj", tmp_path / "deps"
    calls: list[list[str]] = []

    def build(command: list[str], *, check: bool) -> None:
        assert check
        calls.append(command)
        _installation(prefix, "macos")
        _dependencies(dependencies)

    monkeypatch.setattr(native.subprocess, "run", build)
    native.prepare_macos(prefix, dependencies, "9.9.0", False, Path("build.sh"))
    assert calls == [["bash", "-e", "build.sh"]]


@pytest.mark.parametrize("library", ["sqlite3", "tiff", "curl", "nghttp2"])
def test_macos_exact_hit_skips_compilation_but_checks_both_directories(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, library: str
) -> None:
    prefix, dependencies = tmp_path / "proj", tmp_path / "deps"
    _installation(prefix, "macos")
    _dependencies(dependencies)

    def no_build(*_args: object, **_kwargs: object) -> None:
        pytest.fail("a verified cache hit must not compile native dependencies")

    monkeypatch.setattr(native.subprocess, "run", no_build)
    native.prepare_macos(prefix, dependencies, "9.9.0", True, Path("build.sh"))
    (dependencies / f"lib/lib{library}.1.dylib").unlink()
    with pytest.raises(RuntimeError, match=f"lib{library}"):
        native.prepare_macos(prefix, dependencies, "9.9.0", True, Path("build.sh"))


def test_macos_cache_hit_does_not_hide_a_stale_proj_version(tmp_path: Path) -> None:
    _installation(tmp_path / "proj", "macos", version="9.8.0")
    _dependencies(tmp_path / "deps")
    with pytest.raises(RuntimeError, match=r"9\.8\.0.*9\.9\.0"):
        native.prepare_macos(
            tmp_path / "proj", tmp_path / "deps", "9.9.0", True, Path("build.sh")
        )


def test_linux_restores_only_matching_native_inputs_and_preserves_symlinks(
    tmp_path: Path,
) -> None:
    source, destination = tmp_path / "image", tmp_path / "project/proj_dir"
    _installation(source / "proj", "linux")
    (source / "proj/lib/libproj.so").symlink_to("libproj.so.25")
    (source / "build-inputs").write_text("9.9.0\nsha256\nrevision\n")

    native.restore_linux(source, destination, "9.9.0", "sha256", "revision")
    assert (destination / "lib/libproj.so").is_symlink()
    assert (destination / "lib/libproj.so").read_bytes() == b"native library"

    with pytest.raises(RuntimeError, match="overwrite"):
        native.restore_linux(source, destination, "9.9.0", "sha256", "revision")


@pytest.mark.parametrize(
    ("version", "checksum", "revision"),
    [
        ("9.8.0", "sha256", "revision"),
        ("9.9.0", "other-checksum", "revision"),
        ("9.9.0", "sha256", "other-revision"),
    ],
)
def test_linux_rejects_stale_native_image_inputs(
    tmp_path: Path, version: str, checksum: str, revision: str
) -> None:
    source = tmp_path / "image"
    _installation(source / "proj", "linux")
    (source / "build-inputs").write_text("9.9.0\nsha256\nrevision\n")
    with pytest.raises(RuntimeError, match="mismatched inputs"):
        native.restore_linux(source, tmp_path / "proj", version, checksum, revision)
    assert not (tmp_path / "proj").exists()


@pytest.mark.parametrize(
    "name",
    [
        "RUNNER_ARCH",
        "ImageOS",
        "ImageVersion",
        "GITHUB_WORKSPACE",
        "RUNNER_TEMP",
        "NATIVE_ARCH",
        "NATIVE_CONFIG_HASH",
        "NATIVE_MACOS_TARGET",
        "PYPROJ_REVISION",
        "PROJ_VERSION",
        "PROJ_SHA256",
        "PATH",
        "CFLAGS",
        "DYLD_LIBRARY_PATH",
        "CMAKE_GENERATOR_PLATFORM",
        "SDKROOT",
    ],
)
def test_native_key_changes_with_each_build_input(
    name: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(native, "_tool_output", _tool_output)
    environment = _cache_environment("macOS")
    original = native.cache_key(environment)
    environment[name] = environment.get(name, "") + "-changed"
    assert native.cache_key(environment) != original


@pytest.mark.parametrize("system", ["macOS", "Windows"])
def test_native_key_changes_with_toolchain(
    system: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    environment = _cache_environment(system)
    monkeypatch.setattr(native, "_tool_output", _tool_output)
    original = native.cache_key(environment)
    monkeypatch.setattr(
        native, "_tool_output", lambda *command: _tool_output(*command) + " "
    )
    assert native.cache_key(environment) != original


def test_windows_key_includes_vcpkg_and_visual_studio(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    environment = _cache_environment("Windows")
    calls: list[tuple[str, ...]] = []

    def record(*command: str) -> str:
        calls.append(command)
        return _tool_output(*command)

    monkeypatch.setattr(native, "_tool_output", record)
    original = native.cache_key(environment)
    assert any("vcpkg.exe" in command[0] for command in calls)
    assert any("vswhere.exe" in command[0] for command in calls)
    assert any(command[:2] == ("git", "-C") for command in calls)
    environment["NATIVE_TRIPLET"] = "arm64-windows"
    assert native.cache_key(environment) != original


def test_missing_identity_disables_cache_with_a_warning(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    environment = _cache_environment("macOS")
    environment["GITHUB_OUTPUT"] = str(tmp_path / "output")
    del environment["ImageVersion"]
    native.write_cache_outputs(environment)
    assert "Native dependency cache disabled" in capsys.readouterr().out
    assert "enabled=false" in (tmp_path / "output").read_text()


def test_missing_toolchain_disables_cache_with_a_warning(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    environment = _cache_environment("Windows")
    environment["GITHUB_OUTPUT"] = str(tmp_path / "output")

    def missing_tool(*_command: str) -> str:
        raise FileNotFoundError("missing toolchain")

    monkeypatch.setattr(native, "_tool_output", missing_tool)
    native.write_cache_outputs(environment)
    assert (
        "Native dependency cache disabled: missing toolchain" in capsys.readouterr().out
    )
    assert "enabled=false" in (tmp_path / "output").read_text()


@pytest.mark.parametrize("system", ["macOS", "Windows"])
def test_cache_outputs_include_only_native_paths(
    system: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(native, "_tool_output", _tool_output)
    environment = _cache_environment(system)
    environment["GITHUB_OUTPUT"] = str(tmp_path / "output")
    native.write_cache_outputs(environment)
    output = (tmp_path / "output").read_text()
    assert "enabled=true" in output
    assert "pyproj-src/pyproj/proj_dir" in output
    assert ("/runner/work/_temp/pyproj-deps" in output) is (system == "macOS")


def test_prepare_macos_rejects_invalid_cache_hit_value(tmp_path: Path) -> None:
    environment = os.environ | {"NATIVE_CACHE_HIT": "not-a-boolean"}
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "prepare-macos"],
        env=environment,
        capture_output=True,
        text=True,
        check=False,
        cwd=tmp_path,
    )
    assert result.returncode != 0
    assert "Invalid native cache hit value" in result.stderr


def test_workflow_builds_and_tests_wheels_on_cache_hits() -> None:
    text = (ROOT / ".github/workflows/pyproj-wheels.yml").read_text()
    assert "lookup-only: true" in text
    assert "steps.native-cache.outputs.cache-hit != 'true'" in text
    assert (
        "CIBW_BEFORE_ALL_LINUX: /usr/bin/python3 scripts/pyproj_native_cache.py restore-linux"
        in text
    )
    assert (
        "CIBW_BEFORE_ALL_MACOS: python scripts/pyproj_native_cache.py prepare-macos"
        in text
    )
    assert "CIBW_TEST_COMMAND: python {project}/scripts/check_pyproj_wheel.py" in text
    assert (
        text.index("name: Build and test the wheels")
        < text.index("name: Test Linux wheels in the clean manylinux image")
        < text.index("name: Save the manylinux native cache")
    )
    assert (
        text.index("name: Build and test the wheels")
        < text.index("name: Verify host native outputs before caching")
        < text.index("name: Save the host native cache")
    )
    assert "rebuild_native:" in text
    assert "no-cache: ${{ inputs.rebuild_native == true }}" in text
    assert "cache-from: ${{ inputs.rebuild_native != true" in text
    assert "steps.windows-image.outputs.version" in text
    assert "path: ${{ steps.windows-image.outputs.path }}" in text
    for step in (
        "Cache the vcpkg dependencies",
        "Look up a host native cache",
        "Save the manylinux native cache",
        "Save the host native cache",
    ):
        block = text.split(f"name: {step}", 1)[1].split("\n      - ", 1)[0]
        assert "inputs.rebuild_native != true" in block


def test_windows_native_paths_and_validation_are_not_skipped_on_cache_hits() -> None:
    text = (ROOT / ".github/workflows/pyproj-wheels.yml").read_text()
    paths = text.split("name: Set Windows native paths", 1)[1].split("\n      - ", 1)[0]
    verify = text.split("name: Verify the Windows native PROJ installation", 1)[
        1
    ].split("\n      - ", 1)[0]
    assert "if: runner.os == 'Windows'" in paths
    assert "cache-hit" not in paths
    assert "PROJ_PREFIX=" in paths
    assert "VCPKG_BIN=" in paths
    assert "if: runner.os == 'Windows'" in verify
    assert "cache-hit" not in verify
