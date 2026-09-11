"""An explicit base is required when a PROJ search path is ambiguous."""

import os
from pathlib import Path

import pytest

from geodetic_engine.projdb.errors import ConfigurationError
from geodetic_engine.projdb.settings import check_build_target, default_base_proj_db


def test_search_path_finds_one_database(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "proj.db").touch()
    monkeypatch.setattr(
        "pyproj.datadir.get_data_dir",
        lambda: os.pathsep.join([str(tmp_path), str(tmp_path / "grids")]),
    )
    assert default_base_proj_db() == tmp_path / "proj.db"


def test_search_path_with_two_databases_requires_explicit_base(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    second = tmp_path / "second"
    second.mkdir()
    (tmp_path / "proj.db").touch()
    (second / "proj.db").touch()
    monkeypatch.setattr(
        "pyproj.datadir.get_data_dir",
        lambda: os.pathsep.join([str(tmp_path), str(second)]),
    )
    with pytest.raises(ConfigurationError, match="explicitly"):
        default_base_proj_db()


def test_hardlinked_base_cannot_be_output(tmp_path: Path) -> None:
    base = tmp_path / "base.db"
    base.touch()
    output = tmp_path / "output.db"
    output.hardlink_to(base)
    with pytest.raises(ConfigurationError):
        check_build_target(output, base)
