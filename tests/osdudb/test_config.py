"""Resolving the build configuration."""

from __future__ import annotations

from pathlib import Path

import pytest

from geodetic_engine.osdudb.config import (
    DEFAULT_AUTHORITY,
    DEFAULT_OUTPUT_DB,
    OsduBuildConfig,
    load_config,
)
from geodetic_engine.osdudb.errors import ConfigurationError
from geodetic_engine.projdb.settings import AuthorityPreference

from .conftest import write_catalog

CONFIG = "geodetic-osdudb.toml"


@pytest.fixture
def catalog(tmp_path: Path) -> Path:
    return write_catalog(tmp_path / "CRS_CT.json")


def load(catalog: Path, **overrides: object) -> OsduBuildConfig:
    """Load without reading the developer's own .env or config file."""
    return load_config(catalog=catalog, env={}, load_dotenv_file=False, **overrides)


class TestDefaults:
    def test_a_build_needs_nothing_but_the_catalogue(self, catalog: Path) -> None:
        config = load(catalog)
        assert config.catalog == catalog
        assert config.authorities == frozenset({DEFAULT_AUTHORITY})
        assert config.output_db == DEFAULT_OUTPUT_DB
        assert config.include_deprecated is True
        assert config.authority_preference is AuthorityPreference.CUSTOM_FIRST

    def test_the_base_database_defaults_to_the_installed_proj(
        self, catalog: Path, base_proj_db: Path
    ) -> None:
        assert load(catalog).base_proj_db == base_proj_db

    def test_naming_systems_default_to_the_authorities(self, catalog: Path) -> None:
        config = load(catalog, authorities=["OSDU", "EPSG"])
        assert config.naming_systems == frozenset({"OSDU", "EPSG"})

    def test_secrets_are_not_part_of_this_workflow(self, catalog: Path) -> None:
        # A catalogue is a file; there is nothing to authenticate against.
        assert not hasattr(load(catalog), "client_secret")


class TestValidation:
    def test_the_catalogue_is_required(self, tmp_path: Path) -> None:
        with pytest.raises(ConfigurationError, match="OSDU catalogue is required"):
            load_config(env={}, load_dotenv_file=False)

    def test_a_catalogue_that_does_not_exist_is_named(self, tmp_path: Path) -> None:
        with pytest.raises(ConfigurationError, match="does not exist"):
            load(tmp_path / "absent.json")

    def test_the_official_database_is_never_written_in_place(
        self, catalog: Path, base_proj_db: Path
    ) -> None:
        with pytest.raises(ConfigurationError, match="never modified in place"):
            OsduBuildConfig(
                catalog=catalog, output_db=base_proj_db, base_proj_db=base_proj_db
            )

    def test_at_least_one_authority_is_required(self, catalog: Path) -> None:
        with pytest.raises(ConfigurationError, match="at least one authority"):
            OsduBuildConfig(catalog=catalog, authorities=frozenset())

    def test_a_misspelled_preference_names_the_alternatives(
        self, catalog: Path
    ) -> None:
        with pytest.raises(ConfigurationError, match="custom_first"):
            load(catalog, authority_preference="custom-first")


class TestFile:
    def test_settings_are_read_from_the_working_directory(
        self, tmp_path: Path, catalog: Path
    ) -> None:
        Path(CONFIG).write_text(
            f'[osdudb]\ncatalog = "{catalog.as_posix()}"\n'
            'authorities = ["OSDU", "EPSG"]\n'
            'output_db = "out/proj.db"\n'
            'authority_preference = "custom_only"\n',
            encoding="utf-8",
        )
        config = load_config(env={}, load_dotenv_file=False)
        assert config.source_file == Path(CONFIG)
        assert config.authorities == frozenset({"OSDU", "EPSG"})
        assert config.output_db == Path("out/proj.db")
        assert config.authority_preference is AuthorityPreference.CUSTOM_ONLY

    def test_an_unrecognised_setting_is_an_error(self, catalog: Path) -> None:
        # A typo that is ignored is a setting the operator believes is applied.
        Path(CONFIG).write_text(
            f'[osdudb]\ncatalog = "{catalog.as_posix()}"\nauthorites = ["OSDU"]\n',
            encoding="utf-8",
        )
        with pytest.raises(ConfigurationError, match="authorites"):
            load_config(env={}, load_dotenv_file=False)

    def test_the_table_must_be_named(self, catalog: Path) -> None:
        Path(CONFIG).write_text(
            f'[projdb]\ncatalog = "{catalog.as_posix()}"\n', encoding="utf-8"
        )
        with pytest.raises(ConfigurationError, match=r"no \[osdudb\] table"):
            load_config(env={}, load_dotenv_file=False)

    def test_a_config_file_that_was_named_but_is_absent_is_an_error(
        self, tmp_path: Path
    ) -> None:
        with pytest.raises(ConfigurationError, match="does not exist"):
            load_config(
                config_file=tmp_path / "absent.toml", env={}, load_dotenv_file=False
            )


class TestPrecedence:
    def test_an_override_beats_the_environment_and_the_file(
        self, tmp_path: Path, catalog: Path
    ) -> None:
        Path(CONFIG).write_text(
            f'[osdudb]\ncatalog = "{catalog.as_posix()}"\noutput_db = "from-file.db"\n',
            encoding="utf-8",
        )
        env = {"GEODETIC_ENGINE_OUTPUT_DB": "from-env.db"}
        assert load_config(env=env, load_dotenv_file=False).output_db == Path(
            "from-env.db"
        )
        assert load_config(
            env=env, load_dotenv_file=False, output_db=Path("from-caller.db")
        ).output_db == Path("from-caller.db")

    def test_authorities_come_from_the_environment_as_a_list(
        self, catalog: Path
    ) -> None:
        config = load_config(
            catalog=catalog,
            env={"GEODETIC_ENGINE_AUTHORITIES": "OSDU, EPSG"},
            load_dotenv_file=False,
        )
        assert config.authorities == frozenset({"OSDU", "EPSG"})


def test_the_configuration_renders_readably(catalog: Path) -> None:
    assert "OsduBuildConfig(catalog=" in repr(load(catalog))
