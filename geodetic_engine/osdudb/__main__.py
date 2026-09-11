"""Command line entry point for building a proj.db from an OSDU catalogue.

A build needs nothing but the catalogue file, so the common case is::

    geodetic-osdudb build CRS_CT.json

Everything else has a default or lives in an optional ``geodetic-osdudb.toml``.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path
from typing import Any

from geodetic_engine.errors import GeodeticEngineError
from geodetic_engine.osdudb.build import build
from geodetic_engine.osdudb.config import load_config
from geodetic_engine.projdb.cli import inspect_database as _inspect
from geodetic_engine.projdb.cli import run_build
from geodetic_engine.projdb.cli import sidecar as sidecar
from geodetic_engine.projdb.settings import find_env_file
from geodetic_engine.projdb.validate import validate

logger = logging.getLogger("geodetic_engine.osdudb")

_FILE_FORMAT = "%(asctime)s %(levelname)-8s %(name)s: %(message)s"
_CONSOLE_FORMAT = "%(levelname)s %(name)s: %(message)s"


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
    finally:
        root.removeHandler(console)
        console.close()
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
    if getattr(args, "overwrite_rows", False):
        overrides["overwrite_rows"] = True
    return overrides


def _build(args: argparse.Namespace) -> int:
    config = load_config(config_file=args.config, **_overrides(args))

    return run_build(
        config,
        build,
        source="osdudb",
        dry_run=args.dry_run,
        skip_validation=args.skip_validation,
    )


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
        "overwrite_rows": resolved.overwrite_rows,
    }


if __name__ == "__main__":
    raise SystemExit(main())
