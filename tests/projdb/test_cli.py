"""Exercise the Georepository CLI without a network or real credentials."""

import json
import logging
from dataclasses import replace
from typing import Any

import pytest

from geodetic_engine.georepository.client import GeorepositoryClient
from geodetic_engine.projdb import __main__ as cli
from geodetic_engine.projdb.build import build
from geodetic_engine.projdb.config import ProjDbBuildConfig
from geodetic_engine.projdb.report import BuildReport
from tests.projdb.conftest import FakeGeorepository


@pytest.mark.parametrize(
    "flags",
    [[], ["--dry-run"], ["--skip-validation"], ["--append", "--overwrite-existing"]],
)
def test_cli_build_paths(
    config: ProjDbBuildConfig,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    flags: list[str],
) -> None:
    def load(**overrides: Any) -> ProjDbBuildConfig:
        overrides.pop("config_file", None)
        return replace(config, **overrides)

    def mocked_build(selected: ProjDbBuildConfig, **options: Any) -> BuildReport:
        with GeorepositoryClient(
            selected.georepository, transport=FakeGeorepository({}).transport()
        ) as client:
            return build(selected, client=client, **options)

    monkeypatch.setattr(cli, "load_config", load)
    monkeypatch.setattr(cli, "build", mocked_build)
    before = list(logging.getLogger().handlers)
    assert cli.main(["build", "--output", str(config.output_db), *flags]) == 0
    assert logging.getLogger().handlers == before
    if "--dry-run" in flags:
        assert not config.output_db.exists()
    else:
        capsys.readouterr()
        assert cli.main(["inspect", str(config.output_db)]) == 0
        assert "EPSG" in json.loads(capsys.readouterr().out)["crs_by_authority"]
        assert (
            cli.main(["validate", str(config.output_db), "--authority", "Example"]) == 0
        )


def test_cli_config_redacts_secrets(
    config: ProjDbBuildConfig,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(cli, "load_config", lambda **_: config)
    assert cli.main(["config"]) == 0
    output = capsys.readouterr().out
    assert config.georepository.client_secret not in output
    assert json.loads(output)["credentials"] == "set"
