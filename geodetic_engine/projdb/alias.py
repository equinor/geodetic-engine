"""Alternative names for geodetic objects.

An alias is how an organisation's own name for a CRS or datum is carried
alongside the authority's official name, so a caller can look the object up by
either. proj.db stores them in ``alias_name``.

Aliases are collected for every kind of object that proj.db accepts one for,
including datums. Where the names come from is the caller's business: a
register exposes them on a per-object endpoint, a catalogue file carries them
inline.
"""

from __future__ import annotations

import logging
from typing import Any

from geodetic_engine.projdb.records import ObjectKey
from geodetic_engine.projdb.schema import OBJECT_TABLE_NAME

logger = logging.getLogger(__name__)

# proj.db's alias_name.table_name CHECK constraint does not accept every table
# the builder writes; coordinate systems and axes have no aliases.
ALIASABLE_TABLES = frozenset(
    {
        "unit_of_measure",
        "celestial_body",
        "ellipsoid",
        "extent",
        "prime_meridian",
        "geodetic_datum",
        "vertical_datum",
        "engineering_datum",
        "geodetic_crs",
        "projected_crs",
        "vertical_crs",
        "compound_crs",
        "engineering_crs",
        "conversion_table",
        "grid_transformation",
        "helmert_transformation_table",
        "other_transformation",
        "concatenated_operation",
    }
)

# Naming systems value meaning "import aliases from every naming system".
ALL_NAMING_SYSTEMS = "*"

# proj.db requires an alias of at least two characters.
_MINIMUM_LENGTH = 2


class AliasCollector:
    """Collects ``alias_name`` rows for imported objects.

    Only aliases belonging to the configured naming systems are kept, so a
    build does not import every other organisation's naming of an object.
    Configuring ``naming_systems`` as ``["*"]`` keeps all of them, which is what
    a source that curates several naming systems for its own objects wants.

    Example:
        >>> collector = AliasCollector(frozenset({"Example"}))
        >>> key = ObjectKey("geodetic_datum", "Example", "1000")
        >>> collector.add(key, alias="Example datum", source="Example")
        True
        >>> collector.rows[0]["alt_name"]
        'Example datum'
    """

    def __init__(self, naming_systems: frozenset[str]) -> None:
        self._all = ALL_NAMING_SYSTEMS in naming_systems
        self._wanted = {name.casefold() for name in naming_systems}
        self.rows: list[dict[str, Any]] = []
        self._seen: set[tuple[str, str, str, str]] = set()

    def add(self, key: ObjectKey, *, alias: str | None, source: str) -> bool:
        """Record one alias of one object.

        Args:
            key: Identity of the object the alias belongs to.
            alias: The alternative name. Ignored when absent or shorter than
                the two characters proj.db requires.
            source: Naming system the alias comes from.

        Returns:
            Whether a row was added.
        """
        if key.table not in ALIASABLE_TABLES:
            return False
        if not self._all and source.casefold() not in self._wanted:
            return False
        alias = (alias or "").strip()
        if len(alias) < _MINIMUM_LENGTH:
            return False

        table_name = OBJECT_TABLE_NAME[key.table]
        identity = (table_name, key.auth_name, key.code, alias)
        if identity in self._seen:
            return False
        self._seen.add(identity)
        self.rows.append(
            {
                "table_name": table_name,
                "auth_name": key.auth_name,
                "code": key.code,
                "alt_name": alias,
                "source": source or None,
            }
        )
        return True
