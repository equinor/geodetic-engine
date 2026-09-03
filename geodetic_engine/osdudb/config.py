"""Build configuration for a proj.db built from an OSDU catalogue.

An OSDU build needs no credentials and no network: the whole catalogue is one
file. Everything else - which authorities may be written, where the output
goes, how those authorities enter PROJ's operation selection - is the same as
for any other source and comes from
:mod:`geodetic_engine.projdb.settings`.

Settings are read from, in decreasing precedence: explicit keyword arguments,
environment variables, and an optional TOML file. A config file is optional; a
build can be run against nothing but the path of the catalogue.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Final

from geodetic_engine.osdudb.errors import ConfigurationError
from geodetic_engine.projdb.settings import (
    DEFAULT_FALLBACK_AUTHORITIES,
    DEFAULT_UNSUPPORTED_METHOD_CODES,
    ENV_PREFIX,
    AuthorityPreference,
    as_bool,
    as_method_codes,
    as_preference,
    as_set,
    as_tuple,
    default_base_proj_db,
    find_config_file,
    load_env_file,
    read_config_table,
)

# Looked for in the working directory when no config file is given explicitly.
DEFAULT_CONFIG_FILENAME: Final = "geodetic-osdudb.toml"

# The table the settings live under in that file.
CONFIG_TABLE: Final = "osdudb"

# The code space OSDU uses for the objects it defines itself, as opposed to the
# EPSG objects it republishes. Unlike a Georepository instance, whose authority
# name is the organisation's own, this one is fixed by the OSDU standard, so it
# can be a default rather than a required setting.
DEFAULT_AUTHORITY: Final = "OSDU"

# Written when nothing else is configured, so a build can be run against
# nothing but the catalogue path.
DEFAULT_OUTPUT_DB: Final = Path("build/proj.db")

_FILE_KEYS: Final = frozenset(
    {
        "catalog",
        "authorities",
        "naming_systems",
        "output_db",
        "base_proj_db",
        "include_deprecated",
        "unsupported_method_codes",
        "authority_preference",
        "fallback_authorities",
        "append",
        "overwrite_existing",
        "catalog_version",
    }
)


@dataclass(frozen=True, slots=True)
class OsduBuildConfig:
    """Resolved configuration for one proj.db build from an OSDU catalogue.

    Attributes:
        catalog: The OSDU manifest file, holding ``ReferenceData`` records of
            kind ``CoordinateReferenceSystem`` and ``CoordinateTransformation``.
        authorities: Code spaces whose objects are imported. Only rows whose
            ``auth_name`` is in this set may be written to the database.
            Defaults to ``{"OSDU"}``; adding ``"EPSG"`` also imports the EPSG
            objects the catalogue carries that the base proj.db's EPSG dataset
            does not yet define.
        output_db: Path the enriched database is written to.
        base_proj_db: Official proj.db used as the starting point.
        naming_systems: Naming systems whose aliases are imported. Defaults to
            ``authorities``; ``["*"]`` imports every naming system.
        include_deprecated: Import records flagged with ``InactiveIndicator``,
            marked deprecated in the database. Enabled by default so a caller
            can answer "this code is deprecated" rather than "unknown code".
        unsupported_method_codes: EPSG method codes to skip.
        authority_preference: How the imported authorities enter operation
            selection.
        fallback_authorities: Authorities listed after the imported ones in
            every generated preference row.
        append: Add to the database already at ``output_db`` instead of starting
            from a fresh copy of ``base_proj_db``, so this catalogue can extend
            a database another source already built. Has no effect when the
            output does not exist yet. Disabled by default: a build that
            silently added to whatever happened to be at the output path could
            not be reproduced from its configuration alone.
        overwrite_existing: Replace a row this build collides with rather than
            aborting. Reaches only the configured ``authorities``, since the
            per-row authority guard runs first and every object table is keyed
            on ``(auth_name, code)``. Disabled by default, so a collision is a
            reported failure rather than a definition that changed underneath
            whoever was already using it.
        catalog_version: Optional catalogue version to record in the report.
        source_file: The config file the settings were read from, if any.
    """

    catalog: Path
    authorities: frozenset[str] = frozenset({DEFAULT_AUTHORITY})
    output_db: Path = DEFAULT_OUTPUT_DB
    base_proj_db: Path = field(default_factory=default_base_proj_db)
    naming_systems: frozenset[str] = frozenset()
    include_deprecated: bool = True
    unsupported_method_codes: frozenset[int] = DEFAULT_UNSUPPORTED_METHOD_CODES
    authority_preference: AuthorityPreference = AuthorityPreference.CUSTOM_FIRST
    fallback_authorities: tuple[str, ...] = DEFAULT_FALLBACK_AUTHORITIES
    append: bool = False
    overwrite_existing: bool = False
    catalog_version: str | None = None
    source_file: Path | None = None

    def __post_init__(self) -> None:
        if not self.authorities:
            raise ConfigurationError(
                "at least one authority is required: set authorities in "
                f"{self.source_file or DEFAULT_CONFIG_FILENAME}, or "
                f"{ENV_PREFIX}AUTHORITIES in the environment, to name the code "
                "spaces whose objects should be imported"
            )
        if not self.catalog.is_file():
            raise ConfigurationError(
                f"the OSDU catalogue {str(self.catalog)!r} does not exist"
            )
        if self.output_db.resolve() == self.base_proj_db.resolve():
            raise ConfigurationError(
                "output_db must not be the base proj.db; the official database "
                "is never modified in place"
            )
        if not self.naming_systems:
            object.__setattr__(self, "naming_systems", self.authorities)

    def __repr__(self) -> str:
        """Render compactly, so configs can be logged."""
        return (
            f"OsduBuildConfig(catalog={str(self.catalog)!r}, "
            f"authorities={sorted(self.authorities)!r}, "
            f"output_db={str(self.output_db)!r}, "
            f"include_deprecated={self.include_deprecated!r}, "
            f"authority_preference={self.authority_preference.value!r})"
        )


def load_config(
    *,
    config_file: Path | None = None,
    env: Mapping[str, str] | None = None,
    load_dotenv_file: bool = True,
    **overrides: Any,
) -> OsduBuildConfig:
    """Build an :class:`OsduBuildConfig` from file, environment and overrides.

    Unlike the Georepository workflow there is nothing secret to configure, so
    a config file is optional and a build can be driven entirely by the
    ``catalog`` override.

    Args:
        config_file: TOML file with an ``[osdudb]`` table. When omitted, the
            file is located by
            :func:`~geodetic_engine.projdb.settings.find_config_file`.
        env: Environment mapping. Defaults to :data:`os.environ`.
        load_dotenv_file: Load a ``.env`` file into the environment first, so
            an operator can keep one settings file for both workflows.
        **overrides: Explicit values taking precedence over all other sources.

    Returns:
        A validated configuration.

    Raises:
        ConfigurationError: If the catalogue is not named or does not exist, or
            a value is malformed, or the config file has an unknown setting.

    Example:
        >>> load_config(catalog=Path("CRS_CT.json"))  # doctest: +SKIP
        OsduBuildConfig(catalog='CRS_CT.json', authorities=['OSDU'], ...)
    """
    if load_dotenv_file:
        load_env_file()
    environ = os.environ if env is None else env

    resolved_file = find_config_file(
        config_file, environ, default_filename=DEFAULT_CONFIG_FILENAME
    )
    file_values = (
        read_config_table(resolved_file, table=CONFIG_TABLE, known_keys=_FILE_KEYS)
        if resolved_file
        else {}
    )

    def value(key: str, env_suffix: str) -> Any:
        if key in overrides:
            return overrides[key]
        if (from_env := environ.get(ENV_PREFIX + env_suffix)) is not None:
            return from_env
        return file_values.get(key)

    catalog = value("catalog", "OSDU_CATALOG")
    if not catalog:
        raise ConfigurationError(
            "the path of an OSDU catalogue is required: pass it on the command "
            f"line, set catalog in {resolved_file or DEFAULT_CONFIG_FILENAME}, "
            f"or set {ENV_PREFIX}OSDU_CATALOG in the environment"
        )

    base_proj_db = value("base_proj_db", "BASE_PROJ_DB")
    output_db = value("output_db", "OUTPUT_DB")
    return OsduBuildConfig(
        catalog=Path(catalog),
        authorities=as_set(value("authorities", "AUTHORITIES"))
        or frozenset({DEFAULT_AUTHORITY}),
        naming_systems=as_set(value("naming_systems", "NAMING_SYSTEMS")),
        output_db=Path(output_db) if output_db else DEFAULT_OUTPUT_DB,
        base_proj_db=Path(base_proj_db) if base_proj_db else default_base_proj_db(),
        include_deprecated=as_bool(
            value("include_deprecated", "INCLUDE_DEPRECATED"), default=True
        ),
        unsupported_method_codes=as_method_codes(
            value("unsupported_method_codes", "UNSUPPORTED_METHOD_CODES")
        ),
        authority_preference=as_preference(
            value("authority_preference", "AUTHORITY_PREFERENCE")
        ),
        fallback_authorities=as_tuple(
            value("fallback_authorities", "FALLBACK_AUTHORITIES"),
            DEFAULT_FALLBACK_AUTHORITIES,
        ),
        append=as_bool(value("append", "APPEND"), default=False),
        overwrite_existing=as_bool(
            value("overwrite_existing", "OVERWRITE_EXISTING"), default=False
        ),
        catalog_version=value("catalog_version", "OSDU_CATALOG_VERSION"),
        source_file=resolved_file,
    )
