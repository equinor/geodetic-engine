"""Which database answers, and which authorities and versions it holds."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

import pyproj
from pyproj.database import get_database_metadata

from geodetic_engine._referencing.database import database_fingerprints
from geodetic_engine.catalog import _registry


@dataclass(frozen=True, slots=True)
class Authority:
    """An authority whose objects the active database holds.

    Attributes:
        name: The authority as the database spells it, for example ``"EPSG"``.
        version: The version of its dataset the database holds, for example
            ``"v12.013"``, or None when the database states none.
        date: The date of that dataset, or None.
    """

    name: str
    version: str | None
    date: str | None

    def __str__(self) -> str:
        return self.name if self.version is None else f"{self.name} {self.version}"


@dataclass(frozen=True, slots=True)
class DatabaseInfo:
    """The ``proj.db`` that answers listings, identified for provenance.

    Attributes:
        path: The database listings read: the first ``proj.db`` on PROJ's
            search path, the one PROJ resolves codes against.
        proj_version: The PROJ release this package runs.
        metadata: The database's own ``metadata`` table, for example
            ``{"EPSG.VERSION": "v12.013", ...}``.
        fingerprints: SHA-256 of every ``proj.db`` on PROJ's search path, as
            :attr:`~geodetic_engine.geodesy.TransformationResult.database_fingerprints`
            records them, so a listing can be tied to the transformations made
            against the same database.
    """

    path: str
    proj_version: str
    metadata: Mapping[str, str] = field(repr=False)
    fingerprints: tuple[tuple[str, str], ...] = field(repr=False)

    @property
    def epsg_version(self) -> str | None:
        """The EPSG dataset version, for example ``"v12.013"``."""
        return self.metadata.get("EPSG.VERSION")

    def to_json_dict(self) -> dict[str, Any]:
        """The database as a listing's JSON records it."""
        return {
            "path": self.path,
            "proj_version": self.proj_version,
            "metadata": dict(self.metadata),
            "fingerprints": [
                {"path": path, "sha256": digest} for path, digest in self.fingerprints
            ],
        }


def authorities() -> tuple[Authority, ...]:
    """Every authority the active database holds objects of.

    Each with the version and date of its dataset, where the database states
    them. Any ``authority`` filter must name one of these.

    Returns:
        The authorities, in the order PROJ lists them.

    Example:
        >>> from geodetic_engine import catalog
        >>> "EPSG" in [authority.name for authority in catalog.authorities()]
        True
    """
    databases = _registry.identity()
    return tuple(
        Authority(
            name=name,
            version=get_database_metadata(f"{name}.VERSION"),
            date=get_database_metadata(f"{name}.DATE"),
        )
        for name in _registry.authorities(databases)
    )


def database() -> DatabaseInfo:
    """The ``proj.db`` that answers listings, with its versions and fingerprints.

    Raises:
        CatalogError: If there is no ``proj.db`` on PROJ's search path.

    Example:
        >>> from geodetic_engine import catalog
        >>> catalog.database().epsg_version  # doctest: +SKIP
        'v12.013'
    """
    databases = _registry.identity()
    path = _registry.database_path(databases)
    with _registry.reading(databases) as connection:
        metadata = {
            str(key): str(value)
            for key, value in connection.execute("SELECT key, value FROM metadata")
        }
    return DatabaseInfo(
        path=str(path),
        proj_version=pyproj.proj_version_str,
        metadata=metadata,
        fingerprints=database_fingerprints(databases),
    )
