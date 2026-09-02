"""Alternative names for geodetic objects.

An alias is how an organisation's own name for a CRS or datum is carried
alongside the authority's official name, so a caller can look the object up by
either. proj.db stores them in ``alias_name``.

Aliases are collected for every kind of object that proj.db accepts one for,
including datums, which the register exposes on a per-object ``/alias``
endpoint as well as inline on the detail representation.
"""

from __future__ import annotations

import logging
from typing import Any

from geodetic_engine.georepository.client import GeorepositoryClient
from geodetic_engine.projdb.schema import OBJECT_TABLE_NAME
from geodetic_engine.projdb.translate import ObjectKey, text

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
    a register that curates several naming systems for its own objects wants.

    Example:
        >>> collector = AliasCollector(client, frozenset({"Example"}))  # doctest: +SKIP
        >>> collector.collect(key, datum_detail)  # doctest: +SKIP
        >>> collector.rows  # doctest: +SKIP
        [{'table_name': 'geodetic_datum', 'alt_name': 'Example datum', ...}]
    """

    def __init__(
        self, client: GeorepositoryClient, naming_systems: frozenset[str]
    ) -> None:
        self._client = client
        self._all = ALL_NAMING_SYSTEMS in naming_systems
        self._wanted = {name.casefold() for name in naming_systems}
        self.rows: list[dict[str, Any]] = []
        self._seen: set[tuple[str, str, str, str]] = set()

    def collect(self, key: ObjectKey, obj: dict[str, Any]) -> int:
        """Record the aliases of one object.

        Args:
            key: Identity of the object the aliases belong to.
            obj: The object's detail representation.

        Returns:
            The number of alias rows added.
        """
        if key.table not in ALIASABLE_TABLES:
            return 0
        table_name = OBJECT_TABLE_NAME[key.table]
        added = 0
        for record in self._client.aliases(obj):
            row = self._row(key, table_name, record)
            if row is None:
                continue
            identity = (table_name, key.auth_name, key.code, row["alt_name"])
            if identity in self._seen:
                continue
            self._seen.add(identity)
            self.rows.append(row)
            added += 1
        return added

    def _row(
        self, key: ObjectKey, table_name: str, record: dict[str, Any]
    ) -> dict[str, Any] | None:
        naming_system = str((record.get("NamingSystem") or {}).get("Name") or "")
        if not self._all and naming_system.casefold() not in self._wanted:
            return None
        alias = text(record, "Alias")
        if not alias or len(alias) < _MINIMUM_LENGTH:
            return None
        return {
            "table_name": table_name,
            "auth_name": key.auth_name,
            "code": key.code,
            "alt_name": alias,
            "source": naming_system or None,
        }
