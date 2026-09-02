"""The writer's guards against corrupting the official dataset."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from geodetic_engine.projdb.config import ProjDbBuildConfig
from geodetic_engine.projdb.errors import ForeignAuthorityCollision, SchemaDriftError
from geodetic_engine.projdb.schema import verify_schema
from geodetic_engine.projdb.writer import ProjDbWriter
from tests.projdb.conftest import make_config

_SCOPE_ROW = {"auth_name": "Example", "code": "1", "scope": "Testing", "deprecated": 0}


def test_base_database_is_never_modified(config: ProjDbBuildConfig) -> None:
    before = config.base_proj_db.stat().st_mtime_ns
    with ProjDbWriter(config) as writer:
        writer.insert("scope", [_SCOPE_ROW])
        writer.commit()
    assert config.base_proj_db.stat().st_mtime_ns == before
    assert config.output_db.is_file()


def test_foreign_authority_rows_are_refused(config: ProjDbBuildConfig) -> None:
    """A custom build must never write rows owned by EPSG or PROJ."""
    with (
        ProjDbWriter(config) as writer,
        pytest.raises(ForeignAuthorityCollision, match="not one of the configured"),
    ):
        writer.insert("scope", [_SCOPE_ROW | {"auth_name": "EPSG"}])


def test_existing_epsg_code_collides_rather_than_overwriting(
    config: ProjDbBuildConfig,
) -> None:
    """INSERT, not INSERT OR REPLACE: an existing definition is never replaced."""
    custom = make_config(
        config.base_proj_db, config.output_db, authorities=frozenset({"EPSG"})
    )
    with (
        ProjDbWriter(custom) as writer,
        pytest.raises(ForeignAuthorityCollision, match="could not insert"),
    ):
        writer.insert(
            "scope",
            [
                {
                    "auth_name": "EPSG",
                    "code": "1026",
                    "scope": "hijacked",
                    "deprecated": 0,
                }
            ],
        )


def test_failed_build_leaves_no_partial_database(config: ProjDbBuildConfig) -> None:
    """A half-enriched database is worse than none, because it still loads."""
    with pytest.raises(ForeignAuthorityCollision), ProjDbWriter(config) as writer:
        writer.insert("scope", [_SCOPE_ROW])
        writer.insert("scope", [_SCOPE_ROW | {"auth_name": "EPSG"}])
    assert not config.output_db.exists()


def test_uncommitted_build_is_discarded(config: ProjDbBuildConfig) -> None:
    with ProjDbWriter(config) as writer:
        writer.insert("scope", [_SCOPE_ROW])
    assert not config.output_db.exists()


def test_existing_keys_reads_the_base_database(config: ProjDbBuildConfig) -> None:
    with ProjDbWriter(config) as writer:
        keys = writer.existing_keys("geodetic_datum")
        writer.commit()
    assert ("EPSG", "6326") in keys


def test_unknown_table_is_rejected(config: ProjDbBuildConfig) -> None:
    with (
        ProjDbWriter(config) as writer,
        pytest.raises(KeyError, match="not a table this builder writes"),
    ):
        writer.insert("sqlite_master", [{}])


def test_schema_drift_is_detected(copy_of_proj_db: Path) -> None:
    """A PROJ upgrade that renames a column must fail loudly, not silently."""
    connection = sqlite3.connect(copy_of_proj_db)
    connection.execute("ALTER TABLE ellipsoid RENAME COLUMN inv_flattening TO flat")
    connection.commit()
    with pytest.raises(SchemaDriftError, match="inv_flattening"):
        verify_schema(connection)
    connection.close()
