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
from typing import Any

from geodetic_engine.projdb.config import AuthorityPreference, ProjDbBuildConfig

logger = logging.getLogger(__name__)

TABLE = "authority_to_authority_preference"

# PROJ's wildcard for "any authority" in either position.
ANY = "any"


def preference_rows(
    config: ProjDbBuildConfig, existing: dict[tuple[str, str], str]
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

    # Pairs that involve a custom authority: prefer the custom operations.
    targets = [ANY, *config.fallback_authorities, *custom]
    for source in custom:
        for target in dict.fromkeys(targets):
            rows.append(_row(source, target, preferred))
    for source in dict.fromkeys([ANY, *config.fallback_authorities]):
        for target in custom:
            rows.append(_row(source, target, preferred))

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
