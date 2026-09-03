"""Authority preferences for coordinate operation selection.

PROJ consults ``authority_to_authority_preference`` when it looks for
operations between two CRSs. The row matching the source and target authority
names, falling back to ``any``, lists which authorities' operations are
candidates and in what order. Without a row naming a custom authority, PROJ
will not consider that authority's operations for a CRS pair, so custom
transformations are silently invisible outside of directly named custom CRSs.

Because these rows change which operation is applied to a coordinate, what gets
written is driven by an explicit configuration mode rather than assumed.
"""

from __future__ import annotations

import logging
import sqlite3
from collections.abc import Iterable
from typing import Any

from geodetic_engine.projdb.settings import AuthorityPreference, PreferenceSettings

logger = logging.getLogger(__name__)

TABLE = "authority_to_authority_preference"

# The table PROJ consults to decide which authorities exist at all.
BUILTIN_TABLE = "builtin_authorities"

# PROJ's wildcard for "any authority" in either position.
ANY = "any"


def builtin_rows(
    authorities: Iterable[str], existing: Iterable[str]
) -> list[dict[str, Any]]:
    """Build the ``builtin_authorities`` rows a set of authorities needs.

    PROJ resolves ``AUTHORITY:CODE`` only for authorities listed in this table.
    An object written under an authority that is missing from it is present in
    the database, passes every foreign key, and is still reported as "crs not
    found", so registering the authority is not optional.

    Args:
        authorities: The authorities this build writes objects under.
        existing: The authorities the database already lists.

    Returns:
        One row per authority not yet listed, in a deterministic order.

    Example:
        >>> builtin_rows(["Example"], ["EPSG", "PROJ"])
        [{'auth_name': 'Example'}]
        >>> builtin_rows(["EPSG"], ["EPSG", "PROJ"])
        []
    """
    known = {name.casefold() for name in existing}
    return [
        {"auth_name": name}
        for name in sorted(authorities)
        if name.casefold() not in known
    ]


def read_builtin(connection: sqlite3.Connection) -> set[str]:
    """Return the authorities a database currently lists as built in."""
    return {
        str(name)
        for (name,) in connection.execute(f"SELECT auth_name FROM {BUILTIN_TABLE}")
    }


def preference_rows(
    config: PreferenceSettings, existing: dict[tuple[str, str], str]
) -> list[dict[str, Any]]:
    """Build the preference rows for a configuration.

    Args:
        config: The build configuration, whose ``authority_preference`` mode
            decides how far the custom authorities reach.
        existing: The rows already in the database, keyed by
            ``(source_auth_name, target_auth_name)`` with the allowed authority
            list as the value.

    Returns:
        Rows to upsert, in a deterministic order.

    Example:
        For a single custom authority ``Example`` in ``custom_first`` mode, the
        pair ``(Example, any)`` gets ``Example,PROJ,EPSG`` so custom operations
        win where they exist, while the stock ``(EPSG, EPSG)`` row is extended
        to ``PROJ,EPSG,NKG,Example`` so custom operations are considered last.
    """
    if config.authority_preference is AuthorityPreference.NONE:
        return []

    custom = sorted(config.authorities)
    preferred = _join(custom + list(config.fallback_authorities))
    rows: list[dict[str, Any]] = []

    def preference(source: str, target: str) -> str:
        """This build's preference, keeping an earlier build's behind it.

        Only reached when appending, where an existing row was written by a
        build for another authority. Dropping it would make that authority's
        operations invisible for the pair, which is a silent behaviour change
        to a database the caller asked to extend, not rebuild.
        """
        if not config.append or (previous := existing.get((source, target))) is None:
            return preferred
        return _join(preferred.split(",") + previous.split(","))

    # Pairs that involve a custom authority: prefer the custom operations.
    targets = [ANY, *config.fallback_authorities, *custom]
    for source in custom:
        for target in dict.fromkeys(targets):
            rows.append(_row(source, target, preference(source, target)))
    for source in dict.fromkeys([ANY, *config.fallback_authorities]):
        for target in custom:
            rows.append(_row(source, target, preference(source, target)))

    if config.authority_preference is AuthorityPreference.CUSTOM_FIRST:
        # Pairs between other authorities: append the custom authorities so
        # their operations become candidates, but rank behind the established
        # ones rather than displacing them.
        for (source, target), allowed in sorted(existing.items()):
            if source in custom or target in custom:
                continue
            extended = _join(allowed.split(",") + custom)
            if extended != allowed:
                rows.append(_row(source, target, extended))

    deduplicated: dict[tuple[str, str], dict[str, Any]] = {
        (row["source_auth_name"], row["target_auth_name"]): row for row in rows
    }
    return list(deduplicated.values())


def read_existing(connection: sqlite3.Connection) -> dict[tuple[str, str], str]:
    """Return the preference rows currently in a database."""
    statement = (
        f"SELECT source_auth_name, target_auth_name, allowed_authorities FROM {TABLE}"
    )
    return {
        (str(source), str(target)): str(allowed)
        for source, target, allowed in connection.execute(statement)
    }


def _row(source: str, target: str, allowed: str) -> dict[str, Any]:
    return {
        "source_auth_name": source,
        "target_auth_name": target,
        "allowed_authorities": allowed,
    }


def _join(names: list[str]) -> str:
    """Join authority names, preserving order and dropping duplicates."""
    return ",".join(
        dict.fromkeys(name.strip() for name in names if name and name.strip())
    )
