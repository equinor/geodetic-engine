"""Command line entry point for building a proj.db from an OSDU catalogue.

A build needs nothing but the catalogue file, so the common case is::

    geodetic-osdudb build CRS_CT.json

Everything else has a default or lives in an optional ``geodetic-osdudb.toml``.
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
from typing import Any

from geodetic_engine.errors import GeodeticEngineError
from geodetic_engine.osdudb.build import build, log_summary
from geodetic_engine.osdudb.config import load_config
from geodetic_engine.osdudb.errors import ProjDbBuildError
from geodetic_engine.projdb.settings import find_env_file
from geodetic_engine.projdb.validate import validate

logger = logging.getLogger("geodetic_engine.osdudb")

_FILE_FORMAT = "%(asctime)s %(levelname)-8s %(name)s: %(message)s"
_CONSOLE_FORMAT = "%(levelname)s %(name)s: %(message)s"


def sidecar(output_db: Path, suffix: str, *, source: str | None = None) -> Path:
    """Return a path next to the database, for example ``proj.db.log``.

    Args:
        output_db: The database the file describes.
        suffix: What to append, for example ``".report.json"``.
        source: Name of the build to put in the file name, for example
            ``"osdudb"``. Given when appending, where several builds describe
            one database and a name derived from the database alone would let
            the last of them overwrite the others' provenance.

    Returns:
        The path to write, for example ``proj.db.osdudb.report.json``.
    """
    tag = f".{source}" if source else ""
    return output_db.with_suffix(output_db.suffix + tag + suffix)


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
        prog="geodetic-osdudb",
        description=(
            "Build a PROJ database enriched with the CRSs and transformations "
            "published in an OSDU coordinate reference catalogue."
        ),
    )
    parser.add_argument(
        "-v", "--verbose", action="store_true", help="log every imported object"
    )
    sub = parser.add_subparsers(dest="command", required=True)

    build_cmd = sub.add_parser("build", help="build an enriched proj.db")
    build_cmd.add_argument(
        "catalog",
        type=Path,
        nargs="?",
        help="the OSDU manifest to read, for example CRS_CT.json",
    )
    build_cmd.add_argument(
        "--config", type=Path, help="TOML file with an [osdudb] table"
    )
    build_cmd.add_argument("--output", type=Path, help="path of the database to write")
    build_cmd.add_argument(
        "--authority",
        action="append",
        dest="authorities",
        help=(
            "code space to import; repeatable. Defaults to OSDU. Add EPSG to "
            "also import the catalogue's EPSG objects that this proj.db's EPSG "
            "dataset does not yet define."
        ),
    )
    build_cmd.add_argument(
        "--append",
        action="store_true",
        help=(
            "add to the database already at --output instead of rebuilding it "
            "from the base proj.db, so this build extends what another source "
            "already wrote there"
        ),
    )
    build_cmd.add_argument(
        "--overwrite-existing",
        action="store_true",
        help=(
            "replace a colliding row of this build's own authorities instead "
            "of aborting; another authority's rows are still never touched"
        ),
    )
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
        help="authority to check; repeatable",
    )

    inspect_cmd = sub.add_parser(
        "inspect", help="summarise what a built database contains"
    )
    inspect_cmd.add_argument("database", type=Path)

    config_cmd = sub.add_parser(
        "config", help="show the resolved settings and where they came from"
    )
    config_cmd.add_argument("catalog", type=Path, nargs="?")
    config_cmd.add_argument(
        "--config", type=Path, help="TOML file with an [osdudb] table"
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

    try:
        if args.command == "build":
            return _build(args)
        if args.command == "validate":
            print(
                json.dumps(
                    validate(args.database, authorities=args.authorities), indent=2
                )
            )
            return 0
        if args.command == "inspect":
            print(json.dumps(_inspect(args.database), indent=2))
            return 0
        if args.command == "config":
            print(json.dumps(_show_config(args), indent=2))
            return 0
    except GeodeticEngineError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 2


def _overrides(args: argparse.Namespace) -> dict[str, Any]:
    """The settings the command line states, if any."""
    overrides: dict[str, Any] = {}
    if getattr(args, "catalog", None) is not None:
        overrides["catalog"] = args.catalog
    if getattr(args, "output", None) is not None:
        overrides["output_db"] = args.output
    if getattr(args, "authorities", None):
        overrides["authorities"] = args.authorities
    if getattr(args, "append", False):
        overrides["append"] = True
    if getattr(args, "overwrite_existing", False):
        overrides["overwrite_existing"] = True
    return overrides


def _build(args: argparse.Namespace) -> int:
    config = load_config(config_file=args.config, **_overrides(args))

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

    # An appending build shares its output with the builds that came before it,
    # so it must not write over their report and log.
    tag = "osdudb" if config.append else None
    log_path = sidecar(config.output_db, ".log", source=tag)
    report_path = sidecar(config.output_db, ".report.json", source=tag)
    with _log_to_file(log_path):
        logger.info("geodetic-osdudb build starting")
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
                    **validate(
                        config.output_db,
                        authorities=config.authorities,
                        imported=report.imported_objects(),
                    ),
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


def _show_config(args: argparse.Namespace) -> dict[str, Any]:
    """Resolve the configuration and describe where it came from."""
    resolved = load_config(config_file=args.config, **_overrides(args))
    env_file = find_env_file()
    return {
        "config_file": str(resolved.source_file) if resolved.source_file else None,
        "env_file": str(env_file) if env_file else None,
        "catalog": str(resolved.catalog),
        "catalog_version": resolved.catalog_version,
        "authorities": sorted(resolved.authorities),
        "naming_systems": sorted(resolved.naming_systems),
        "output_db": str(resolved.output_db),
        "base_proj_db": str(resolved.base_proj_db),
        "include_deprecated": resolved.include_deprecated,
        "authority_preference": resolved.authority_preference.value,
        "fallback_authorities": list(resolved.fallback_authorities),
        "unsupported_method_codes": sorted(resolved.unsupported_method_codes),
        "append": resolved.append,
        "overwrite_existing": resolved.overwrite_existing,
    }


def _inspect(database: Path) -> dict[str, Any]:
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
