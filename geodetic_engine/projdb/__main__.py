"""Command line entry point for the custom proj.db workflow.

Credentials are never accepted as arguments; they come from the environment or
a gitignored ``.env`` file, because arguments end up in shell history and in
process listings.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

from geodetic_engine.errors import GeodeticEngineError
from geodetic_engine.projdb.build import build
from geodetic_engine.projdb.cli import inspect_database as _inspect
from geodetic_engine.projdb.cli import run_build
from geodetic_engine.projdb.cli import sidecar as sidecar
from geodetic_engine.projdb.config import find_env_file, load_config
from geodetic_engine.projdb.validate import validate

logger = logging.getLogger("geodetic_engine.projdb")

_FILE_FORMAT = "%(asctime)s %(levelname)-8s %(name)s: %(message)s"
_CONSOLE_FORMAT = "%(levelname)s %(name)s: %(message)s"


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
        "--append",
        action="store_true",
        help=(
            "add to the database already at --output instead of rebuilding it "
            "from the base proj.db, so this build extends what another source "
            "already wrote there"
        ),
    )
    build_cmd.add_argument(
        "--overwrite-rows",
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
    finally:
        root.removeHandler(console)
        console.close()
    return 2


def _build(args: argparse.Namespace) -> int:
    overrides = {}
    if args.output is not None:
        overrides["output_db"] = args.output
    if args.append:
        overrides["append"] = True
    if args.overwrite_rows:
        overrides["overwrite_rows"] = True
    config = load_config(config_file=args.config, **overrides)

    return run_build(
        config,
        build,
        source="projdb",
        dry_run=args.dry_run,
        skip_validation=args.skip_validation,
    )


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
        "append": resolved.append,
        "overwrite_rows": resolved.overwrite_rows,
        "page_size": resolved.georepository.page_size,
    }


if __name__ == "__main__":
    raise SystemExit(main())
