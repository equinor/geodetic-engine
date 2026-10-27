"""Prepare and validate native dependencies used by the pyproj wheel workflow."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sqlite3
import subprocess
from collections.abc import Mapping
from contextlib import closing
from pathlib import Path

_REQUIRED_INPUTS = (
    "RUNNER_OS",
    "RUNNER_ARCH",
    "ImageOS",
    "ImageVersion",
    "GITHUB_WORKSPACE",
    "RUNNER_TEMP",
    "NATIVE_ARCH",
    "NATIVE_CONFIG_HASH",
    "PYPROJ_REVISION",
    "PROJ_VERSION",
    "PROJ_SHA256",
)
_BUILD_ENVIRONMENT = (
    "PATH",
    "CC",
    "CXX",
    "AR",
    "AS",
    "LD",
    "RANLIB",
    "STRIP",
    "CFLAGS",
    "CXXFLAGS",
    "CPPFLAGS",
    "LDFLAGS",
    "LIBRARY_PATH",
    "LD_LIBRARY_PATH",
    "LD_RUN_PATH",
    "DYLD_LIBRARY_PATH",
    "DYLD_FALLBACK_LIBRARY_PATH",
    "PKG_CONFIG",
    "PKG_CONFIG_PATH",
    "SDKROOT",
    "DEVELOPER_DIR",
    "CMAKE_PREFIX_PATH",
    "CMAKE_GENERATOR",
    "CMAKE_GENERATOR_INSTANCE",
    "CMAKE_GENERATOR_PLATFORM",
    "CMAKE_OSX_SYSROOT",
    "CMAKE_TOOLCHAIN_FILE",
    "VSINSTALLDIR",
    "VCPKG_DEFAULT_TRIPLET",
    "VCPKG_OVERLAY_PORTS",
    "VCPKG_OVERLAY_TRIPLETS",
    "VCPKG_FEATURE_FLAGS",
)
_LIBRARIES = {
    "linux": "lib/libproj.so*",
    "macos": "lib/libproj*.dylib",
    "windows": "bin/proj*.dll",
}


def verify_installation(prefix: Path, version: str, platform: str) -> None:
    """Reject incomplete, corrupt, or mismatched native PROJ installations."""
    database = prefix / "share/proj/proj.db"
    if not database.is_file():
        raise RuntimeError(f"Native PROJ installation is missing {database}")
    try:
        with closing(
            sqlite3.connect(f"{database.resolve().as_uri()}?mode=ro", uri=True)
        ) as connection:
            versions = connection.execute(
                "SELECT value FROM metadata WHERE key = 'PROJ.VERSION'"
            ).fetchall()
    except sqlite3.Error as error:
        raise RuntimeError(
            f"Cannot read native PROJ database {database}: {error}"
        ) from error
    if versions != [(version,)]:
        raise RuntimeError(
            f"Native PROJ database {database} has PROJ.VERSION {versions!r}, "
            f"expected {version!r}"
        )
    header = prefix / "include/proj.h"
    if not header.is_file():
        raise RuntimeError(f"Native PROJ installation is missing {header}")
    if not any(path.is_file() for path in prefix.glob(_LIBRARIES[platform])):
        raise RuntimeError(
            f"Native PROJ installation {prefix} has no {_LIBRARIES[platform]}"
        )


def prepare_macos(
    prefix: Path, dependencies: Path, version: str, cache_hit: bool, script: Path
) -> None:
    """Build on a miss and verify both directories before building wheels."""
    if cache_hit:
        print("Using the exact macOS native dependency cache", flush=True)
    else:
        print("Building macOS native dependencies", flush=True)
        subprocess.run(["bash", "-e", str(script)], check=True)
    verify_installation(prefix, version, "macos")
    for path in (dependencies / "include", dependencies / "lib"):
        if not path.is_dir() or not any(path.iterdir()):
            raise RuntimeError(f"Native dependency installation is missing {path}")
    for pattern in (
        "lib/libsqlite3*.dylib",
        "lib/libtiff*.dylib",
        "lib/libcurl*.dylib",
        "lib/libnghttp2*.dylib",
    ):
        if not any(path.is_file() for path in dependencies.glob(pattern)):
            raise RuntimeError(
                f"Native dependency installation {dependencies} has no {pattern}"
            )


def restore_linux(
    source: Path, destination: Path, version: str, checksum: str, revision: str
) -> None:
    """Copy the matching image's native PROJ back to its original build prefix."""
    expected = f"{version}\n{checksum}\n{revision}\n"
    inputs = source / "build-inputs"
    if not inputs.is_file() or inputs.read_text(encoding="utf-8") != expected:
        raise RuntimeError(
            f"Linux native image has missing or mismatched inputs: {inputs}"
        )
    installation = source / "proj"
    verify_installation(installation, version, "linux")
    if destination.exists() and (
        not destination.is_dir() or any(destination.iterdir())
    ):
        raise RuntimeError(
            f"Refusing to overwrite existing native installation {destination}"
        )
    shutil.copytree(installation, destination, symlinks=True, dirs_exist_ok=True)
    verify_installation(destination, version, "linux")
    print(f"Restored verified Linux native PROJ to {destination}", flush=True)


