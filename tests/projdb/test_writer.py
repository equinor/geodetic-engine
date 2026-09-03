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


def test_append_keeps_what_an_earlier_build_wrote(config: ProjDbBuildConfig) -> None:
    """A second source extends the first rather than starting over."""
    with ProjDbWriter(config) as first:
        first.insert("scope", [_SCOPE_ROW])
        first.commit()

    second = make_config(
        config.base_proj_db,
        config.output_db,
        authorities=frozenset({"Other"}),
        append=True,
    )
    with ProjDbWriter(second) as writer:
        assert writer.appended
        writer.insert("scope", [_SCOPE_ROW | {"auth_name": "Other", "code": "2"}])
        writer.commit()

    assert _scopes(config.output_db) == {("Example", "1"), ("Other", "2")}


def test_append_to_a_missing_output_copies_the_base(
    config: ProjDbBuildConfig,
) -> None:
    """The first build of a chain has nothing to append to and must not fail."""
    appending = make_config(config.base_proj_db, config.output_db, append=True)
    with ProjDbWriter(appending) as writer:
        assert not writer.appended
        writer.insert("scope", [_SCOPE_ROW])
        writer.commit()
    assert _scopes(config.output_db) == {("Example", "1")}


def test_failed_append_leaves_the_earlier_build_intact(
    config: ProjDbBuildConfig,
) -> None:
    """Deleting the output would destroy the build this one was extending."""
    with ProjDbWriter(config) as first:
        first.insert("scope", [_SCOPE_ROW])
        first.commit()

    appending = make_config(config.base_proj_db, config.output_db, append=True)
    with pytest.raises(ForeignAuthorityCollision), ProjDbWriter(appending) as writer:
        writer.insert("scope", [_SCOPE_ROW | {"code": "2"}])
        writer.insert("scope", [_SCOPE_ROW | {"auth_name": "EPSG"}])

    assert config.output_db.is_file()
    assert _scopes(config.output_db) == {("Example", "1")}


def test_overwrite_existing_replaces_only_its_own_authority(
    config: ProjDbBuildConfig,
) -> None:
    with ProjDbWriter(config) as first:
        first.insert("scope", [_SCOPE_ROW])
        first.commit()

    overwriting = make_config(
        config.base_proj_db, config.output_db, append=True, overwrite_existing=True
    )
    with ProjDbWriter(overwriting) as writer:
        writer.insert("scope", [_SCOPE_ROW | {"scope": "Rewritten"}])
        writer.commit()

    with sqlite3.connect(config.output_db) as connection:
        (scope,) = connection.execute(
            "SELECT scope FROM scope WHERE auth_name = 'Example' AND code = '1'"
        ).fetchone()
    assert scope == "Rewritten"


def test_overwrite_existing_still_cannot_reach_epsg(
    config: ProjDbBuildConfig,
) -> None:
    """The authority guard runs first, so replacement never escapes its own rows."""
    overwriting = make_config(
        config.base_proj_db, config.output_db, overwrite_existing=True
    )
    with (
        ProjDbWriter(overwriting) as writer,
        pytest.raises(ForeignAuthorityCollision, match="not one of the configured"),
    ):
        writer.insert("scope", [_SCOPE_ROW | {"auth_name": "EPSG", "code": "1026"}])


def _scopes(database: Path) -> set[tuple[str, str]]:
    """The test authorities' scope rows, as ``(auth_name, code)`` pairs.

    The stock proj.db ships scopes for IGNF, NKG and others; only the rows
    these tests write are of interest.
    """
    with sqlite3.connect(database) as connection:
        return {
            (str(auth), str(code))
            for auth, code in connection.execute(
                "SELECT auth_name, code FROM scope "
                "WHERE auth_name IN ('Example', 'Other')"
            )
        }


def test_schema_drift_is_detected(copy_of_proj_db: Path) -> None:
    """A PROJ upgrade that renames a column must fail loudly, not silently."""
    connection = sqlite3.connect(copy_of_proj_db)
    connection.execute("ALTER TABLE ellipsoid RENAME COLUMN inv_flattening TO flat")
    connection.commit()
    with pytest.raises(SchemaDriftError, match="inv_flattening"):
        verify_schema(connection)
    connection.close()
