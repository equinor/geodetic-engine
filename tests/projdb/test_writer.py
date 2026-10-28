"""The writer's guards against corrupting the official dataset."""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import stat
from contextlib import closing
from pathlib import Path
from typing import Any

import pytest

from geodetic_engine.persistablereference import Kind
from geodetic_engine.projdb import writer as writer_module
from geodetic_engine.projdb.config import ProjDbBuildConfig
from geodetic_engine.projdb.errors import (
    ForeignAuthorityCollision,
    MissingReferencedObjectError,
    OutputWouldBeDiscarded,
    ProjDbBuildError,
    SchemaDriftError,
)
from geodetic_engine.projdb.schema import (
    BUILD_HISTORY_TABLE,
    IDENTIFIER_TABLE,
    LEGACY_IDENTIFIER_TABLE,
    PERSISTABLE_REFERENCE_KINDS,
    PERSISTABLE_REFERENCE_TABLE,
    verify_schema,
)
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


@pytest.mark.skipif(os.name != "posix", reason="requires POSIX permission bits")
@pytest.mark.parametrize("mode", [0o600, 0o640, 0o644, 0o660])
@pytest.mark.parametrize("append", [False, True])
def test_publication_preserves_existing_permissions(
    config: ProjDbBuildConfig, mode: int, append: bool
) -> None:
    with ProjDbWriter(config) as writer:
        writer.commit()
    config.output_db.chmod(mode)
    updated = make_config(config.base_proj_db, config.output_db, append=append)

    def check_staging(database: Path) -> None:
        assert stat.S_IMODE(database.stat().st_mode) == 0o600
        assert stat.S_IMODE(config.output_db.stat().st_mode) == mode

    with ProjDbWriter(updated) as writer:
        writer.insert("scope", [_SCOPE_ROW])
        writer.commit(validate=check_staging)

    assert stat.S_IMODE(config.output_db.stat().st_mode) == mode
    assert _scopes(config.output_db) == {("Example", "1")}


@pytest.mark.skipif(os.name != "posix", reason="requires POSIX permission bits")
def test_new_database_is_private_by_default(config: ProjDbBuildConfig) -> None:
    with ProjDbWriter(config) as writer:
        writer.commit()
    assert stat.S_IMODE(config.output_db.stat().st_mode) == 0o600


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


def test_dry_run_preserves_existing_output(config: ProjDbBuildConfig) -> None:
    with ProjDbWriter(config) as writer:
        writer.insert("scope", [_SCOPE_ROW])
        writer.commit()
    before = config.output_db.read_bytes()
    with ProjDbWriter(config) as writer:
        writer.insert("scope", [_SCOPE_ROW | {"code": "2"}])
    assert config.output_db.read_bytes() == before


def test_validation_failure_does_not_publish(config: ProjDbBuildConfig) -> None:
    with ProjDbWriter(config) as writer:
        writer.insert("scope", [_SCOPE_ROW])
        writer.commit()
    config.output_db.chmod(0o644)
    before_mode = stat.S_IMODE(config.output_db.stat().st_mode)
    before = config.output_db.read_bytes()

    def reject(database: Path) -> None:
        assert database != config.output_db
        raise ValueError("invalid staged database")

    with (
        pytest.raises(ValueError, match="invalid staged"),
        ProjDbWriter(config) as writer,
    ):
        writer.commit(validate=reject)
    assert config.output_db.read_bytes() == before
    assert stat.S_IMODE(config.output_db.stat().st_mode) == before_mode


def test_permission_failure_does_not_publish(
    config: ProjDbBuildConfig, monkeypatch: pytest.MonkeyPatch
) -> None:
    with ProjDbWriter(config) as writer:
        writer.commit()
    config.output_db.chmod(0o644)
    before = config.output_db.read_bytes()
    before_mode = stat.S_IMODE(config.output_db.stat().st_mode)

    def reject_permissions(database: Path, mode: int) -> None:
        assert database != config.output_db
        raise PermissionError("cannot set staging permissions")

    monkeypatch.setattr(Path, "chmod", reject_permissions)
    with (
        pytest.raises(PermissionError, match="staging permissions"),
        ProjDbWriter(config) as writer,
    ):
        writer.insert("scope", [_SCOPE_ROW])
        writer.commit()

    assert config.output_db.read_bytes() == before
    assert stat.S_IMODE(config.output_db.stat().st_mode) == before_mode
    assert not list(config.output_db.parent.glob("*.staging"))


