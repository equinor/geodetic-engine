"""Command line entry point for the custom proj.db workflow.

Credentials are never accepted as arguments; they come from the environment or
a gitignored ``.env`` file, because arguments end up in shell history and in
process listings.
"""

from __future__ import annotations

import argparse
import json
import logging
import sqlite3
import sys
from collections.abc import Generator
from contextlib import contextmanager
from pathlib import Path

from geodetic_engine.errors import GeodeticEngineError
from geodetic_engine.projdb.build import build, log_summary
from geodetic_engine.projdb.config import find_env_file, load_config
from geodetic_engine.projdb.errors import ProjDbBuildError
from geodetic_engine.projdb.validate import validate

logger = logging.getLogger("geodetic_engine.projdb")

_FILE_FORMAT = "%(asctime)s %(levelname)-8s %(name)s: %(message)s"
_CONSOLE_FORMAT = "%(levelname)s %(name)s: %(message)s"


def sidecar(output_db: Path, suffix: str) -> Path:
    """Return a path next to the database, for example ``proj.db.log``."""
    return output_db.with_suffix(output_db.suffix + suffix)


@contextmanager
def _log_to_file(path: Path) -> Generator[None]:
    """Tee the run into a log file beside the database being built.

    The file records everything at DEBUG regardless of console verbosity, so a
    build that fails leaves a complete account of what it did next to the
    artefact it produced.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    handler = logging.FileHandler(path, mode="w", encoding="utf-8")
    handler.setFormatter(logging.Formatter(_FILE_FORMAT))
    handler.setLevel(logging.DEBUG)
    root = logging.getLogger()
    root.addHandler(handler)
    try:
        yield
    finally:
        root.removeHandler(handler)
        handler.close()


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="geodetic-projdb",
        description=(
            "Build a PROJ database enriched with a custom authority's CRSs and "
            "transformations, fetched from a Georepository instance."
        ),
    )
    parser.add_argument(
        "-v", "--verbose", action="store_true", help="log every imported object"
    )
    sub = parser.add_subparsers(dest="command", required=True)

    build_cmd = sub.add_parser("build", help="build an enriched proj.db")
    build_cmd.add_argument(
        "--config", type=Path, help="TOML file with a [projdb] table (no secrets)"
    )
    build_cmd.add_argument("--output", type=Path, help="path of the database to write")
    build_cmd.add_argument(
        "--skip-validation",
        action="store_true",
        help="write the database without checking that PROJ can read it back",
    )
    build_cmd.add_argument(
        "--dry-run",
        action="store_true",
        help=(
            "run the whole build and report what it would write, then discard "
            "it; nothing is left on disk"
        ),
    )

    validate_cmd = sub.add_parser("validate", help="validate an existing proj.db")
    validate_cmd.add_argument("database", type=Path)
    validate_cmd.add_argument(
        "--authority",
        action="append",
        required=True,
        dest="authorities",
        help="custom authority to check; repeatable",
    )

    inspect_cmd = sub.add_parser(
        "inspect", help="summarise what a built database contains"
    )
    inspect_cmd.add_argument("database", type=Path)

    config_cmd = sub.add_parser(
        "config", help="show the resolved settings and where they came from"
    )
    config_cmd.add_argument(
        "--config", type=Path, help="TOML file with a [projdb] table (no secrets)"
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    """Run the command line interface.

    Args:
        argv: Argument list, defaulting to :data:`sys.argv`.

    Returns:
        A process exit status: 0 on success, 1 on a build failure, 2 on a usage
        or configuration error.
    """
    args = _parser().parse_args(argv)
    console = logging.StreamHandler()
    console.setLevel(logging.DEBUG if args.verbose else logging.INFO)
    console.setFormatter(logging.Formatter(_CONSOLE_FORMAT))
    root = logging.getLogger()
    # Handlers filter; the root passes everything so the log file can be fuller
    # than the console.
    root.setLevel(logging.DEBUG)
    root.addHandler(console)
    if not args.verbose:
        logging.getLogger("httpx").setLevel(logging.WARNING)
        logging.getLogger("httpcore").setLevel(logging.WARNING)

    try:
        if args.command == "build":
            return _build(args)
        if args.command == "validate":
            summary = validate(args.database, authorities=args.authorities)
            print(json.dumps(summary, indent=2))
            return 0
        if args.command == "inspect":
            print(json.dumps(_inspect(args.database), indent=2))
            return 0
        if args.command == "config":
            print(json.dumps(_show_config(args.config), indent=2))
            return 0
    except GeodeticEngineError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 2


def _build(args: argparse.Namespace) -> int:
    overrides = {}
    if args.output is not None:
        overrides["output_db"] = args.output
    config = load_config(config_file=args.config, **overrides)

    if args.dry_run:
        # A dry run keeps nothing, so it leaves no log or report behind either.
        report = build(config, dry_run=True)
        log_summary(report)
        print(report.to_json())
        print(
            f"dry run: would write {sum(report.rows_by_table.values())} rows to "
            f"{config.output_db}; nothing was kept",
            file=sys.stderr,
        )
        return 0

    log_path = sidecar(config.output_db, ".log")
    report_path = sidecar(config.output_db, ".report.json")
    with _log_to_file(log_path):
        logger.info("geodetic-projdb build starting")
        logger.info("configuration: %r", config)
        if source := config.source_file:
            logger.info("settings read from %s", source)

        report = build(config)
        logger.info(
            "wrote %d rows across %d tables to %s",
            sum(report.rows_by_table.values()),
            len(report.rows_by_table),
            config.output_db,
        )

        failure: ProjDbBuildError | None = None
        if args.skip_validation:
            report.validation = {"status": "skipped"}
            logger.warning("validation skipped at the caller's request")
        else:
            try:
                report.validation = {
                    "status": "passed",
                    **validate(config.output_db, authorities=config.authorities),
                }
            except ProjDbBuildError as exc:
                # The database is on disk and the report explains why it is not
                # trustworthy; discarding both would lose the diagnosis.
                failure = exc
                report.validation = {"status": "failed", "error": str(exc)}
                logger.error("validation failed: %s", exc)

        report.write(report_path)
        logger.info("wrote build report to %s", report_path)
        # Last, so the end of the log is the whole picture including validation.
        log_summary(report)

    print(
        f"wrote {config.output_db} "
        f"({sum(report.rows_by_table.values())} rows), {report_path} and {log_path}"
    )
    if failure is not None:
        raise failure
    return 0


def _show_config(config_file: Path | None) -> dict[str, object]:
    """Resolve the configuration and describe it without revealing secrets."""
    resolved = load_config(config_file=config_file)
    env_file = find_env_file()
    return {
        "config_file": str(resolved.source_file) if resolved.source_file else None,
        "env_file": str(env_file) if env_file else None,
        "api_url": resolved.georepository.api_url,
        "token_url": resolved.georepository.token_url,
        "scope": resolved.georepository.scope,
        "credentials": "set" if resolved.georepository.client_secret else "missing",
        "authorities": sorted(resolved.authorities),
        "naming_systems": sorted(resolved.naming_systems),
        "output_db": str(resolved.output_db),
        "base_proj_db": str(resolved.base_proj_db),
        "include_deprecated": resolved.include_deprecated,
        "authority_preference": resolved.authority_preference.value,
        "fallback_authorities": list(resolved.fallback_authorities),
        "unsupported_method_codes": sorted(resolved.unsupported_method_codes),
        "page_size": resolved.georepository.page_size,
    }


def _inspect(database: Path) -> dict[str, object]:
    with sqlite3.connect(f"file:{database}?mode=ro", uri=True) as connection:
        metadata = dict(connection.execute("SELECT key, value FROM metadata"))
        authorities = {
            str(auth): int(count)
            for auth, count in connection.execute(
                "SELECT auth_name, COUNT(*) FROM crs_view GROUP BY auth_name "
                "ORDER BY auth_name"
            )
        }
    return {
        "database": str(database),
        "proj_version": metadata.get("PROJ.VERSION"),
        "epsg_version": metadata.get("EPSG.VERSION"),
        "crs_by_authority": authorities,
    }


if __name__ == "__main__":
    raise SystemExit(main())
