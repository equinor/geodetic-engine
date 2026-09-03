"""Build configuration for the custom PROJ database workflow.

Everything that identifies a particular organisation - the Georepository
instance, its credentials, and which authority names count as "custom" - is
configuration. Nothing here has an organisation-specific default, so the same
code builds one organisation's database and anyone else's.

Configuration is read from, in decreasing precedence: explicit keyword
arguments, environment variables, an optional TOML file, and a ``.env`` file.
Secrets are only ever accepted from the environment or ``.env``; they are never
read from the TOML file or from command line arguments, both of which routinely
end up in version control, shell history and process listings.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final

from geodetic_engine.georepository.config import (
    DEFAULT_PAGE_SIZE,
    DEFAULT_SCOPE,
    GeorepositoryConfig,
)
from geodetic_engine.georepository.errors import GeorepositoryConfigError
from geodetic_engine.projdb.errors import ConfigurationError
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
    find_env_file,
    load_env_file,
    read_config_table,
)
from geodetic_engine.projdb.settings import (
    find_config_file as _find_config_file,
)

__all__ = [
    "DEFAULT_CONFIG_FILENAME",
    "DEFAULT_UNSUPPORTED_METHOD_CODES",
    "ENV_PREFIX",
    "AuthorityPreference",
    "ProjDbBuildConfig",
    "find_config_file",
    "find_env_file",
    "load_config",
]

# Looked for in the working directory when no config file is given explicitly,
# so an operator can keep every non-secret setting in one edited file.
DEFAULT_CONFIG_FILENAME: Final = "geodetic-projdb.toml"

# The table the settings live under in that file.
CONFIG_TABLE: Final = "projdb"

_SECRET_KEYS: Final = frozenset({"client_id", "client_secret"})

# Every key accepted in the config file. A key outside this set is a typo, and a
# typo that is ignored is a setting the operator believes is applied when it is
# not.
_FILE_KEYS: Final = frozenset(
    {
        "api_url",
        "token_url",
        "scope",
        "authorities",
        "naming_systems",
        "output_db",
        "base_proj_db",
        "include_deprecated",
        "unsupported_method_codes",
        "authority_preference",
        "annotate_foreign_objects",
        "fallback_authorities",
        "append",
        "overwrite_existing",
        "page_size",
        "request_timeout",
        "georepository_version",
    }
)


@dataclass(frozen=True, slots=True)
class ProjDbBuildConfig:
    """Resolved configuration for one custom proj.db build.

    Attributes:
        georepository: Connection settings for the source register.
        authorities: Authority names whose objects are imported. Only rows whose
            ``auth_name`` is in this set may be written to the database.
        output_db: Path the enriched database is written to.
        base_proj_db: Official proj.db used as the starting point.
        naming_systems: Naming systems whose aliases are imported. Defaults to
            ``authorities``.
        include_deprecated: Import deprecated objects, flagged as deprecated and
            linked to their replacements. Enabled by default so that callers can
            answer "this code is deprecated, superseded by X" rather than
            "unknown code".
        unsupported_method_codes: EPSG method codes to skip.
        authority_preference: How custom authorities enter operation selection.
        annotate_foreign_objects: Import this authority's aliases and usages for
            objects owned by another authority, such as a local name for an EPSG
            CRS. Requires enumerating every CRS in the register rather than only
            this authority's, so it is the slowest part of a build; the objects
            themselves are never rewritten.
        fallback_authorities: Authorities listed after the custom ones in every
            generated preference row.
        append: Add to the database already at ``output_db`` instead of starting
            from a fresh copy of ``base_proj_db``, so a second source can extend
            what a first one built. Has no effect when the output does not exist
            yet. Disabled by default: a build that silently added to whatever
            happened to be at the output path could not be reproduced from its
            configuration alone.
        overwrite_existing: Replace a row this build collides with rather than
            aborting. Reaches only the configured ``authorities``, since the
            per-row authority guard runs first and every object table is keyed
            on ``(auth_name, code)``. Disabled by default, so a collision is a
            reported failure rather than a definition that changed underneath
            whoever was already using it.
        georepository_version: Optional Georepository version name to record.
        source_file: The config file the settings were read from, if any.
    """

    georepository: GeorepositoryConfig
    authorities: frozenset[str]
    output_db: Path
    base_proj_db: Path
    naming_systems: frozenset[str] = frozenset()
    include_deprecated: bool = True
    unsupported_method_codes: frozenset[int] = DEFAULT_UNSUPPORTED_METHOD_CODES
    authority_preference: AuthorityPreference = AuthorityPreference.CUSTOM_FIRST
    annotate_foreign_objects: bool = True
    fallback_authorities: tuple[str, ...] = DEFAULT_FALLBACK_AUTHORITIES
    append: bool = False
    overwrite_existing: bool = False
    georepository_version: str | None = None
    source_file: Path | None = None

    def __post_init__(self) -> None:
        if not self.authorities:
            where = self.source_file or DEFAULT_CONFIG_FILENAME
            raise ConfigurationError(
                "at least one custom authority is required and there is no "
                f"default: set authorities in {where}, or "
                f"{ENV_PREFIX}AUTHORITIES in the environment, to name the "
                "authority whose objects should be imported"
            )
        if self.output_db.resolve() == self.base_proj_db.resolve():
            raise ConfigurationError(
                "output_db must not be the base proj.db; the official database "
                "is never modified in place"
            )
        if not self.naming_systems:
            object.__setattr__(self, "naming_systems", self.authorities)

    def __repr__(self) -> str:
        """Render without secrets, so configs can be logged safely."""
        return (
            f"ProjDbBuildConfig(georepository={self.georepository!r}, "
            f"authorities={sorted(self.authorities)!r}, "
            f"output_db={str(self.output_db)!r}, "
            f"include_deprecated={self.include_deprecated!r}, "
            f"authority_preference={self.authority_preference.value!r})"
        )

    @property
    def api_url(self) -> str:
        """Base URL of the Georepository instance."""
        return self.georepository.api_url

    def endpoint(self, name: str) -> str:
        """Return the absolute URL of a Georepository v1 collection endpoint."""
        return self.georepository.endpoint(name)


def find_config_file(
    explicit: Path | None = None, env: Mapping[str, str] | None = None
) -> Path | None:
    """Locate the settings file to read.

    Searched in order: an explicitly given path, ``GEODETIC_ENGINE_CONFIG``, and
    then :data:`DEFAULT_CONFIG_FILENAME` in the working directory.

    Args:
        explicit: A path given on the command line or by a caller.
        env: Environment mapping. Defaults to :data:`os.environ`.

    Returns:
        The file to read, or None when there is none to read.

    Raises:
        ConfigurationError: If a file was named explicitly or through the
            environment but does not exist. Falling back silently would apply a
            different configuration than the operator asked for.
    """
    return _find_config_file(explicit, env, default_filename=DEFAULT_CONFIG_FILENAME)


def _read_config_file(path: Path) -> dict[str, Any]:
    return read_config_table(
        path,
        table=CONFIG_TABLE,
        known_keys=_FILE_KEYS,
        secret_keys=_SECRET_KEYS,
    )


def load_config(
    *,
    config_file: Path | None = None,
    env: Mapping[str, str] | None = None,
    load_dotenv_file: bool = True,
    **overrides: Any,
) -> ProjDbBuildConfig:
    """Build a :class:`ProjDbBuildConfig` from file, environment and overrides.

    Non-secret settings belong in the config file, which is safe to version
    control. Credentials come from the environment or a gitignored ``.env``
    file, and are rejected if they appear in the config file.

    Args:
        config_file: TOML file with a ``[projdb]`` table. When omitted, the file
            is located by :func:`find_config_file`.
        env: Environment mapping. Defaults to :data:`os.environ`.
        load_dotenv_file: Load a ``.env`` file into the environment first.
        **overrides: Explicit values taking precedence over all other sources.

    Returns:
        A validated configuration.

    Raises:
        ConfigurationError: If a required value is missing, a value is
            malformed, or the config file names a secret or an unknown setting.

    Example:
        >>> load_config()  # doctest: +SKIP
        ProjDbBuildConfig(georepository=..., authorities=['Example'], ...)
    """
    if load_dotenv_file:
        load_env_file()
    environ = os.environ if env is None else env

    resolved_file = find_config_file(config_file, environ)
    file_values = _read_config_file(resolved_file) if resolved_file else {}

    def value(key: str, env_suffix: str) -> Any:
        if key in overrides:
            return overrides[key]
        if (from_env := environ.get(ENV_PREFIX + env_suffix)) is not None:
            return from_env
        return file_values.get(key)

    output_db = value("output_db", "OUTPUT_DB")
    if not output_db:
        raise ConfigurationError(
            "an output database path is required: set output_db in "
            f"{resolved_file or DEFAULT_CONFIG_FILENAME} or "
            f"{ENV_PREFIX}OUTPUT_DB in the environment"
        )

    include_deprecated = as_bool(
        value("include_deprecated", "INCLUDE_DEPRECATED"), default=True
    )
    try:
        georepository = GeorepositoryConfig(
            api_url=str(value("api_url", "GEOREP_URL") or ""),
            client_id=str(value("client_id", "GEOREP_CLIENT_ID") or ""),
            client_secret=str(value("client_secret", "GEOREP_CLIENT_SECRET") or ""),
            token_url=str(value("token_url", "GEOREP_TOKEN_URL") or ""),
            scope=str(value("scope", "GEOREP_SCOPE") or DEFAULT_SCOPE),
            page_size=int(value("page_size", "PAGE_SIZE") or DEFAULT_PAGE_SIZE),
            request_timeout=float(value("request_timeout", "REQUEST_TIMEOUT") or 60.0),
            include_deprecated=include_deprecated,
        )
    except GeorepositoryConfigError as exc:
        raise ConfigurationError(str(exc)) from exc

    base_proj_db = value("base_proj_db", "BASE_PROJ_DB")
    return ProjDbBuildConfig(
        georepository=georepository,
        authorities=as_set(value("authorities", "AUTHORITIES")),
        naming_systems=as_set(value("naming_systems", "NAMING_SYSTEMS")),
        output_db=Path(output_db),
        base_proj_db=Path(base_proj_db) if base_proj_db else default_base_proj_db(),
        include_deprecated=include_deprecated,
        unsupported_method_codes=as_method_codes(
            value("unsupported_method_codes", "UNSUPPORTED_METHOD_CODES")
        ),
        authority_preference=as_preference(
            value("authority_preference", "AUTHORITY_PREFERENCE")
        ),
        annotate_foreign_objects=as_bool(
            value("annotate_foreign_objects", "ANNOTATE_FOREIGN_OBJECTS"), default=True
        ),
        fallback_authorities=as_tuple(
            value("fallback_authorities", "FALLBACK_AUTHORITIES"),
            DEFAULT_FALLBACK_AUTHORITIES,
        ),
        append=as_bool(value("append", "APPEND"), default=False),
        overwrite_existing=as_bool(
            value("overwrite_existing", "OVERWRITE_EXISTING"), default=False
        ),
        georepository_version=value("georepository_version", "GEOREP_VERSION"),
        source_file=resolved_file,
    )
