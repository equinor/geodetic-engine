"""Settings shared by every custom proj.db build, whatever the source.

A build needs the same things regardless of where the definitions come from: a
base database to copy, a place to write, the authorities whose objects may be
written, and how those authorities enter PROJ's operation selection. Those, and
the parsing that turns a TOML file and an environment into them, live here so
that one source's configuration cannot drift away from another's.

Secrets are only ever accepted from the environment or a ``.env`` file; they are
never read from a TOML file or from command line arguments, both of which
routinely end up in version control, shell history and process listings.
"""

from __future__ import annotations

import os
import tomllib
from collections.abc import Iterable, Mapping
from enum import StrEnum
from pathlib import Path
from typing import Any, Final, Protocol, runtime_checkable

from dotenv import find_dotenv, load_dotenv

from geodetic_engine.projdb.errors import ConfigurationError

ENV_PREFIX: Final = "GEODETIC_ENGINE_"

# Coordinate operation methods this PROJ build cannot evaluate. Objects using
# them are skipped and reported rather than written as unusable rows.
DEFAULT_UNSUPPORTED_METHOD_CODES: Final[frozenset[int]] = frozenset({1044, 1108})

# Authorities listed after the custom ones in every generated preference row.
DEFAULT_FALLBACK_AUTHORITIES: Final[tuple[str, ...]] = ("PROJ", "EPSG")


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


@runtime_checkable
class DatabaseSettings(Protocol):
    """What :class:`~geodetic_engine.projdb.writer.ProjDbWriter` needs to know."""

    @property
    def output_db(self) -> Path:
        """Path the enriched database is written to."""

    @property
    def base_proj_db(self) -> Path:
        """Official proj.db used as the starting point."""

    @property
    def authorities(self) -> frozenset[str]:
        """Authority names whose rows may be written to the database."""

    @property
    def append(self) -> bool:
        """Add to an existing output database instead of rebuilding it."""

    @property
    def overwrite_existing(self) -> bool:
        """Replace a colliding row of this build's own authorities."""


@runtime_checkable
class PreferenceSettings(DatabaseSettings, Protocol):
    """What :mod:`~geodetic_engine.projdb.authority` needs to know."""

    @property
    def authority_preference(self) -> AuthorityPreference:
        """How the custom authorities enter operation selection."""

    @property
    def fallback_authorities(self) -> tuple[str, ...]:
        """Authorities listed after the custom ones in every preference row."""


def default_base_proj_db() -> Path:
    """Return the proj.db shipped with the installed PROJ."""
    from pyproj.datadir import get_data_dir

    return Path(get_data_dir()) / "proj.db"


def as_set(raw: Any) -> frozenset[str]:
    """Coerce a comma separated string or a sequence into a set of names."""
    if raw is None:
        return frozenset()
    if isinstance(raw, str):
        return frozenset(part.strip() for part in raw.split(",") if part.strip())
    return frozenset(str(item).strip() for item in raw if str(item).strip())


def as_tuple(raw: Any, default: tuple[str, ...]) -> tuple[str, ...]:
    """Coerce a comma separated string or a sequence into an ordered tuple."""
    if raw is None:
        return default
    parts = (
        [part.strip() for part in raw.split(",") if part.strip()]
        if isinstance(raw, str)
        else [str(item).strip() for item in raw if str(item).strip()]
    )
    return tuple(parts) or default


def as_method_codes(raw: Any) -> frozenset[int]:
    """Coerce a list of EPSG method codes, rejecting anything non-numeric."""
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


def as_bool(raw: Any, *, default: bool) -> bool:
    """Coerce a TOML boolean or an environment string into a bool."""
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


def as_preference(raw: Any) -> AuthorityPreference:
    """Coerce a preference mode name, naming the alternatives when it is wrong."""
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


def load_env_file() -> Path | None:
    """Load a ``.env`` file into the environment without overriding it."""
    if env_file := find_env_file():
        load_dotenv(env_file, override=False)
    return env_file


def find_config_file(
    explicit: Path | None,
    env: Mapping[str, str] | None,
    *,
    default_filename: str,
) -> Path | None:
    """Locate the settings file to read.

    Searched in order: an explicitly given path, ``GEODETIC_ENGINE_CONFIG``, and
    then ``default_filename`` in the working directory.

    Args:
        explicit: A path given on the command line or by a caller.
        env: Environment mapping. Defaults to :data:`os.environ`.
        default_filename: File looked for in the working directory when nothing
            was named.

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

    default = Path(default_filename)
    return default if default.is_file() else None


def read_config_table(
    path: Path,
    *,
    table: str,
    known_keys: Iterable[str],
    secret_keys: Iterable[str] = (),
) -> dict[str, Any]:
    """Read and check one table out of a TOML settings file.

    Args:
        path: The file to read.
        table: Name of the table the settings must live under.
        known_keys: Every key the table accepts. A key outside this set is a
            typo, and a typo that is ignored is a setting the operator believes
            is applied when it is not.
        secret_keys: Keys that must never appear in a version controlled file.

    Returns:
        The table's contents.

    Raises:
        ConfigurationError: If the table is absent, names a secret, or contains
            an unrecognised key.
    """
    with path.open("rb") as handle:
        document = tomllib.load(handle)
    values: dict[str, Any] | None = document.get(table)
    if values is None:
        raise ConfigurationError(
            f"{path} has no [{table}] table; settings must live under it"
        )

    leaked = sorted(set(secret_keys).intersection(values))
    if leaked:
        raise ConfigurationError(
            f"{path} contains {leaked}; supply credentials through the "
            "environment or a gitignored .env file instead, never through a "
            "file meant to be version controlled"
        )
    unknown = sorted(set(values) - set(known_keys))
    if unknown:
        raise ConfigurationError(
            f"{path} has unrecognised setting(s) {unknown} in [{table}]. "
            f"Valid settings are: {', '.join(sorted(known_keys))}"
        )
    return values
