"""Guards that apply to both source and published pyproj installations."""

import re
import subprocess
import sys
import tomllib
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]


def _import_with_pyproj(version: str, proj: str) -> subprocess.CompletedProcess[str]:
    code = (
        "import pyproj; "
        f"pyproj.__version__ = {version!r}; "
        f"pyproj.proj_version_str = {proj!r}; "
        "import geodetic_engine"
    )
    return subprocess.run(
        [sys.executable, "-c", code],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )


def test_source_built_pyproj_with_the_pinned_proj_is_accepted() -> None:
    result = _import_with_pyproj("3.8.0", "9.9.0")
    assert result.returncode == 0, result.stderr


def test_public_pyproj_with_another_proj_fails_clearly() -> None:
    result = _import_with_pyproj("3.8.0", "9.8.1")
    assert result.returncode != 0
    assert "PROJ 9.9.0" in result.stderr
    assert "9.8.1" in result.stderr
    assert "pyproj==3.8.0+proj9.9.0" in result.stderr
    assert "https://equinor.github.io/geodetic-engine/simple/" in result.stderr


def test_a_different_custom_proj_wheel_is_rejected() -> None:
    result = _import_with_pyproj("3.8.0+proj9.10.0", "9.10.0")
    assert result.returncode != 0
    assert "9.10.0" in result.stderr


def test_runtime_pins_match_the_build_inputs() -> None:
    from geodetic_engine._environment import PINNED_PROJ_VERSION, PINNED_PYPROJ_VERSION

    script = (REPO_ROOT / ".devcontainer/install-proj.sh").read_text(encoding="utf-8")
    match = re.search(r'^PROJ_VERSION="\$\{PROJ_VERSION:-([^}]+)}"', script, re.M)
    assert match is not None
    assert match.group(1) == PINNED_PROJ_VERSION

    project = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    assert f"pyproj=={PINNED_PYPROJ_VERSION}" in project["project"]["dependencies"]


@pytest.mark.parametrize(
    "relative",
    [
        "README.md",
        "docs/getting-started/installation.md",
        "docs/user-guide/welltrajectory/plotting.md",
        "geodetic_engine/welltrajectory/README.md",
        "examples/welltrajectory_examples.ipynb",
        ".github/workflows/pyproj-wheels.yml",
        ".github/workflows/tests.yml",
    ],
)
def test_install_examples_select_the_expected_wheel(relative: str) -> None:
    from geodetic_engine._environment import (
        PYPROJ_WHEEL_INDEX,
        PYPROJ_WHEEL_REQUIREMENT,
    )

    text = (REPO_ROOT / relative).read_text(encoding="utf-8")
    assert PYPROJ_WHEEL_REQUIREMENT in text
    assert PYPROJ_WHEEL_INDEX in text


def test_missing_plotly_error_preserves_the_verified_pyproj_installation() -> None:
    code = (
        "import sys; sys.modules['plotly'] = None; "
        "from geodetic_engine.welltrajectory.plot import _plotly; _plotly()"
    )
    result = subprocess.run(
        [sys.executable, "-c", code],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode != 0
    assert "pygeodetic-engine[plot]" in result.stderr
    assert "https://equinor.github.io/geodetic-engine/simple/" in result.stderr
    assert "pyproj==" not in result.stderr
