"""Guards that apply to both source and published pyproj installations."""

import subprocess
import sys
from pathlib import Path

from geodetic_engine._environment import (
    PINNED_PROJ_VERSION,
    PINNED_PYPROJ_VERSION,
    PYPROJ_WHEEL_INDEX,
    PYPROJ_WHEEL_REQUIREMENT,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
# A PROJ release other than the pinned one, whatever that is.
OTHER_PROJ = "0.0.1" if PINNED_PROJ_VERSION != "0.0.1" else "0.0.2"


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
    result = _import_with_pyproj(PINNED_PYPROJ_VERSION, PINNED_PROJ_VERSION)
    assert result.returncode == 0, result.stderr


def test_public_pyproj_with_another_proj_fails_clearly() -> None:
    result = _import_with_pyproj(PINNED_PYPROJ_VERSION, OTHER_PROJ)
    assert result.returncode != 0
    assert f"PROJ {PINNED_PROJ_VERSION}" in result.stderr
    assert OTHER_PROJ in result.stderr
    assert PYPROJ_WHEEL_REQUIREMENT in result.stderr
    assert PYPROJ_WHEEL_INDEX in result.stderr


def test_a_different_custom_proj_wheel_is_rejected() -> None:
    result = _import_with_pyproj(
        f"{PINNED_PYPROJ_VERSION}+proj{OTHER_PROJ}", OTHER_PROJ
    )
    assert result.returncode != 0
    assert OTHER_PROJ in result.stderr


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
