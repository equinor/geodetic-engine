"""The release database is built, and its wheel checked, apart from the caller.

scripts/build_bundled_osdudb.py builds the OSDU proj.db the wheel ships, and
scripts/check_installed_wheel.py checks the installed wheel, both in a new
environment. Whatever PROJ setup the caller has must not reach either: the
database would be built on, or the check answered by, something other than
what users install. Nor does any secret of the caller's: they get only what
they need to run and to reach the network.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

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
    assert name not in build_bundled_osdudb.pip_environment()


@pytest.mark.parametrize(
    "name",
    [
        "TWINE_PASSWORD",
        "AWS_SECRET_ACCESS_KEY",
        "GITHUB_TOKEN",
        # Set for every step of a job that may request an OIDC token.
        "ACTIONS_ID_TOKEN_REQUEST_TOKEN",
        "UV_INDEX_ORGANISATION_PASSWORD",
    ],
)
def test_no_secret_of_the_callers_reaches_the_build(
    monkeypatch: pytest.MonkeyPatch, name: str
) -> None:
    """Only what a build needs is passed on, so nothing else can leak into it."""
    monkeypatch.setenv(name, "the caller's")
    assert name not in build_bundled_osdudb.clean_environment()
    assert name not in build_bundled_osdudb.pip_environment()


def test_what_the_network_needs_still_reaches_the_build(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A proxy or a certificate bundle is the network's, not PROJ's."""
    kept = {
        "HTTPS_PROXY": "http://proxy.example:3128",
        "NO_PROXY": "localhost",
        "SSL_CERT_FILE": "ca.pem",
        "REQUESTS_CA_BUNDLE": "ca.pem",
    }
    if os.name != "nt":  # Windows has one variable, whatever its case.
        kept["https_proxy"] = "http://proxy.example:3128"
    for name, value in kept.items():
        monkeypatch.setenv(name, value)
    for environment in (
        build_bundled_osdudb.clean_environment(),
        build_bundled_osdudb.pip_environment(),
    ):
        assert environment.items() >= kept.items()


def test_pips_own_settings_reach_pip_alone(monkeypatch: pytest.MonkeyPatch) -> None:
    """A mirror's URL may hold its token; no other process of a build needs it."""
    monkeypatch.setenv("PIP_INDEX_URL", "https://mirror.example/simple")
    environment = build_bundled_osdudb.pip_environment()
    assert environment["PIP_INDEX_URL"] == "https://mirror.example/simple"
    assert environment["PIP_DISABLE_PIP_VERSION_CHECK"] == "1"
    assert "PIP_INDEX_URL" not in build_bundled_osdudb.clean_environment()


def test_the_new_environment_is_made_with_nothing_more(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("TWINE_PASSWORD", "the caller's")
    monkeypatch.setenv("PIP_INDEX_URL", "https://mirror.example/simple")
    calls: list[tuple[list[str], dict[str, str]]] = []

    def run(command: list[str], *, env: dict[str, str], **_: Any) -> str:
        calls.append((command, env))
        return str(tmp_path)

    monkeypatch.setattr(build_bundled_osdudb, "run", run)
    monkeypatch.setattr(build_bundled_osdudb.venv, "create", lambda *_, **__: None)
    monkeypatch.setattr(
        build_bundled_osdudb, "stock_proj_db", lambda path: Path(path) / "proj.db"
    )
    pins = SimpleNamespace(
        pyproj_wheel_index="https://index.example/simple/",
        pyproj_requirement="pyproj==3.8.0+proj9.9.0",
    )
    python = build_bundled_osdudb.pinned_environment(tmp_path / "venv", pins, "it")
    build_bundled_osdudb.own_proj_db(python)
    (install, install_env), (_, probe_env) = calls
    assert install[1:4] == ["-m", "pip", "install"]
    assert install_env == build_bundled_osdudb.pip_environment()
    assert probe_env == build_bundled_osdudb.clean_environment()
    assert "TWINE_PASSWORD" not in install_env.keys() | probe_env.keys()


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
