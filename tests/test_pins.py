"""One file states the pinned PROJ, proj-data and pyproj releases.

Everything that can read ``.devcontainer/pins.env`` does, and every copy that
has to stay literal text is kept in line with it by ``scripts/sync_pins.py``.
"""

import os
import re
import shutil
import subprocess
import tomllib
from pathlib import Path

import pytest

from geodetic_engine import _environment, _pins
from scripts import sync_pins

REPO_ROOT = Path(__file__).resolve().parents[1]
PINS_FILE = REPO_ROOT / ".devcontainer" / "pins.env"
VALID = PINS_FILE.read_text(encoding="utf-8")
PINS = _pins.parse(VALID)


def _with(text: str, key: str, value: str) -> str:
    return "\n".join(
        f"{key}={value}" if line.startswith(f"{key}=") else line
        for line in text.splitlines()
    )


def test_every_copy_matches_the_pins() -> None:
    assert sync_pins.sync(write=False) == [], "run python scripts/sync_pins.py"


def test_the_package_reads_the_pins() -> None:
    assert PINS.proj_version == _environment.PINNED_PROJ_VERSION
    assert PINS.pyproj_version == _environment.PINNED_PYPROJ_VERSION
    assert PINS.pyproj_wheel_index == _environment.PYPROJ_WHEEL_INDEX
    assert (
        f"pyproj=={PINS.pyproj_version}+proj{PINS.proj_version}"
    ) == _environment.PYPROJ_WHEEL_REQUIREMENT


def test_an_installed_package_reads_the_copy_it_ships(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    shipped = tmp_path / "_pins.env"
    shipped.write_text(_with(VALID, "PROJ_VERSION", "9.9.1"), encoding="utf-8")
    monkeypatch.setattr(_pins, "PACKAGED", shipped)
    assert _pins.read().proj_version == "9.9.1"


def test_the_wheel_ships_the_pins() -> None:
    project = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text("utf-8"))
    included = project["tool"]["hatch"]["build"]["targets"]["wheel"]["force-include"]
    assert included[".devcontainer/pins.env"] == "geodetic_engine/_pins.env"


@pytest.mark.skipif(shutil.which("bash") is None, reason="needs bash")
def test_install_proj_reads_the_pins() -> None:
    script = REPO_ROOT / ".devcontainer" / "install-proj.sh"
    environment = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith(("PROJ_", "PINS_FILE"))
    }

    def printed(**overrides: str) -> dict[str, str]:
        result = subprocess.run(
            ["bash", str(script), "--print-pins"],
            env=environment | overrides,
            capture_output=True,
            text=True,
            check=True,
        )
        return dict(line.split("=", 1) for line in result.stdout.splitlines())

    assert printed() == {
        "PROJ_VERSION": PINS.proj_version,
        "PROJ_SHA256": PINS.proj_sha256,
        "PROJ_DATA_VERSION": PINS.proj_data_version,
        "PROJ_DATA_SHA256": PINS.proj_data_sha256,
    }
    assert printed(PROJ_VERSION="9.9.1")["PROJ_VERSION"] == "9.9.1"


@pytest.mark.parametrize(
    "relative",
    [
        ".devcontainer/install-proj.sh",
        ".devcontainer/Dockerfile",
        ".github/workflows/tests.yml",
        ".github/workflows/pypi_release.yml",
        ".github/workflows/pyproj-wheels.yml",
    ],
)
def test_no_build_input_states_a_pin_of_its_own(relative: str) -> None:
    text = (REPO_ROOT / relative).read_text(encoding="utf-8")
    for value in (
        PINS.pyproj_requirement,
        PINS.proj_version,
        PINS.proj_sha256,
        PINS.proj_data_sha256,
    ):
        assert value not in text


@pytest.mark.parametrize(
    ("text", "message"),
    [
        (VALID + "FOO=1\n", "expected KEY=VALUE"),
        (VALID + f"PROJ_VERSION={PINS.proj_version}\n", "PROJ_VERSION is set twice"),
        (_with(VALID, "PROJ_SHA256", "0" * 63), "PROJ_SHA256='0+' is malformed"),
        (_with(VALID, "PROJ_VERSION", '"9.9.0"'), "PROJ_VERSION='\"9.9.0\"'"),
        (_with(VALID, "PROJ_VERSION", "9.9"), "PROJ_VERSION='9.9' is malformed"),
        (
            VALID.replace(f"PROJ_VERSION={PINS.proj_version}", "PROJ_VERSION = 9.9.0"),
            "expected KEY=VALUE",
        ),
        (
            _with(VALID, "PYPROJ_WHEEL_INDEX", "http://example.org/simple/"),
            "PYPROJ_WHEEL_INDEX",
        ),
        (
            "\n".join(
                line
                for line in VALID.splitlines()
                if not line.startswith("PYPROJ_WHEEL_INDEX=")
            ),
            "does not set PYPROJ_WHEEL_INDEX",
        ),
    ],
)
def test_a_malformed_pins_file_is_refused(text: str, message: str) -> None:
    with pytest.raises(ValueError, match=message):
        _pins.parse(text)


def test_windows_line_endings_are_read_alike() -> None:
    assert _pins.parse(VALID.replace("\n", "\r\n")) == PINS


def test_a_pin_is_read_by_its_key() -> None:
    assert PINS.get("PROJ_VERSION") == PINS.proj_version
    assert PINS.get("PYPROJ_REQUIREMENT") == PINS.pyproj_requirement
    with pytest.raises(ValueError, match="unknown pin"):
        PINS.get("PROJ")


@pytest.fixture
def checkout(tmp_path: Path) -> Path:
    """The files sync_pins reads and rewrites, copied out of the repository."""
    for relative in {".devcontainer/pins.env", *(c.path for c in sync_pins.COPIES)}:
        target = tmp_path / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(REPO_ROOT / relative, target)
    return tmp_path


def test_sync_rewrites_exactly_the_stated_copies(checkout: Path) -> None:
    pins_file = checkout / ".devcontainer" / "pins.env"
    bumped = _with(_with(VALID, "PROJ_VERSION", "9.9.1"), "PYPROJ_VERSION", "3.8.1")
    pins_file.write_text(bumped, encoding="utf-8")
    before = {
        path: (checkout / path).read_text("utf-8")
        for path in {copy.path for copy in sync_pins.COPIES}
    }

    rewritten = sync_pins.sync(write=True, root=checkout)

    assert sorted(rewritten) == sorted(before)
    assert sync_pins.sync(write=False, root=checkout) == []
    for path, text in before.items():
        after = (checkout / path).read_text("utf-8")
        spots = sum(copy.path == path for copy in sync_pins.COPIES)
        changed = [
            new
            for old, new in zip(text.splitlines(), after.splitlines(), strict=True)
            if old != new
        ]
        assert 1 <= len(changed) <= spots, path
        assert all("9.9.1" in line or "3.8.1" in line for line in changed), path
    assert "pyproj==3.8.1+proj9.9.1" in (checkout / "README.md").read_text("utf-8")
    assert '"pyproj==3.8.1",' in (checkout / "pyproject.toml").read_text("utf-8")


def test_sync_refuses_a_copy_it_can_no_longer_find(checkout: Path) -> None:
    readme = checkout / "README.md"
    readme.write_text(
        readme.read_text("utf-8").replace(PINS.pyproj_requirement, "pyproj"),
        encoding="utf-8",
    )
    with pytest.raises(
        ValueError, match=re.escape("README.md: found the pyproj wheel 0 times")
    ):
        sync_pins.sync(write=False, root=checkout)
