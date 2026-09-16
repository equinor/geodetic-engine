"""Configuration loading, validation and secret handling."""

from __future__ import annotations

from pathlib import Path

import pytest

from geodetic_engine.projdb.config import (
    AuthorityPreference,
    ProjDbBuildConfig,
    load_config,
)
from geodetic_engine.projdb.errors import ConfigurationError
from tests.projdb.conftest import make_config, make_georepository_config


def test_authority_has_no_default(base_proj_db: Path, output_db: Path) -> None:
    """An organisation name must never be assumed."""
    with pytest.raises(ConfigurationError, match="at least one custom authority"):
        make_config(base_proj_db, output_db, authorities=frozenset())


def test_api_url_must_be_https(base_proj_db: Path, output_db: Path) -> None:
    """Client credentials are sent on every token request."""
    from geodetic_engine.georepository.errors import GeorepositoryConfigError

    with pytest.raises(GeorepositoryConfigError, match="https"):
        make_georepository_config(api_url="http://georepo.example.test")


def test_output_may_not_be_the_base_database(base_proj_db: Path) -> None:
    with pytest.raises(ConfigurationError, match="never modified in place"):
        make_config(base_proj_db, base_proj_db)


def test_repr_hides_credentials(config: ProjDbBuildConfig) -> None:
    rendered = repr(config)
    assert "test-secret" not in rendered
    assert "test-client" not in rendered
    assert "client_secret='***'" in rendered


def test_naming_systems_default_to_authorities(config: ProjDbBuildConfig) -> None:
    assert config.naming_systems == config.authorities


def test_deprecated_objects_are_included_by_default(
    config: ProjDbBuildConfig,
) -> None:
    """Needed so callers can say 'deprecated, superseded by X', not 'not found'."""
    assert config.include_deprecated is True


def test_config_file_may_not_carry_secrets(tmp_path: Path) -> None:
    config_file = tmp_path / "projdb.toml"
    config_file.write_text(
        '[projdb]\napi_url = "https://georepo.example.test"\n'
        'client_secret = "hunter2"\n',
        encoding="utf-8",
    )
    with pytest.raises(ConfigurationError, match=r"gitignored \.env"):
        load_config(config_file=config_file, env={}, load_dotenv_file=False)


def test_environment_overrides_file(tmp_path: Path, base_proj_db: Path) -> None:
    config_file = tmp_path / "projdb.toml"
    config_file.write_text(
        '[projdb]\napi_url = "https://from-file.example.test"\n'
        'authorities = ["FromFile"]\n',
        encoding="utf-8",
    )
    config = load_config(
        config_file=config_file,
        env={
            "GEODETIC_ENGINE_GEOREP_URL": "https://from-env.example.test",
            "GEODETIC_ENGINE_GEOREP_CLIENT_ID": "id",
            "GEODETIC_ENGINE_GEOREP_CLIENT_SECRET": "secret",
            "GEODETIC_ENGINE_AUTHORITIES": "FromEnv",
            "GEODETIC_ENGINE_OUTPUT_DB": str(tmp_path / "out.db"),
            "GEODETIC_ENGINE_BASE_PROJ_DB": str(base_proj_db),
        },
        load_dotenv_file=False,
    )
    assert config.api_url == "https://from-env.example.test"
    assert config.authorities == frozenset({"FromEnv"})


def test_token_url_defaults_below_the_api_host(
    tmp_path: Path, base_proj_db: Path
) -> None:
    config = load_config(
        env={
            "GEODETIC_ENGINE_GEOREP_URL": "https://georepo.example.test",
            "GEODETIC_ENGINE_GEOREP_CLIENT_ID": "id",
            "GEODETIC_ENGINE_GEOREP_CLIENT_SECRET": "secret",
            "GEODETIC_ENGINE_AUTHORITIES": "Example",
            "GEODETIC_ENGINE_OUTPUT_DB": str(tmp_path / "out.db"),
            "GEODETIC_ENGINE_BASE_PROJ_DB": str(base_proj_db),
        },
        load_dotenv_file=False,
    )
    assert config.georepository.token_url == (
        "https://georepo.example.test/auth/connect/token"
    )


def test_authority_preference_defaults_to_custom_first(
    config: ProjDbBuildConfig,
) -> None:
    assert config.authority_preference is AuthorityPreference.CUSTOM_FIRST


def test_authority_preference_rejects_unknown_mode(
    tmp_path: Path, base_proj_db: Path
) -> None:
    """A typo must not silently fall back to a different selection policy."""
    with pytest.raises(ConfigurationError, match="AUTHORITY_PREFERENCE"):
        load_config(
            env={
                "GEODETIC_ENGINE_GEOREP_URL": "https://georepo.example.test",
                "GEODETIC_ENGINE_GEOREP_CLIENT_ID": "id",
                "GEODETIC_ENGINE_GEOREP_CLIENT_SECRET": "secret",
                "GEODETIC_ENGINE_AUTHORITIES": "Example",
                "GEODETIC_ENGINE_OUTPUT_DB": str(tmp_path / "out.db"),
                "GEODETIC_ENGINE_BASE_PROJ_DB": str(base_proj_db),
                "GEODETIC_ENGINE_AUTHORITY_PREFERENCE": "custom-frist",
            },
            load_dotenv_file=False,
        )


def test_endpoint_url_includes_api_v1(config: ProjDbBuildConfig) -> None:
    assert config.endpoint("Transformation").endswith("/api/v1/Transformation")