def test_staging_is_synced_through_a_writable_handle(
    config: ProjDbBuildConfig, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Windows flushes only a handle opened for writing; read-only fails there."""
    events: list[str] = []
    real_open, real_fsync = Path.open, os.fsync

    def recording_open(path: Path, mode: str = "r", *args: Any, **kwargs: Any) -> Any:
        if path.suffix == ".staging":
            events.append(f"open {mode}")
        return real_open(path, mode, *args, **kwargs)

    def recording_fsync(descriptor: int) -> None:
        events.append("fsync")
        real_fsync(descriptor)

    monkeypatch.setattr(Path, "open", recording_open)
    monkeypatch.setattr(os, "fsync", recording_fsync)
    with ProjDbWriter(config) as writer:
        writer.commit()
    assert events[:2] == ["open r+b", "fsync"]


def test_publication_on_windows_does_not_sync_the_directory(
    config: ProjDbBuildConfig, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Windows cannot open a directory, so there is nothing to sync it through."""
    real_open = os.open

    def no_directories(path: Any, flags: int, *args: Any, **kwargs: Any) -> int:
        assert not Path(path).is_dir(), "a directory was opened"
        return real_open(path, flags, *args, **kwargs)

    monkeypatch.setattr(writer_module, "_WINDOWS", True)
    monkeypatch.setattr(os, "open", no_directories)
    with ProjDbWriter(config) as writer:
        writer.insert("scope", [_SCOPE_ROW])
        writer.commit()
    assert _scopes(config.output_db) == {("Example", "1")}


def test_a_database_in_use_on_windows_is_reported_and_kept(
    config: ProjDbBuildConfig, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Windows refuses to replace a file another process has open, such as PROJ."""
    with ProjDbWriter(config) as writer:
        writer.commit()
    before = config.output_db.read_bytes()

    def in_use(source: Any, destination: Any) -> None:
        raise PermissionError(13, "used by another process")

    monkeypatch.setattr(writer_module, "_WINDOWS", True)
    monkeypatch.setattr(os, "replace", in_use)
    with (
        pytest.raises(ProjDbBuildError, match="used by another process"),
        ProjDbWriter(config) as writer,
    ):
        writer.insert("scope", [_SCOPE_ROW])
        writer.commit()
    assert config.output_db.read_bytes() == before
    assert not list(config.output_db.parent.glob("*.staging"))


def test_a_read_only_staging_file_is_removed_on_windows(
    config: ProjDbBuildConfig, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Staging takes a read-only output's mode, and Windows will not delete it."""
    with ProjDbWriter(config) as writer:
        writer.commit()
    config.output_db.chmod(stat.S_IREAD)
    unlink = Path.unlink

    def windows_unlink(path: Path, missing_ok: bool = False) -> None:
        if path.exists() and not path.stat().st_mode & stat.S_IWRITE:
            raise PermissionError(13, "Access is denied", str(path))
        unlink(path, missing_ok=missing_ok)

    def refused(source: Any, destination: Any) -> None:
        raise PermissionError(13, "Access is denied")

    monkeypatch.setattr(writer_module, "_WINDOWS", True)
    monkeypatch.setattr(Path, "unlink", windows_unlink)
    monkeypatch.setattr(os, "replace", refused)
    with pytest.raises(ProjDbBuildError), ProjDbWriter(config) as writer:
        writer.insert("scope", [_SCOPE_ROW])
        writer.commit()
    assert not list(config.output_db.parent.glob("*.staging"))


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


def _record_build(database: Path, authorities: list[str]) -> None:
    """Give a database the build history a real build would have left."""
    with closing(sqlite3.connect(database)) as connection:
        connection.execute(
            f"CREATE TABLE IF NOT EXISTS {BUILD_HISTORY_TABLE} "
            "(sequence INTEGER PRIMARY KEY, report TEXT NOT NULL)"
        )
        connection.execute(
            f"INSERT INTO {BUILD_HISTORY_TABLE} (report) VALUES (?)",
            (json.dumps({"authorities": authorities}),),
        )
        connection.commit()


def test_fresh_build_refuses_to_discard_another_sources_import(
    config: ProjDbBuildConfig,
) -> None:
    """Both CLIs default to one path, so this is the easy way to lose a build."""
    with ProjDbWriter(config) as first:
        first.insert("scope", [_SCOPE_ROW])
        first.commit()
    _record_build(config.output_db, ["Example"])

    other = make_config(
        config.base_proj_db, config.output_db, authorities=frozenset({"OSDU"})
    )
    with pytest.raises(OutputWouldBeDiscarded, match="Example"):
        ProjDbWriter(other).open()

    assert _scopes(config.output_db) == {("Example", "1")}


def test_rebuilding_the_same_authority_is_allowed(config: ProjDbBuildConfig) -> None:
    """Refreshing a database from the source that wrote it is routine."""
    with ProjDbWriter(config) as first:
        first.insert("scope", [_SCOPE_ROW])
        first.commit()
    _record_build(config.output_db, ["Example"])

    with ProjDbWriter(make_config(config.base_proj_db, config.output_db)) as writer:
        writer.insert("scope", [_SCOPE_ROW | {"code": "2"}])
        writer.commit()

    assert _scopes(config.output_db) == {("Example", "2")}


def test_replace_discards_deliberately(config: ProjDbBuildConfig) -> None:
    with ProjDbWriter(config) as first:
        first.insert("scope", [_SCOPE_ROW])
        first.commit()
    _record_build(config.output_db, ["Example"])

    replacing = make_config(
        config.base_proj_db,
        config.output_db,
        authorities=frozenset({"OSDU"}),
        replace=True,
    )
    with ProjDbWriter(replacing) as writer:
        writer.commit()

    assert _scopes(config.output_db) == set()


def test_output_without_a_build_history_is_not_protected(
    config: ProjDbBuildConfig,
) -> None:
    """Nothing is known about a file this package did not write."""
    with ProjDbWriter(config) as first:
        first.insert("scope", [_SCOPE_ROW])
        first.commit()

    other = make_config(
        config.base_proj_db, config.output_db, authorities=frozenset({"OSDU"})
    )
    with ProjDbWriter(other) as writer:
        writer.commit()

    assert _scopes(config.output_db) == set()


def test_overwrite_rows_replaces_only_its_own_authority(
    config: ProjDbBuildConfig,
) -> None:
    with ProjDbWriter(config) as first:
        first.insert("scope", [_SCOPE_ROW])
        first.commit()

    overwriting = make_config(
        config.base_proj_db, config.output_db, append=True, overwrite_rows=True
    )
    with ProjDbWriter(overwriting) as writer:
        writer.insert("scope", [_SCOPE_ROW | {"scope": "Rewritten"}])
        writer.commit()

    with closing(sqlite3.connect(config.output_db)) as connection:
        (scope,) = connection.execute(
            "SELECT scope FROM scope WHERE auth_name = 'Example' AND code = '1'"
        ).fetchone()
    assert scope == "Rewritten"


def test_overwrite_rows_reports_what_it_replaced_and_leaves_the_rest(
    config: ProjDbBuildConfig,
) -> None:
    with ProjDbWriter(config) as first:
        first.insert("scope", [_SCOPE_ROW, _SCOPE_ROW | {"code": "2"}])
        first.commit()

    overwriting = make_config(
        config.base_proj_db, config.output_db, append=True, overwrite_rows=True
    )
    with ProjDbWriter(overwriting) as writer:
        writer.insert(
            "scope", [_SCOPE_ROW | {"scope": "Rewritten"}, _SCOPE_ROW | {"code": "2"}]
        )
        overrides = writer.overrides()
        writer.commit()

    assert overrides["objects"] == [
        {
            "table": "scope",
            "auth_name": "Example",
            "code": "1",
            "name": None,
            "previous_name": None,
            "changed": ["scope"],
        }
    ]
    assert overrides["unchanged"] == 1


def _coordinate_system(orientation: str) -> list[tuple[str, list[dict[str, Any]]]]:
    axes = [
        ("Easting", "E", "east", 1),
        ("Northing", "N", orientation, 2),
    ]
    return [
        (
            "coordinate_system",
            [
                {
                    "auth_name": "Example",
                    "code": "1",
                    "type": "Cartesian",
                    "dimension": 2,
                }
            ],
        ),
        (
            "axis",
            [
                {
                    "auth_name": "Example",
                    "code": str(order),
                    "name": name,
                    "abbrev": abbrev,
                    "orientation": direction,
                    "coordinate_system_auth_name": "Example",
                    "coordinate_system_code": "1",
                    "coordinate_system_order": order,
                    "uom_auth_name": "EPSG",
                    "uom_code": "9001",
                }
                for name, abbrev, direction, order in axes
            ],
        ),
    ]


@pytest.mark.parametrize(
    ("orientation", "replaced"), [("north", []), ("south", ["axes"])]
)
def test_a_coordinate_system_is_replaced_with_its_axes(
    config: ProjDbBuildConfig, orientation: str, replaced: list[str]
) -> None:
    with ProjDbWriter(config) as first:
        for table, rows in _coordinate_system("north"):
            first.insert(table, rows)
        first.commit()

    overwriting = make_config(
        config.base_proj_db, config.output_db, append=True, overwrite_rows=True
    )
    with ProjDbWriter(overwriting) as writer:
        for table, rows in _coordinate_system(orientation):
            writer.insert(table, rows)
        overrides = writer.overrides()
        writer.commit()

    assert [item["changed"] for item in overrides["objects"]] == (
        [replaced] if replaced else []
    )
    assert overrides["unchanged"] == (0 if replaced else 1)


def test_overwrite_rows_still_cannot_reach_epsg(
    config: ProjDbBuildConfig,
) -> None:
    """The authority guard runs first, so replacement never escapes its own rows."""
    overwriting = make_config(
        config.base_proj_db, config.output_db, overwrite_rows=True
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
    with closing(sqlite3.connect(database)) as connection:
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


def _identifier(code: str = "23031", **row: Any) -> dict[str, Any]:
    """An OSDU id recorded against an EPSG projected CRS PROJ ships."""
    return {
        "scheme": "OSDU",
        "identifier": "reference-data--CoordinateReferenceSystem:Projected:EPSG::23031",
        "table_name": "projected_crs",
        "auth_name": "EPSG",
        "code": code,
        "deprecated": 0,
        "source": "CRS_CT.json",
    } | row


def _identifiers(database: Path) -> list[tuple[Any, ...]]:
    with closing(sqlite3.connect(database)) as connection:
        return connection.execute(
            f"SELECT identifier, table_name, auth_name, code FROM {IDENTIFIER_TABLE}"
        ).fetchall()


def _identifier_aliases(database: Path) -> list[tuple[Any, ...]]:
    with closing(sqlite3.connect(database)) as connection:
        return connection.execute(
            "SELECT alt_name, table_name, auth_name, code FROM alias_name "
            "WHERE source = 'OSDU_ID'"
        ).fetchall()


def test_appending_migrates_the_legacy_identifier_table(
    config: ProjDbBuildConfig,
) -> None:
    with ProjDbWriter(config) as writer:
        writer.commit()

    with closing(sqlite3.connect(config.output_db)) as connection:
        connection.execute(
            f"""CREATE TABLE {LEGACY_IDENTIFIER_TABLE}(
                scheme TEXT NOT NULL,
                identifier TEXT NOT NULL,
                table_name TEXT NOT NULL,
                auth_name TEXT NOT NULL,
                code INTEGER_OR_TEXT NOT NULL,
                deprecated BOOLEAN NOT NULL,
                source TEXT,
                PRIMARY KEY (scheme, identifier)
            ) WITHOUT ROWID"""
        )
        connection.execute(
            f"CREATE INDEX idx_{LEGACY_IDENTIFIER_TABLE}_object "
            f"ON {LEGACY_IDENTIFIER_TABLE}(table_name, auth_name, code)"
        )
        connection.execute(
            f"""CREATE TRIGGER {LEGACY_IDENTIFIER_TABLE}_insert_trigger
                BEFORE INSERT ON {LEGACY_IDENTIFIER_TABLE}
                FOR EACH ROW BEGIN
                    SELECT RAISE(ABORT, 'invalid target')
                    WHERE NOT EXISTS (
                        SELECT 1 FROM object_view o
                        WHERE o.table_name = NEW.table_name
                            AND o.auth_name = NEW.auth_name
                            AND o.code = NEW.code
                    );
                END"""
        )
        connection.execute(
            f"INSERT INTO {LEGACY_IDENTIFIER_TABLE} VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                "OSDU",
                _identifier()["identifier"],
                "projected_crs",
                "EPSG",
                23031,
                0,
                "CRS_CT.json",
            ),
        )
        connection.commit()

    appending = make_config(config.base_proj_db, config.output_db, append=True)
    with ProjDbWriter(appending) as writer:
        assert writer.connection.execute(
            f"SELECT identifier, table_name, auth_name, code FROM {IDENTIFIER_TABLE}"
        ).fetchall() == [(_identifier()["identifier"], "projected_crs", "EPSG", 23031)]
        assert (
            writer.connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
                (LEGACY_IDENTIFIER_TABLE,),
            ).fetchone()
            is None
        )
        assert (
            writer.connection.execute(
                "SELECT 1 FROM sqlite_master WHERE name = ?",
                (f"idx_{IDENTIFIER_TABLE}_object",),
            ).fetchone()
            is not None
        )
        assert (
            writer.connection.execute(
                "SELECT 1 FROM sqlite_master WHERE name = ?",
                (f"{IDENTIFIER_TABLE}_insert_trigger",),
            ).fetchone()
            is not None
        )
        assert writer.write_identifiers([_identifier()]) == (0, 0)
        writer.commit()

    assert _identifiers(config.output_db) == [
        (_identifier()["identifier"], "projected_crs", "EPSG", 23031)
    ]


def test_an_identifier_may_name_another_authoritys_object(
    config: ProjDbBuildConfig,
) -> None:
    """It annotates the EPSG object rather than defining one, like an alias."""
    with ProjDbWriter(config) as writer:
        assert writer.write_identifiers([_identifier()], aliases=True) == (1, 1)
        writer.commit()
    identifier = _identifier()["identifier"]
    assert _identifiers(config.output_db) == [
        (identifier, "projected_crs", "EPSG", 23031)
    ]
    assert _identifier_aliases(config.output_db) == [
        (identifier, "projected_crs", "EPSG", 23031)
    ]


def test_aliases_are_only_written_when_asked_for(config: ProjDbBuildConfig) -> None:
    with ProjDbWriter(config) as writer:
        assert writer.write_identifiers([_identifier()]) == (1, 0)
        writer.commit()
    assert _identifier_aliases(config.output_db) == []


def test_an_identifier_for_an_absent_object_is_refused(
    config: ProjDbBuildConfig,
) -> None:
    with (
        ProjDbWriter(config) as writer,
        pytest.raises(MissingReferencedObjectError, match="does not define"),
    ):
        writer.write_identifiers([_identifier(code="99999999")])


def test_recording_an_identifier_again_changes_nothing(
    config: ProjDbBuildConfig,
) -> None:
    with ProjDbWriter(config) as writer:
        writer.write_identifiers([_identifier()], aliases=True)
        writer.commit()
    appending = make_config(config.base_proj_db, config.output_db, append=True)
    with ProjDbWriter(appending) as writer:
        assert writer.write_identifiers([_identifier()], aliases=True) == (0, 0)
        writer.commit()
    assert len(_identifiers(config.output_db)) == 1
    assert len(_identifier_aliases(config.output_db)) == 1


def test_an_identifier_is_not_silently_moved_to_another_object(
    config: ProjDbBuildConfig,
) -> None:
    with ProjDbWriter(config) as writer:
        writer.write_identifiers([_identifier()], aliases=True)
        writer.commit()
    appending = make_config(config.base_proj_db, config.output_db, append=True)
    with (
        ProjDbWriter(appending) as writer,
        pytest.raises(ForeignAuthorityCollision, match="already names"),
    ):
        writer.write_identifiers([_identifier(code="23032")], aliases=True)


def test_a_changed_inactive_flag_is_a_collision_too(
    config: ProjDbBuildConfig,
) -> None:
    with ProjDbWriter(config) as writer:
        writer.write_identifiers([_identifier()])
        writer.write_identifiers([_identifier()])
        with pytest.raises(ForeignAuthorityCollision, match="inactive"):
            writer.write_identifiers([_identifier(deprecated=1)])


def test_overwriting_moves_the_identifier_and_its_alias(
    config: ProjDbBuildConfig,
) -> None:
    with ProjDbWriter(config) as writer:
        writer.write_identifiers([_identifier()], aliases=True)
        writer.commit()
    overwriting = make_config(
        config.base_proj_db, config.output_db, append=True, overwrite_rows=True
    )
    with ProjDbWriter(overwriting) as writer:
        assert writer.write_identifiers([_identifier(code="23032")], aliases=True) == (
            1,
            1,
        )
        writer.commit()
    identifier = _identifier()["identifier"]
    assert _identifiers(config.output_db) == [
        (identifier, "projected_crs", "EPSG", 23032)
    ]
    assert _identifier_aliases(config.output_db) == [
        (identifier, "projected_crs", "EPSG", 23032)
    ]


_PAYLOAD = (
    '{"authCode":{"auth":"EPSG","code":"23031"},"name":"ED_1950_UTM_Zone_31N",'
    '"type":"LBC","ver":"PE_10_9_1","wkt":"PROJCS[\\"ED_1950_UTM_Zone_31N\\"]"}'
)


def _reference(payload: str = _PAYLOAD, **row: Any) -> dict[str, Any]:
    """The persistableReference of the record :func:`_identifier` records."""
    return {
        "scheme": "OSDU",
        "identifier": _identifier()["identifier"],
        "kind": "LBC",
        "name": "ED_1950_UTM_Zone_31N",
        "stated_auth_name": "EPSG",
        "stated_code": "23031",
        "version": "PE_10_9_1",
        "sha256": hashlib.sha256(payload.encode("utf-8")).hexdigest(),
        "persistable_reference": payload,
        "source": "CRS_CT.json",
    } | row


def _references(database: Path) -> list[tuple[Any, ...]]:
    with closing(sqlite3.connect(database)) as connection:
        return connection.execute(
            f"SELECT identifier, kind, persistable_reference "
            f"FROM {PERSISTABLE_REFERENCE_TABLE}"
        ).fetchall()


def test_the_reference_kinds_allowed_are_the_kinds_there_are() -> None:
    assert set(PERSISTABLE_REFERENCE_KINDS) == {kind.value for kind in Kind}


def test_a_reference_is_recorded_against_a_recorded_id(
    config: ProjDbBuildConfig,
) -> None:
    with ProjDbWriter(config) as writer:
        writer.write_identifiers([_identifier()])
        assert writer.write_persistable_references([_reference()]) == (1, 0)
        writer.commit()
    assert _references(config.output_db) == [
        (_identifier()["identifier"], "LBC", _PAYLOAD)
    ]


def test_a_reference_for_an_unrecorded_id_is_refused(
    config: ProjDbBuildConfig,
) -> None:
    """The foreign key ties every reference to an id, and so to an object."""
    with (
        ProjDbWriter(config) as writer,
        pytest.raises(MissingReferencedObjectError, match="does not record"),
    ):
        writer.write_persistable_references([_reference()])


def test_recording_a_reference_again_changes_nothing(
    config: ProjDbBuildConfig,
) -> None:
    with ProjDbWriter(config) as writer:
        writer.write_identifiers([_identifier()])
        writer.write_persistable_references([_reference()])
        writer.commit()
    appending = make_config(config.base_proj_db, config.output_db, append=True)
    with ProjDbWriter(appending) as writer:
        assert writer.write_persistable_references([_reference()]) == (0, 1)
        writer.commit()
    assert len(_references(config.output_db)) == 1


def test_a_reference_is_not_silently_replaced(config: ProjDbBuildConfig) -> None:
    with ProjDbWriter(config) as writer:
        writer.write_identifiers([_identifier()])
        writer.write_persistable_references([_reference()])
        changed = _PAYLOAD.replace("PE_10_9_1", "PE_10_10")
        with pytest.raises(ForeignAuthorityCollision, match="different"):
            writer.write_persistable_references([_reference(changed)])


def test_overwriting_replaces_the_reference(config: ProjDbBuildConfig) -> None:
    with ProjDbWriter(config) as writer:
        writer.write_identifiers([_identifier()])
        writer.write_persistable_references([_reference()])
        writer.commit()
    overwriting = make_config(
        config.base_proj_db, config.output_db, append=True, overwrite_rows=True
    )
    changed = _PAYLOAD.replace("PE_10_9_1", "PE_10_10")
    with ProjDbWriter(overwriting) as writer:
        assert writer.write_persistable_references([_reference(changed)]) == (1, 0)
        writer.commit()
    assert _references(config.output_db) == [
        (_identifier()["identifier"], "LBC", changed)
    ]


def test_moving_an_id_takes_its_reference_away_with_it(
    config: ProjDbBuildConfig,
) -> None:
    """A reference stated for one object must not be handed out for another."""
    with ProjDbWriter(config) as writer:
        writer.write_identifiers([_identifier()])
        writer.write_persistable_references([_reference()])
        writer.commit()
    overwriting = make_config(
        config.base_proj_db, config.output_db, append=True, overwrite_rows=True
    )
    with ProjDbWriter(overwriting) as writer:
        writer.write_identifiers([_identifier(code="23032")])
        writer.commit()
    assert _references(config.output_db) == []


def test_a_changed_inactive_flag_keeps_the_reference(
    config: ProjDbBuildConfig,
) -> None:
    """The id still names the same object, so its reference still belongs to it.

    Appended with references turned off, nothing would record it again.
    """
    with ProjDbWriter(config) as writer:
        writer.write_identifiers([_identifier()], aliases=True)
        writer.write_persistable_references([_reference()])
        writer.commit()
    overwriting = make_config(
        config.base_proj_db, config.output_db, append=True, overwrite_rows=True
    )
    with ProjDbWriter(overwriting) as writer:
        assert writer.write_identifiers([_identifier(deprecated=1)], aliases=True) == (
            1,
            0,
        )
        (moved,) = writer.overrides()["identifiers"]
        writer.commit()
    assert moved["now"].endswith("(inactive)")
    assert _references(config.output_db) == [
        (_identifier()["identifier"], "LBC", _PAYLOAD)
    ]
    with closing(sqlite3.connect(config.output_db)) as connection:
        assert connection.execute(
            f"SELECT deprecated FROM {IDENTIFIER_TABLE}"
        ).fetchall() == [(1,)]
    assert len(_identifier_aliases(config.output_db)) == 1


def test_a_reference_states_a_kind_there_is(config: ProjDbBuildConfig) -> None:
    with (
        ProjDbWriter(config) as writer,
        pytest.raises(MissingReferencedObjectError, match="CHECK"),
    ):
        writer.write_identifiers([_identifier()])
        writer.write_persistable_references([_reference(kind="XYZ")])
