"""The release database is built, and its wheel checked, apart from the caller.

scripts/build_bundled_osdudb.py builds the OSDU proj.db the wheel ships, and
scripts/check_installed_wheel.py checks the installed wheel, both in a new
environment. Whatever PROJ setup the caller has must not reach either: the
database would be built on, or the check answered by, something other than
what users install.
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

import pytest

from scripts import build_bundled_osdudb, check_installed_wheel


@pytest.mark.parametrize(
    "name",
    [
        "PROJ_DATA",
        "PROJ_LIB",
        # Attached to every proj.db PROJ opens, so its codes resolve as well.
        "PROJ_AUX_DB",
        # Read by pyproj on import, and lets PROJ fetch grids from the network.
        "PROJ_NETWORK",
        "PROJ_USER_WRITABLE_DIRECTORY",
        "PROJ_ONLY_BEST_DEFAULT",
        # The devcontainer's: pyproj built against its PROJ rather than a wheel.
        "PROJ_DIR",
        "PROJ_WHEEL",
        "GEODETIC_ENGINE_OSDU_DB",
        "PYTHONPATH",
        "VIRTUAL_ENV",
    ],
)
def test_nothing_that_steers_proj_or_the_package_reaches_the_build(
    monkeypatch: pytest.MonkeyPatch, name: str
) -> None:
    monkeypatch.setenv(name, "the caller's")
    assert name not in build_bundled_osdudb.clean_environment()


def test_what_pip_needs_still_reaches_the_build(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A proxy, an index or a certificate bundle is the network's, not PROJ's."""
    kept = {
        "HTTPS_PROXY": "http://proxy.example:3128",
        "PIP_INDEX_URL": "https://mirror.example/simple",
        "SSL_CERT_FILE": "ca.pem",
    }
    for name, value in kept.items():
        monkeypatch.setenv(name, value)
    environment = build_bundled_osdudb.clean_environment()
    assert environment.items() >= kept.items()
    assert environment["PIP_DISABLE_PIP_VERSION_CHECK"] == "1"


def test_the_callers_auxiliary_database_is_not_read(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """PROJ fails to open any database when its auxiliary one is missing."""
    monkeypatch.setenv("PROJ_AUX_DB", str(tmp_path / "missing.db"))
    result = subprocess.run(
        [sys.executable, "-c", "import pyproj; print(pyproj.CRS('EPSG:4326').name)"],
        env=build_bundled_osdudb.clean_environment(),
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "WGS 84"


def test_the_installed_wheel_is_checked_without_any_proj_setting(
    tmp_path: Path,
) -> None:
    """Refused before pyproj or the package is imported, as each reads them."""
    result = subprocess.run(
        [sys.executable, "-X", "importtime", check_installed_wheel.__file__, "--probe"],
        env=build_bundled_osdudb.clean_environment()
        | {"PROJ_AUX_DB": str(tmp_path / "missing.db")},
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 1
    assert "PROJ_AUX_DB is set; run this in a clean environment" in result.stderr
    imported = re.findall(r"\|\s*([\w.]+)$", result.stderr, re.MULTILINE)
    assert {"pyproj", "geodetic_engine"}.isdisjoint(
        name.partition(".")[0] for name in imported
    )
