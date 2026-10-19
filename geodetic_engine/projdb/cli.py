"""Source-independent command-line build and inspection helpers."""

from __future__ import annotations

import logging
import sqlite3
import sys
from collections.abc import Callable
from contextlib import closing
from pathlib import Path

from geodetic_engine.projdb.report import BuildReport, log_summary
from geodetic_engine.projdb.schema import IDENTIFIER_TABLE, PERSISTABLE_REFERENCE_TABLE
from geodetic_engine.projdb.settings import DatabaseSettings


def sidecar(output_db: Path, suffix: str, *, source: str | None = None) -> Path:
    """Return a sidecar path, optionally identifying the appending source."""
    tag = f".{source}" if source else ""
    return output_db.with_suffix(output_db.suffix + tag + suffix)


def inspect_database(database: Path) -> dict[str, object]:
    """Read a database's version metadata and authority inventory.

    The identifiers another scheme gives its objects, OSDU record ids for
    example, are counted by scheme, and the persistable references recorded
    against them by kind, when the database records any.
    """
    with closing(sqlite3.connect(f"file:{database}?mode=ro", uri=True)) as connection:
        metadata = dict(connection.execute("SELECT key, value FROM metadata"))
        authorities = {
            str(auth): int(count)
            for auth, count in connection.execute(
                "SELECT auth_name, COUNT(*) FROM crs_view "
                "GROUP BY auth_name ORDER BY auth_name"
            )
        }
        identifiers = _counts(
            connection,
            IDENTIFIER_TABLE,
            f"SELECT scheme, COUNT(*) FROM {IDENTIFIER_TABLE} "
            "GROUP BY scheme ORDER BY scheme",
        )
        references = _counts(
            connection,
            PERSISTABLE_REFERENCE_TABLE,
            f"SELECT kind, COUNT(*) FROM {PERSISTABLE_REFERENCE_TABLE} "
            "GROUP BY kind ORDER BY kind",
        )
    return {
        "database": str(database),
        "proj_version": metadata.get("PROJ.VERSION"),
        "epsg_version": metadata.get("EPSG.VERSION"),
        "crs_by_authority": authorities,
        "identifiers_by_scheme": identifiers,
        "persistable_references_by_kind": references,
    }


def _counts(
    connection: sqlite3.Connection, table: str, statement: str
) -> dict[str, int]:
    """Counts by key from a table this package adds, or none when it is absent."""
    if not connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?", (table,)
    ).fetchone():
        return {}
    return {str(key): int(count) for key, count in connection.execute(statement)}


def run_build(
    config: DatabaseSettings,
    builder: Callable[..., BuildReport],
    *,
    source: str,
    dry_run: bool,
    skip_validation: bool,
) -> int:
    """Run a source builder; the library owns validation and publication."""
    if dry_run:
        report = builder(config, dry_run=True, skip_validation=skip_validation)
        log_summary(report)
        print(report.to_json())
        print(f"dry run: nothing was published to {config.output_db}", file=sys.stderr)
        return 0
    log_path = sidecar(
        config.output_db, ".log", source=source if config.append else None
    )
    log_path.parent.mkdir(parents=True, exist_ok=True)
    handler = logging.FileHandler(log_path, mode="w", encoding="utf-8")
    handler.setFormatter(
        logging.Formatter("%(asctime)s %(levelname)-8s %(name)s: %(message)s")
    )
    handler.setLevel(logging.DEBUG)
    root = logging.getLogger()
    root.addHandler(handler)
    try:
        root.info("configuration: %r", config)
        report = builder(config, skip_validation=skip_validation)
        log_summary(report)
    finally:
        root.removeHandler(handler)
        handler.close()
    print(
        f"wrote {config.output_db} ({sum(report.rows_by_table.values())} rows), "
        f"report and {log_path}"
    )
    return 0
