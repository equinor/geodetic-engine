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
import tomllib
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any, Final

from dotenv import find_dotenv, load_dotenv

from geodetic_engine.georepository.config import (
    DEFAULT_PAGE_SIZE,
    DEFAULT_SCOPE,
    GeorepositoryConfig,
)
from geodetic_engine.georepository.errors import GeorepositoryConfigError
from geodetic_engine.projdb.errors import ConfigurationError

ENV_PREFIX: Final = "GEODETIC_ENGINE_"

# Looked for in the working directory when no config file is given explicitly,
# so an operator can keep every non-secret setting in one edited file.
DEFAULT_CONFIG_FILENAME: Final = "geodetic-projdb.toml"

# The table the settings live under in that file.
CONFIG_TABLE: Final = "projdb"

# Coordinate operation methods this PROJ build cannot evaluate. Objects using
# them are skipped and reported rather than written as unusable rows.
DEFAULT_UNSUPPORTED_METHOD_CODES: Final[frozenset[int]] = frozenset({1044, 1108})

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
        "page_size",
        "request_timeout",
        "georepository_version",
    }
)


class AuthorityPreference(StrEnum):
    """How custom authorities enter PROJ's operation selection.

    PROJ consults ``authority_to_authority_preference`` to decide which
    authorities' coordinate operations are candidates for a given CRS pair, and
    in what order. Because this changes which operation is applied to a
    coordinate, the mode is explicit configuration rather than a silent default.

    Attributes:
        CUSTOM_FIRST: Custom operations are preferred for CRS pairs involving a
            custom authority, and become candidates of last resort for pairs
            between other authorities. This is what an organisation that
            maintains its own operations normally wants.
        CUSTOM_ONLY: Custom operations are preferred for pairs involving a
            custom authority, and selection between other authorities is left
            exactly as PROJ ships it.
        NONE: No preference rows are written. Custom operations are only found
            when a custom CRS is named directly.
    """

    CUSTOM_FIRST = "custom_first"
    CUSTOM_ONLY = "custom_only"
    NONE = "none"


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
    fallback_authorities: tuple[str, ...] = ("PROJ", "EPSG")
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


def _default_base_proj_db() -> Path:
    from pyproj.datadir import get_data_dir

    return Path(get_data_dir()) / "proj.db"


def _as_set(raw: Any) -> frozenset[str]:
    if raw is None:
        return frozenset()
    if isinstance(raw, str):
        return frozenset(part.strip() for part in raw.split(",") if part.strip())
    return frozenset(str(item).strip() for item in raw if str(item).strip())


def _as_tuple(raw: Any, default: tuple[str, ...]) -> tuple[str, ...]:
    if raw is None:
        return default
    parts = (
        [part.strip() for part in raw.split(",") if part.strip()]
        if isinstance(raw, str)
        else [str(item).strip() for item in raw if str(item).strip()]
    )
    return tuple(parts) or default


def _as_method_codes(raw: Any) -> frozenset[int]:
    if raw is None:
        return DEFAULT_UNSUPPORTED_METHOD_CODES
    values = (
        [part.strip() for part in raw.split(",") if part.strip()]
        if isinstance(raw, str)
        else list(raw)
    )
    try:
        return frozenset(int(value) for value in values)
    except (TypeError, ValueError) as exc:
        raise ConfigurationError(
            f"unsupported method codes must be integers, got {raw!r}"
        ) from exc


def _as_bool(raw: Any, *, default: bool) -> bool:
    if raw is None:
        return default
    if isinstance(raw, bool):
        return raw
    normalised = str(raw).strip().lower()
    if normalised in {"1", "true", "yes", "on"}:
        return True
    if normalised in {"0", "false", "no", "off"}:
        return False
    raise ConfigurationError(f"expected a boolean value, got {raw!r}")


def _as_preference(raw: Any) -> AuthorityPreference:
    if raw is None:
        return AuthorityPreference.CUSTOM_FIRST
    if isinstance(raw, AuthorityPreference):
        return raw
    try:
        return AuthorityPreference(str(raw).strip().lower())
    except ValueError as exc:
        allowed = ", ".join(mode.value for mode in AuthorityPreference)
        raise ConfigurationError(
            f"{ENV_PREFIX}AUTHORITY_PREFERENCE must be one of {allowed}, got {raw!r}"
        ) from exc


def find_env_file() -> Path | None:
    """Locate the ``.env`` file holding credentials.

    Searched upward from the working directory. python-dotenv's default search
    starts from the calling module instead, which for an installed package means
    somewhere under site-packages, so the operator's ``.env`` would never be
    found.

    Returns:
        The file, or None when there is none.
    """
    found = find_dotenv(usecwd=True)
    return Path(found) if found else None


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
    environ = os.environ if env is None else env
    for candidate, described in (
        (explicit, "the --config option"),
        (
            Path(named) if (named := environ.get(f"{ENV_PREFIX}CONFIG")) else None,
            f"{ENV_PREFIX}CONFIG",
        ),
    ):
        if candidate is None:
            continue
        if not candidate.is_file():
            raise ConfigurationError(
                f"the config file {str(candidate)!r} given by {described} does "
                "not exist"
            )
        return candidate

    default = Path(DEFAULT_CONFIG_FILENAME)
    return default if default.is_file() else None


def _read_config_file(path: Path) -> dict[str, Any]:
    with path.open("rb") as handle:
        document = tomllib.load(handle)
    values: dict[str, Any] | None = document.get(CONFIG_TABLE)
    if values is None:
        raise ConfigurationError(
            f"{path} has no [{CONFIG_TABLE}] table; settings must live under it"
        )

    leaked = _SECRET_KEYS.intersection(values)
    if leaked:
        raise ConfigurationError(
            f"{path} contains {sorted(leaked)}; supply credentials through the "
            "environment or a gitignored .env file instead, never through a "
            "file meant to be version controlled"
        )
    unknown = sorted(set(values) - _FILE_KEYS)
    if unknown:
        raise ConfigurationError(
            f"{path} has unrecognised setting(s) {unknown} in [{CONFIG_TABLE}]. "
            f"Valid settings are: {', '.join(sorted(_FILE_KEYS))}"
        )
    return values


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
    if load_dotenv_file and (env_file := find_env_file()):
        load_dotenv(env_file, override=False)
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

    include_deprecated = _as_bool(
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
        authorities=_as_set(value("authorities", "AUTHORITIES")),
        naming_systems=_as_set(value("naming_systems", "NAMING_SYSTEMS")),
        output_db=Path(output_db),
        base_proj_db=Path(base_proj_db) if base_proj_db else _default_base_proj_db(),
        include_deprecated=include_deprecated,
        unsupported_method_codes=_as_method_codes(
            value("unsupported_method_codes", "UNSUPPORTED_METHOD_CODES")
        ),
        authority_preference=_as_preference(
            value("authority_preference", "AUTHORITY_PREFERENCE")
        ),
        annotate_foreign_objects=_as_bool(
            value("annotate_foreign_objects", "ANNOTATE_FOREIGN_OBJECTS"), default=True
        ),
        fallback_authorities=_as_tuple(
            value("fallback_authorities", "FALLBACK_AUTHORITIES"), ("PROJ", "EPSG")
        ),
        georepository_version=value("georepository_version", "GEOREP_VERSION"),
        source_file=resolved_file,
    )