def _tool_output(*command: str) -> str:
    output = subprocess.run(
        command, check=True, capture_output=True, text=True
    ).stdout.strip()
    if not output:
        raise ValueError(f"{command[0]} returned no version information")
    return output


def cache_key(environment: Mapping[str, str]) -> str:
    """Key host-native caches on source, paths, build settings and toolchains."""
    required = list(_REQUIRED_INPUTS)
    system = environment.get("RUNNER_OS")
    if system == "macOS":
        required.append("NATIVE_MACOS_TARGET")
    elif system == "Windows":
        required.extend(
            ("NATIVE_TRIPLET", "VCPKG_INSTALLATION_ROOT", "ProgramFiles(x86)")
        )
    else:
        raise ValueError(f"Unsupported host for native cache: {system!r}")

    missing = [name for name in required if not environment.get(name)]
    if missing:
        raise ValueError(f"missing cache inputs: {', '.join(missing)}")
    inputs = {name: environment[name] for name in required}
    inputs.update({name: environment.get(name, "") for name in _BUILD_ENVIRONMENT})
    inputs["cmake"] = _tool_output("cmake", "--version")
    if system == "macOS":
        inputs["xcode-select"] = _tool_output("xcode-select", "-p")
        inputs["xcodebuild"] = _tool_output("xcodebuild", "-version")
        inputs["sdk-path"] = _tool_output("xcrun", "--show-sdk-path")
        inputs["sdk-version"] = _tool_output("xcrun", "--show-sdk-version")
        inputs["clang"] = _tool_output("clang", "--version")
    else:
        root = Path(environment["VCPKG_INSTALLATION_ROOT"])
        inputs["vcpkg-revision"] = _tool_output(
            "git", "-C", str(root), "rev-parse", "HEAD"
        )
        inputs["vcpkg-version"] = _tool_output(str(root / "vcpkg.exe"), "version")
        vswhere = (
            Path(environment["ProgramFiles(x86)"])
            / "Microsoft Visual Studio/Installer/vswhere.exe"
        )
        visual_studio = _tool_output(
            str(vswhere),
            "-latest",
            "-products",
            "*",
            "-requires",
            "Microsoft.VisualStudio.Component.VC.Tools.x86.x64",
            "-format",
            "json",
        )
        if not json.loads(visual_studio):
            raise ValueError("vswhere found no C++ toolchain")
        inputs["visual-studio"] = visual_studio
    payload = json.dumps(inputs, sort_keys=True, separators=(",", ":")).encode()
    return f"pyproj-native-v1-{system}-{hashlib.sha256(payload).hexdigest()}"


def write_cache_outputs(environment: Mapping[str, str]) -> None:
    """Disable the optional cache explicitly if its identity cannot be verified."""
    output = Path(environment["GITHUB_OUTPUT"])
    try:
        key = cache_key(environment)
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        print(f"::warning::Native dependency cache disabled: {error}", flush=True)
        with output.open("a", encoding="utf-8") as file:
            print("enabled=false", file=file)
        return

    paths = ["pyproj-src/pyproj/proj_dir"]
    if environment["RUNNER_OS"] == "macOS":
        paths.append(str(Path(environment["RUNNER_TEMP"]) / "pyproj-deps"))
    with output.open("a", encoding="utf-8") as file:
        print("enabled=true", file=file)
        print(f"key={key}", file=file)
        print("paths<<NATIVE_CACHE_PATHS", file=file)
        print("\n".join(paths), file=file)
        print("NATIVE_CACHE_PATHS", file=file)
    print(f"Native dependency cache key: {key}", flush=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("key")
    commands.add_parser("prepare-macos")
    commands.add_parser("restore-linux")
    verify = commands.add_parser("verify")
    verify.add_argument("platform", choices=tuple(_LIBRARIES))
    verify.add_argument("prefix", type=Path)
    verify.add_argument("version")
    args = parser.parse_args()

    if args.command == "key":
        write_cache_outputs(os.environ)
    elif args.command == "prepare-macos":
        hit = os.environ.get("NATIVE_CACHE_HIT", "false")
        if hit not in ("true", "false"):
            raise ValueError(f"Invalid native cache hit value: {hit!r}")
        prepare_macos(
            Path(os.environ["PROJ_DIR"]),
            Path(os.environ["BUILD_PREFIX"]),
            os.environ["PROJ_VERSION"],
            hit == "true",
            Path("pyproj-src/ci/proj-compile-wheels.sh"),
        )
    elif args.command == "restore-linux":
        restore_linux(
            Path("/opt/pyproj-native"),
            Path(os.environ["PROJ_DIR"]),
            os.environ["PROJ_VERSION"],
            os.environ["PROJ_SHA256"],
            os.environ["PYPROJ_REVISION"],
        )
    else:
        verify_installation(args.prefix, args.version, args.platform)


if __name__ == "__main__":
    main()
