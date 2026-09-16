"""Locating and reading the operator's settings file.

The intended split is that everything non-secret lives in a version controlled
config file and only credentials come from the environment. These tests pin the
parts of that contract which would otherwise fail silently.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from geodetic_engine.projdb.config import (
    DEFAULT_CONFIG_FILENAME,
    find_config_file,
    find_env_file,
    load_config,
)
from geodetic_engine.projdb.errors import ConfigurationError

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
TEMPLATE = REPOSITORY_ROOT / "geodetic-projdb.example.toml"

CREDENTIALS = {
    "GEODETIC_ENGINE_GEOREP_CLIENT_ID": "id",
    "GEODETIC_ENGINE_GEOREP_CLIENT_SECRET": "secret",
}

MINIMAL = """
[projdb]
api_url = "https://georepo.example.test"
authorities = ["Example"]
output_db = "build/proj.db"
"""


def _write(directory: Path, body: str, name: str = DEFAULT_CONFIG_FILENAME) -> Path:
    path = directory / name
    path.write_text(body, encoding="utf-8")
    return path


def test_default_file_is_found_in_the_working_directory(tmp_path: Path) -> None:
    """The operator edits one file and runs the command; no flag needed."""
    _write(Path.cwd(), MINIMAL)
    config = load_config(env=CREDENTIALS, load_dotenv_file=False)
    assert config.authorities == frozenset({"Example"})
    assert config.source_file is not None
    assert config.source_file.name == DEFAULT_CONFIG_FILENAME


def test_no_file_anywhere_is_not_an_error(tmp_path: Path) -> None:
    """Configuring entirely through the environment stays possible."""
    assert find_config_file(None, {}) is None


def test_config_file_can_be_named_by_environment(tmp_path: Path) -> None:
    path = _write(tmp_path, MINIMAL, name="elsewhere.toml")
    config = load_config(
        env={**CREDENTIALS, "GEODETIC_ENGINE_CONFIG": str(path)},
        load_dotenv_file=False,
    )
    assert config.source_file == path


def test_named_file_that_is_missing_is_an_error(tmp_path: Path) -> None:
    """Falling back silently would build with a different configuration."""
    missing = tmp_path / "absent.toml"
    with pytest.raises(ConfigurationError, match="does not exist"):
        load_config(config_file=missing, env=CREDENTIALS, load_dotenv_file=False)


def test_misspelled_setting_is_rejected(tmp_path: Path) -> None:
    """A silently ignored typo is a setting the operator believes is applied."""
    _write(Path.cwd(), MINIMAL + 'authoritys = ["Oops"]\n')
    with pytest.raises(ConfigurationError, match="unrecognised setting"):
        load_config(env=CREDENTIALS, load_dotenv_file=False)


def test_missing_table_is_rejected(tmp_path: Path) -> None:
    _write(Path.cwd(), 'api_url = "https://georepo.example.test"\n')
    with pytest.raises(ConfigurationError, match=r"no \[projdb\] table"):
        load_config(env=CREDENTIALS, load_dotenv_file=False)


def test_secrets_in_the_config_file_are_rejected(tmp_path: Path) -> None:
    _write(Path.cwd(), MINIMAL + 'client_secret = "nope"\n')
    with pytest.raises(ConfigurationError, match="never through a file"):
        load_config(env=CREDENTIALS, load_dotenv_file=False)


def test_environment_overrides_the_file(tmp_path: Path) -> None:
    _write(Path.cwd(), MINIMAL)
    config = load_config(
        env={**CREDENTIALS, "GEODETIC_ENGINE_AUTHORITIES": "FromEnv"},
        load_dotenv_file=False,
    )
    assert config.authorities == frozenset({"FromEnv"})


def test_shipped_template_is_valid_and_complete(tmp_path: Path) -> None:
    """The template an operator copies must load with only credentials added."""
    config = load_config(config_file=TEMPLATE, env=CREDENTIALS, load_dotenv_file=False)
    assert config.authorities == frozenset({"YourAuthority"})
    assert config.api_url == "https://georepository.example.com"
    assert config.output_db == Path("build/proj.db")
    # Commented-out settings must still resolve to the documented defaults.
    assert config.georepository.token_url == (
        "https://georepository.example.com/auth/connect/token"
    )
    assert config.authority_preference.value == "custom_first"
    assert config.include_deprecated is True


def test_env_file_is_found_from_the_working_directory(tmp_path: Path) -> None:
    """python-dotenv's default search starts at the installed package instead.

    Left at the default, an operator's .env next to their config file is never
    read and the build fails claiming the credentials are missing.
    """
    env_file = Path.cwd() / ".env"
    env_file.write_text("GEODETIC_ENGINE_GEOREP_CLIENT_ID=id\n", encoding="utf-8")
    assert find_env_file() == env_file


def test_template_supplies_no_credentials(tmp_path: Path) -> None:
    """Proven by the template failing to authenticate on its own."""
    with pytest.raises(ConfigurationError, match="client id and a client secret"):
        load_config(config_file=TEMPLATE, env={}, load_dotenv_file=False)


def test_api_url_with_api_path_is_normalised() -> None:
    """The client appends /api/v1 itself; a copied browser URL has it already."""
    from geodetic_engine.georepository.config import GeorepositoryConfig

    for given in (
        "https://example.test/api/v1",
        "https://example.test/api/v1/",
        "https://example.test/api",
    ):
        config = GeorepositoryConfig(
            api_url=given, client_id="id", client_secret="secret"
        )
        assert config.api_url == "https://example.test"
        assert config.endpoint("Datum") == "https://example.test/api/v1/Datum"
